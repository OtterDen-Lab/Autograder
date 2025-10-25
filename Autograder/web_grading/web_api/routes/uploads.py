"""
File upload and processing endpoints.
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import List, Dict
from pydantic import BaseModel
import tempfile
import zipfile
import hashlib
import logging
from pathlib import Path

from ..models import UploadResponse
from ..database import get_db_connection
from .. import sse

router = APIRouter()
log = logging.getLogger(__name__)


class SplitPointsSubmission(BaseModel):
    """Model for manual split points submission"""
    split_points: Dict[str, List[int]]
    skip_first_region: bool = True  # Default to skipping first region (header/title)
    last_page_blank: bool = False  # Default to not skipping last page


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


@router.get("/{session_id}/upload-stream")
async def upload_progress_stream(session_id: int):
    """SSE stream for upload/processing progress"""
    stream_id = sse.make_stream_id("upload", session_id)

    # Create stream if it doesn't exist
    if not sse.get_stream(stream_id):
        sse.create_stream(stream_id)

    return StreamingResponse(
        sse.event_generator(stream_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.post("/{session_id}/upload", response_model=UploadResponse)
async def upload_exams(
    session_id: int,
    files: List[UploadFile] = File(...)
):
    """
    Upload exam PDFs or a zip file containing exams.
    Returns composites for manual alignment before processing.
    """
    from ..services.manual_alignment import ManualAlignmentService

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
    filename_counter = {}  # Track filename usage to handle duplicates

    for file in files:
        # Handle duplicate filenames by appending a counter
        # This can happen when dragging folders with same filenames in different subdirectories
        base_filename = file.filename
        if base_filename in filename_counter:
            filename_counter[base_filename] += 1
            # Insert counter before extension: "file.pdf" -> "file_1.pdf"
            stem = Path(base_filename).stem
            suffix = Path(base_filename).suffix
            unique_filename = f"{stem}_{filename_counter[base_filename]}{suffix}"
        else:
            filename_counter[base_filename] = 0
            unique_filename = base_filename

        file_path = temp_dir / unique_filename
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Compute hash for duplicate detection
        file_hash = compute_file_hash(file_path)
        file_metadata[file_path] = {
            "hash": file_hash,
            "original_filename": base_filename  # Store original name for display
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

    # Store file paths and metadata in session for later processing
    # Append to existing uploads if any (support multiple upload batches)
    import json

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get existing session data
        cursor.execute("SELECT metadata FROM grading_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        existing_data = json.loads(row["metadata"]) if row and row["metadata"] else None

        if existing_data and "file_paths" in existing_data:
            # Append to existing files
            log.info(f"Appending {len(saved_files)} files to existing {len(existing_data['file_paths'])} files")

            existing_files = [Path(p) for p in existing_data["file_paths"]]
            existing_metadata = {Path(k): v for k, v in existing_data["file_metadata"].items()}

            # Combine with new files (avoiding duplicates by hash)
            existing_hashes = {meta["hash"] for meta in existing_metadata.values()}
            new_files_added = 0

            for new_file in saved_files:
                new_hash = file_metadata[new_file]["hash"]
                if new_hash not in existing_hashes:
                    existing_files.append(new_file)
                    existing_metadata[new_file] = file_metadata[new_file]
                    new_files_added += 1
                else:
                    log.info(f"Skipping duplicate file: {new_file.name}")

            log.info(f"Added {new_files_added} new files (skipped {len(saved_files) - new_files_added} duplicates)")

            # Use the first temp_dir or create new one
            temp_dir_to_use = existing_data.get("temp_dir", str(temp_dir))

            session_data = {
                "temp_dir": temp_dir_to_use,
                "file_paths": [str(f) for f in existing_files],
                "file_metadata": {str(k): v for k, v in existing_metadata.items()}
            }

            total_files = len(existing_files)
        else:
            # First upload for this session
            log.info(f"First upload: {len(saved_files)} files")
            session_data = {
                "temp_dir": str(temp_dir),
                "file_paths": [str(f) for f in saved_files],
                "file_metadata": {str(k): v for k, v in file_metadata.items()}
            }
            total_files = len(saved_files)

        cursor.execute("""
            UPDATE grading_sessions
            SET status = 'awaiting_alignment',
                total_exams = ?,
                metadata = ?,
                processing_message = 'Uploaded. Please align split points.',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (total_files, json.dumps(session_data), session_id))

    # Generate composite images using ALL files (existing + new)
    all_file_paths = [Path(p) for p in session_data["file_paths"]]
    alignment_service = ManualAlignmentService()
    composites, composite_dimensions = alignment_service.create_composite_images(all_file_paths)

    # Convert composite dimensions to dict with string keys for JSON serialization
    page_dimensions = {}
    for page_num, (width, height) in composite_dimensions.items():
        page_dimensions[page_num] = {
            "width": width,
            "height": height
        }

    # Store composite dimensions in session metadata for later use during processing
    session_data["composite_dimensions"] = {str(k): list(v) for k, v in composite_dimensions.items()}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE grading_sessions
            SET metadata = ?
            WHERE id = ?
        """, (json.dumps(session_data), session_id))

    return {
        "session_id": session_id,
        "files_uploaded": len(saved_files),
        "status": "awaiting_alignment",
        "message": f"Uploaded {len(saved_files)} exam(s). Total: {total_files} exam(s). Please set split points.",
        "composites": composites,
        "page_dimensions": page_dimensions,
        "num_exams": total_files
    }


@router.post("/{session_id}/submit-alignment")
async def submit_alignment(
    session_id: int,
    background_tasks: BackgroundTasks,
    submission: SplitPointsSubmission
):
    """
    Submit manual split points and start processing exams.

    Args:
        session_id: Session ID
        submission: Model containing split_points dict mapping page_number (as string) -> list of y-positions
    """
    import json

    # Retrieve stored file paths from session metadata
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT metadata FROM grading_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()

        if not row or not row["metadata"]:
            raise HTTPException(status_code=404, detail="Session not found or no files uploaded")

        session_data = json.loads(row["metadata"])

    # Reconstruct file paths and metadata
    file_paths = [Path(p) for p in session_data["file_paths"]]
    file_metadata = {Path(k): v for k, v in session_data["file_metadata"].items()}

    # Convert split_points from absolute pixels to percentages of page height
    # This makes them resolution-independent
    composite_dimensions = session_data.get("composite_dimensions", {})
    manual_split_points = {}

    for page_str, y_positions in submission.split_points.items():
        page_num = int(page_str)

        # Get composite page height for this page
        if str(page_num) in composite_dimensions:
            page_height = composite_dimensions[str(page_num)][1]  # [width, height]

            # Convert each y-position from pixels to percentage
            percentages = [y_pos / page_height for y_pos in y_positions]
            manual_split_points[page_num] = percentages
        else:
            # Fallback: if no composite dimensions, pass through as-is
            log.warning(f"No composite dimensions for page {page_num}, using absolute coordinates")
            manual_split_points[page_num] = y_positions

    # Create SSE stream for progress updates
    stream_id = sse.make_stream_id("upload", session_id)
    sse.create_stream(stream_id)

    # Start background processing with manual split points
    background_tasks.add_task(
        process_exam_files,
        session_id,
        file_paths,
        file_metadata,
        stream_id,
        manual_split_points,  # Pass manual splits
        submission.skip_first_region,  # Pass skip_first_region flag
        submission.last_page_blank  # Pass last_page_blank flag
    )

    # Update session status
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE grading_sessions
            SET status = 'preprocessing',
                processed_exams = 0,
                matched_exams = 0,
                processing_message = 'Processing with manual split points...',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (session_id,))

    return {
        "session_id": session_id,
        "status": "processing",
        "message": f"Processing {len(file_paths)} exam(s) with manual alignment"
    }


async def process_exam_files(
    session_id: int,
    file_paths: List[Path],
    file_metadata: Dict[Path, Dict],
    stream_id: str,
    manual_split_points: Dict[int, List[int]] = None,
    skip_first_region: bool = True,
    last_page_blank: bool = False
):
    """
    Background task to process uploaded exam files.

    Args:
        session_id: Session ID to process for
        file_paths: List of PDF file paths
        file_metadata: Dict mapping file_path -> {hash, original_filename}
        stream_id: SSE stream ID for progress updates
        manual_split_points: Manual split points (optional)
        skip_first_region: Skip first region when splitting (default True, for header/title)
        last_page_blank: Skip last page when splitting (default False)
    """
    import logging
    import json
    import asyncio
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

        # Get event loop reference for sending SSE events from thread
        main_loop = asyncio.get_event_loop()

        # Step-based progress tracking (each exam has ~5 steps: extract, match, split, etc.)
        # Estimate total steps based on number of files
        estimated_steps_per_exam = 5
        total_steps = len(file_paths) * estimated_steps_per_exam
        current_step = {'count': 0}  # Use dict so it's mutable in closure

        # Progress callback to update database and send SSE events (with offset)
        def update_progress(processed, matched, message):
            total = base_total + len(file_paths)
            processed_count = base_processed + processed
            matched_count = base_matched + matched

            # Increment step counter
            current_step['count'] += 1

            # Update database
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
                """, (total, processed_count, matched_count, message, session_id))

            # Calculate progress based on steps completed
            progress_percent = min(100, int((current_step['count'] / total_steps) * 100))

            # Send SSE progress event from thread to event loop
            try:
                asyncio.run_coroutine_threadsafe(
                    sse.send_event(stream_id, "progress", {
                        "total": total,
                        "processed": processed_count,
                        "matched": matched_count,
                        "progress": progress_percent,
                        "current_step": current_step['count'],
                        "total_steps": total_steps,
                        "message": message
                    }),
                    main_loop
                )
            except Exception as e:
                log.error(f"Failed to send SSE event: {e}")

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

        # Process exams in thread executor so event loop can send SSE events
        processor = ExamProcessor()
        loop = asyncio.get_event_loop()
        matched, unmatched = await loop.run_in_executor(
            None,  # Use default thread pool
            lambda: processor.process_exams(
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
                extract_max_points_enabled=False,  # Disabled - use manual entry via UI
                manual_split_points=manual_split_points,  # Use manual alignment (now percentage-based)
                skip_first_region=skip_first_region,  # Skip first region (header/title)
                last_page_blank=last_page_blank  # Skip last page if blank
            )
        )

        # Store in database
        with get_db_connection() as conn:
            cursor = conn.cursor()

            all_submissions = matched + unmatched

            for submission in all_submissions:
                # Insert submission (with PDF data at end for easier manual editing)
                cursor.execute("""
                    INSERT INTO submissions
                    (session_id, document_id, approximate_name, student_name,
                     canvas_user_id, page_mappings, file_hash, original_filename,
                     name_image_data, exam_pdf_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    submission["document_id"],
                    submission.get("approximate_name"),
                    submission["student_name"],
                    submission["canvas_user_id"],
                    json.dumps(submission["page_mappings"]),
                    submission.get("file_hash"),
                    submission.get("original_filename"),
                    submission.get("name_image_data"),  # Large base64 data at end
                    submission.get("pdf_data")  # Large base64 PDF data at end
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

                    # Prepare region_coords JSON if metadata is available
                    region_coords = None
                    if (problem.get("page_number") is not None and
                        problem.get("region_y_start") is not None and
                        problem.get("region_y_end") is not None):
                        coords_dict = {
                            "page_number": problem["page_number"],
                            "region_y_start": problem["region_y_start"],
                            "region_y_end": problem["region_y_end"],
                            "region_height": problem.get("region_height")
                        }
                        # Add cross-page fields if present
                        if problem.get("end_page_number") is not None:
                            coords_dict["end_page_number"] = problem["end_page_number"]
                            coords_dict["end_region_y"] = problem["end_region_y"]
                        region_coords = json.dumps(coords_dict)

                    # Insert problem with region metadata and QR encrypted data if available
                    cursor.execute("""
                        INSERT INTO problems
                        (session_id, submission_id, problem_number, image_data, graded,
                         is_blank, blank_confidence, blank_method, blank_reasoning, max_points,
                         region_coords, qr_encrypted_data)
                        VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        session_id,
                        submission_id,
                        problem_number,
                        problem.get("image_base64"),  # May be None for new PDF-based storage
                        1 if problem.get("is_blank", False) else 0,
                        problem.get("blank_confidence", 0.0),
                        problem.get("blank_method"),
                        problem.get("blank_reasoning"),
                        max_points,
                        region_coords,  # JSON with page_number, region_y_start, region_y_end, region_height
                        problem.get("qr_encrypted_data")  # Encrypted QR data
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

        # Send completion event
        await sse.send_event(stream_id, "complete", {
            "total": len(matched) + len(unmatched),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "message": f"Processing complete: {len(matched)} matched, {len(unmatched)} unmatched"
        })

    except Exception as e:
        log.error(f"Error processing exams: {e}", exc_info=True)

        # Send error event
        await sse.send_event(stream_id, "error", {
            "error": str(e),
            "message": f"Processing failed: {str(e)}"
        })

        # Update session to error state
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE grading_sessions
                SET status = 'preprocessing', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (session_id,))
