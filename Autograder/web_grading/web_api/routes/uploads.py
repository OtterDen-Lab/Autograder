"""
File upload and processing endpoints.
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from typing import List
import tempfile
import zipfile
from pathlib import Path

from ..models import UploadResponse
from ..database import get_db_connection

router = APIRouter()


@router.post("/{session_id}/upload", response_model=UploadResponse)
async def upload_exams(
    session_id: int,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...)
):
    """
    Upload exam PDFs or a zip file containing exams.
    Processing happens in background, status available via SSE endpoint.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

    # Save uploaded files temporarily
    temp_dir = Path(tempfile.mkdtemp())
    saved_files = []

    for file in files:
        file_path = temp_dir / file.filename
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        saved_files.append(file_path)

    # If it's a zip file, extract it
    if len(saved_files) == 1 and saved_files[0].suffix == ".zip":
        zip_path = saved_files[0]
        extract_dir = temp_dir / "extracted"
        extract_dir.mkdir()

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        # Find all PDFs in extracted directory
        saved_files = list(extract_dir.rglob("*.pdf"))

    # Start background processing
    background_tasks.add_task(process_exam_files, session_id, saved_files)

    # Update session status with initial count
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE grading_sessions
            SET status = 'preprocessing',
                total_exams = ?,
                processed_exams = 0,
                matched_exams = 0,
                processing_message = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (len(saved_files), f"Uploaded {len(saved_files)} exam(s), starting processing...", session_id))

    return UploadResponse(
        session_id=session_id,
        files_uploaded=len(saved_files),
        status="processing",
        message=f"Processing {len(saved_files)} exam(s)"
    )


async def process_exam_files(session_id: int, file_paths: List[Path]):
    """
    Background task to process uploaded exam files.
    """
    import logging
    import json
    from ..services.exam_processor import ExamProcessor
    from lms_interface.canvas_interface import CanvasInterface

    log = logging.getLogger(__name__)
    log.info(f"Processing {len(file_paths)} files for session {session_id}")

    try:
        # Get session info
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM grading_sessions WHERE id = ?", (session_id,))
            session = cursor.fetchone()

            if not session:
                log.error(f"Session {session_id} not found")
                return

            course_id = session["course_id"]
            assignment_id = session["assignment_id"]

        # Get Canvas students
        canvas_interface = CanvasInterface(prod=False)  # Use dev by default
        course = canvas_interface.get_course(course_id)
        assignment = course.get_assignment(assignment_id)
        students = assignment.get_students()

        # Convert to simple dicts for processor
        canvas_students = [
            {"name": s.name, "user_id": s.user_id}
            for s in students
        ]

        # Progress callback to update database
        def update_progress(processed, matched, message):
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE grading_sessions
                    SET processed_exams = ?,
                        matched_exams = ?,
                        processing_message = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (processed, matched, message, session_id))

        # Process exams
        processor = ExamProcessor()
        matched, unmatched = processor.process_exams(
            input_files=file_paths,
            canvas_students=canvas_students,
            page_ranges=None,  # TODO: Get from session config
            use_ai=True,
            progress_callback=update_progress
        )

        # Store in database
        with get_db_connection() as conn:
            cursor = conn.cursor()

            all_submissions = matched + unmatched

            for submission in all_submissions:
                # Insert submission
                cursor.execute("""
                    INSERT INTO submissions
                    (session_id, document_id, approximate_name, name_image_data, student_name, canvas_user_id, page_mappings)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    submission["document_id"],
                    submission.get("approximate_name"),
                    submission.get("name_image_data"),
                    submission["student_name"],
                    submission["canvas_user_id"],
                    json.dumps(submission["page_mappings"])
                ))

                submission_id = cursor.lastrowid

                # Insert problems
                for problem in submission["problems"]:
                    cursor.execute("""
                        INSERT INTO problems
                        (session_id, submission_id, problem_number, image_data, graded)
                        VALUES (?, ?, ?, ?, 0)
                    """, (
                        session_id,
                        submission_id,
                        problem["problem_number"],
                        problem["image_base64"]
                    ))

            # Update session status
            if unmatched:
                cursor.execute("""
                    UPDATE grading_sessions
                    SET status = 'name_matching_needed', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (session_id,))
            else:
                cursor.execute("""
                    UPDATE grading_sessions
                    SET status = 'ready', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (session_id,))

        log.info(f"Completed processing for session {session_id}: {len(matched)} matched, {len(unmatched)} unmatched")

    except Exception as e:
        log.error(f"Error processing exams: {e}", exc_info=True)
        # Update session to error state
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE grading_sessions
                SET status = 'preprocessing', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (session_id,))
