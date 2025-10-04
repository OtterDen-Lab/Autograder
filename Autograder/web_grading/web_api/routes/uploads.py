"""
File upload and processing endpoints.
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from typing import List, Dict
import tempfile
import zipfile
import hashlib
from pathlib import Path

from ..models import UploadResponse
from ..database import get_db_connection

router = APIRouter()


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


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

    # Save uploaded files temporarily and compute hashes
    temp_dir = Path(tempfile.mkdtemp())
    saved_files = []
    file_metadata = {}  # Map: file_path -> {hash, original_filename}

    for file in files:
        file_path = temp_dir / file.filename
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Compute hash for duplicate detection
        file_hash = compute_file_hash(file_path)
        file_metadata[file_path] = {
            "hash": file_hash,
            "original_filename": file.filename
        }

        saved_files.append(file_path)

    # If it's a zip file, extract it
    if len(saved_files) == 1 and saved_files[0].suffix == ".zip":
        zip_path = saved_files[0]
        extract_dir = temp_dir / "extracted"
        extract_dir.mkdir()

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        # Find all PDFs in extracted directory and compute their hashes
        saved_files = list(extract_dir.rglob("*.pdf"))
        file_metadata = {}
        for pdf_path in saved_files:
            file_hash = compute_file_hash(pdf_path)
            file_metadata[pdf_path] = {
                "hash": file_hash,
                "original_filename": pdf_path.name
            }

    # Start background processing
    background_tasks.add_task(process_exam_files, session_id, saved_files, file_metadata)

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


async def process_exam_files(session_id: int, file_paths: List[Path], file_metadata: Dict[Path, Dict]):
    """
    Background task to process uploaded exam files.

    Args:
        session_id: Session ID to process for
        file_paths: List of PDF file paths
        file_metadata: Dict mapping file_path -> {hash, original_filename}
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

        # Get students who already have submissions in this session
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT canvas_user_id
                FROM submissions
                WHERE session_id = ? AND canvas_user_id IS NOT NULL
            """, (session_id,))
            existing_user_ids = set(row[0] for row in cursor.fetchall())

        # Convert to simple dicts for processor, excluding students who already have submissions
        canvas_students = [
            {"name": s.name, "user_id": s.user_id}
            for s in students
            if s.user_id not in existing_user_ids
        ]

        log.info(f"Found {len(students)} total students, {len(existing_user_ids)} already have submissions, {len(canvas_students)} available for matching")

        # Check for duplicate files (same hash already processed)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT file_hash, original_filename
                FROM submissions
                WHERE session_id = ? AND file_hash IS NOT NULL
            """, (session_id,))
            existing_hashes = {row[0]: row[1] for row in cursor.fetchall()}

        # Filter out duplicate files
        new_file_paths = []
        duplicate_files = []
        for file_path in file_paths:
            file_hash = file_metadata[file_path]["hash"]
            if file_hash in existing_hashes:
                log.info(f"Skipping duplicate file: {file_path.name} (hash={file_hash[:8]}..., already processed as {existing_hashes[file_hash]})")
                duplicate_files.append(file_path.name)
            else:
                new_file_paths.append(file_path)

        if duplicate_files:
            log.info(f"Skipped {len(duplicate_files)} duplicate file(s): {', '.join(duplicate_files)}")

        if not new_file_paths:
            log.info("No new files to process (all were duplicates)")
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE grading_sessions
                    SET status = 'ready',
                        processing_message = 'All uploaded files were duplicates - no new exams added',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (session_id,))
            return

        file_paths = new_file_paths
        log.info(f"Processing {len(file_paths)} new file(s) after duplicate detection")

        # Get the highest existing document_id to avoid conflicts
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(document_id) FROM submissions WHERE session_id = ?
            """, (session_id,))
            max_doc_id = cursor.fetchone()[0]
            start_document_id = (max_doc_id + 1) if max_doc_id is not None else 0

        log.info(f"Starting document_id offset: {start_document_id}")

        # Get current totals for progress tracking
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT total_exams, processed_exams, matched_exams
                FROM grading_sessions
                WHERE id = ?
            """, (session_id,))
            row = cursor.fetchone()
            base_total = row[0] or 0
            base_processed = row[1] or 0
            base_matched = row[2] or 0

        # Progress callback to update database (with offset)
        def update_progress(processed, matched, message):
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE grading_sessions
                    SET total_exams = ?,
                        processed_exams = ?,
                        matched_exams = ?,
                        processing_message = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (base_total + len(file_paths), base_processed + processed, base_matched + matched, message, session_id))

        # Load existing max_points metadata to avoid re-extracting
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT problem_number, max_points
                FROM problem_metadata
                WHERE session_id = ?
            """, (session_id,))
            problem_max_points = {row[0]: row[1] for row in cursor.fetchall()}

        log.info(f"Loaded {len(problem_max_points)} existing max_points values from metadata")

        # Process exams
        processor = ExamProcessor()
        matched, unmatched = processor.process_exams(
            input_files=file_paths,
            canvas_students=canvas_students,
            page_ranges=None,  # TODO: Get from session config
            use_ai=True,
            detect_blank=True,  # Enable blank detection
            blank_confidence_threshold=0.8,
            use_ai_for_borderline=False,  # Only use heuristics to save cost
            progress_callback=update_progress,
            document_id_offset=start_document_id,
            file_metadata=file_metadata,
            problem_max_points=problem_max_points,
            extract_max_points_enabled=False  # Disabled - use manual entry via UI
        )

        # Store in database
        with get_db_connection() as conn:
            cursor = conn.cursor()

            all_submissions = matched + unmatched

            for submission in all_submissions:
                # Insert submission
                cursor.execute("""
                    INSERT INTO submissions
                    (session_id, document_id, approximate_name, name_image_data, student_name, canvas_user_id, page_mappings, file_hash, original_filename)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    submission["document_id"],
                    submission.get("approximate_name"),
                    submission.get("name_image_data"),
                    submission["student_name"],
                    submission["canvas_user_id"],
                    json.dumps(submission["page_mappings"]),
                    submission.get("file_hash"),
                    submission.get("original_filename")
                ))

                submission_id = cursor.lastrowid

                # Insert problems and update metadata
                for problem in submission["problems"]:
                    problem_number = problem["problem_number"]

                    # Check if we have metadata for this problem number
                    cursor.execute("""
                        SELECT max_points FROM problem_metadata
                        WHERE session_id = ? AND problem_number = ?
                    """, (session_id, problem_number))

                    metadata_row = cursor.fetchone()
                    if metadata_row:
                        # Use stored max_points
                        max_points = metadata_row["max_points"]
                    else:
                        # Use extracted max_points (if any) and store it
                        max_points = problem.get("max_points")
                        if max_points is not None:
                            cursor.execute("""
                                INSERT INTO problem_metadata (session_id, problem_number, max_points)
                                VALUES (?, ?, ?)
                                ON CONFLICT(session_id, problem_number)
                                DO UPDATE SET max_points = excluded.max_points
                            """, (session_id, problem_number, max_points))

                    cursor.execute("""
                        INSERT INTO problems
                        (session_id, submission_id, problem_number, image_data, graded,
                         is_blank, blank_confidence, blank_method, blank_reasoning, max_points)
                        VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                    """, (
                        session_id,
                        submission_id,
                        problem_number,
                        problem["image_base64"],
                        1 if problem.get("is_blank", False) else 0,
                        problem.get("blank_confidence", 0.0),
                        problem.get("blank_method"),
                        problem.get("blank_reasoning"),
                        max_points
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
