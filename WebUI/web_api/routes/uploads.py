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
from ..repositories import SessionRepository
from ..domain.common import SessionStatus
from .. import sse

router = APIRouter()
log = logging.getLogger(__name__)


class SplitPointsSubmission(BaseModel):
  """Model for manual split points submission"""
  split_points: Dict[str, List[int]]
  skip_first_region: bool = True  # Default to skipping first region (header/title)
  last_page_blank: bool = False  # Default to not skipping last page
  ai_provider: str = "anthropic"  # AI provider for name extraction (anthropic, openai, ollama)


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

  return StreamingResponse(sse.event_generator(stream_id),
                           media_type="text/event-stream",
                           headers={
                             "Cache-Control": "no-cache",
                             "Connection": "keep-alive",
                           })


@router.post("/{session_id}/upload", response_model=UploadResponse)
async def upload_exams(session_id: int, files: List[UploadFile] = File(...)):
  """
    Upload exam PDFs or a zip file containing exams.
    Returns composites for manual alignment before processing.
    """
  from ..services.manual_alignment import ManualAlignmentService

  # Verify session exists
  session_repo = SessionRepository()
  if not session_repo.exists(session_id):
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

  session_repo = SessionRepository()

  # Get existing session data
  existing_data = session_repo.get_metadata(session_id)

  # Check if we have existing split points from a previous upload
  has_existing_split_points = (existing_data
                               and "split_points" in existing_data
                               and existing_data["split_points"])

  if has_existing_split_points:
    log.info(f"Found existing split points - will auto-process new uploads")
  else:
    log.info(f"No existing split points found - will show alignment UI")

  if existing_data and "file_paths" in existing_data:
    # Append to existing files
    log.info(
      f"Appending {len(saved_files)} files to existing {len(existing_data['file_paths'])} files"
    )

    existing_files = [Path(p) for p in existing_data["file_paths"]]
    existing_metadata = {
      Path(k): v
      for k, v in existing_data["file_metadata"].items()
    }

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

    log.info(
      f"Added {new_files_added} new files (skipped {len(saved_files) - new_files_added} duplicates)"
    )

    # Use the first temp_dir or create new one
    temp_dir_to_use = existing_data.get("temp_dir", str(temp_dir))

    # Preserve existing split points and settings if they exist
    session_data = {
      "temp_dir": temp_dir_to_use,
      "file_paths": [str(f) for f in existing_files],
      "file_metadata": {
        str(k): v
        for k, v in existing_metadata.items()
      }
    }

    # Preserve split points and other settings from previous upload
    if has_existing_split_points:
      # Convert string keys back to integers (JSON serialization converts int keys to strings)
      raw_split_points = existing_data["split_points"]
      session_data["split_points"] = {
        int(k): v
        for k, v in raw_split_points.items()
      }
      session_data["skip_first_region"] = existing_data.get(
        "skip_first_region", True)
      session_data["last_page_blank"] = existing_data.get(
        "last_page_blank", False)
      session_data["ai_provider"] = existing_data.get(
        "ai_provider", "anthropic")
      session_data["composite_dimensions"] = existing_data.get(
        "composite_dimensions", {})
      log.info(
        f"Reusing existing split points from previous upload: {session_data['split_points']}"
      )

    total_files = len(existing_files)
  else:
    # First upload for this session
    log.info(f"First upload: {len(saved_files)} files")
    session_data = {
      "temp_dir": str(temp_dir),
      "file_paths": [str(f) for f in saved_files],
      "file_metadata": {
        str(k): v
        for k, v in file_metadata.items()
      }
    }
    total_files = len(saved_files)

  # Update session with file metadata and status
  session_repo.update_metadata(session_id, session_data)
  session_repo.update_status(session_id, SessionStatus.AWAITING_ALIGNMENT, "Uploaded. Please align split points.")

  # Also update total_exams count
  session = session_repo.get_by_id(session_id)
  session.total_exams = total_files
  session_repo.update(session)

  # If we already have split points from a previous upload, auto-submit and skip alignment UI
  if has_existing_split_points:
    log.info(f"Auto-processing new files with existing split points")

    # Create SSE stream for progress
    stream_id = sse.make_stream_id("upload", session_id)
    sse.create_stream(stream_id)

    # Get file paths and metadata
    file_paths = [Path(p) for p in session_data["file_paths"]]
    file_metadata_dict = {
      Path(k): v
      for k, v in session_data["file_metadata"].items()
    }

    # Start background processing with existing split points
    from fastapi import BackgroundTasks
    background_tasks = BackgroundTasks()
    background_tasks.add_task(process_exam_files, session_id, file_paths,
                              file_metadata_dict, stream_id,
                              session_data["split_points"],
                              session_data.get("skip_first_region", True),
                              session_data.get("last_page_blank", False),
                              session_data.get("ai_provider", "anthropic"))

    # Execute background tasks (they run after response is sent)
    import asyncio
    asyncio.create_task(background_tasks())

    return {
      "session_id": session_id,
      "files_uploaded": len(saved_files),
      "status": "processing",
      "message":
      f"Uploaded {len(saved_files)} exam(s). Auto-processing with existing split points.",
      "num_exams": total_files,
      "auto_processed": True
    }

  # Otherwise, generate composite images for alignment UI (first upload only)
  all_file_paths = [Path(p) for p in session_data["file_paths"]]
  alignment_service = ManualAlignmentService()
  composites, composite_dimensions = alignment_service.create_composite_images(
    all_file_paths)

  # Convert composite dimensions to dict with string keys for JSON serialization
  page_dimensions = {}
  for page_num, (width, height) in composite_dimensions.items():
    page_dimensions[page_num] = {"width": width, "height": height}

  # Store composite dimensions in session metadata for later use during processing
  session_data["composite_dimensions"] = {
    str(k): list(v)
    for k, v in composite_dimensions.items()
  }

  session_repo.update_metadata(session_id, session_data)

  return {
    "session_id": session_id,
    "files_uploaded": len(saved_files),
    "status": "awaiting_alignment",
    "message":
    f"Uploaded {len(saved_files)} exam(s). Total: {total_files} exam(s). Please set split points.",
    "composites": composites,
    "page_dimensions": page_dimensions,
    "num_exams": total_files,
    "auto_processed": False
  }


@router.post("/{session_id}/submit-alignment")
async def submit_alignment(session_id: int, background_tasks: BackgroundTasks,
                           submission: SplitPointsSubmission):
  """
    Submit manual split points and start processing exams.

    Args:
        session_id: Session ID
        submission: Model containing split_points dict mapping page_number (as string) -> list of y-positions
    """
  # Retrieve stored file paths from session metadata
  session_repo = SessionRepository()
  session_data = session_repo.get_metadata(session_id)

  if not session_data:
    raise HTTPException(status_code=404,
                        detail="Session not found or no files uploaded")

  # Reconstruct file paths and metadata
  file_paths = [Path(p) for p in session_data["file_paths"]]
  file_metadata = {
    Path(k): v
    for k, v in session_data["file_metadata"].items()
  }

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
      log.warning(
        f"No composite dimensions for page {page_num}, using absolute coordinates"
      )
      manual_split_points[page_num] = y_positions

  # Save split points and settings to session metadata for reuse in future uploads
  session_data["split_points"] = manual_split_points
  session_data["skip_first_region"] = submission.skip_first_region
  session_data["last_page_blank"] = submission.last_page_blank
  session_data["ai_provider"] = submission.ai_provider

  session_repo.update_metadata(session_id, session_data)

  log.info(
    f"Saved split points and settings to session metadata for future uploads")

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
    submission.last_page_blank,  # Pass last_page_blank flag
    submission.ai_provider  # Pass AI provider selection
  )

  # Update session status
  session = session_repo.get_by_id(session_id)
  session.status = SessionStatus.PREPROCESSING
  session.processed_exams = 0
  session.matched_exams = 0
  session.processing_message = 'Processing with manual split points...'
  session_repo.update(session)

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
  last_page_blank: bool = False,
  ai_provider: str = "anthropic"
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
        ai_provider: AI provider to use for name extraction (anthropic, openai, ollama)
    """
  import logging
  import asyncio
  from ..services.exam_processor import ExamProcessor
  from lms_interface.canvas_interface import CanvasInterface

  log = logging.getLogger(__name__)
  log.info(f"Processing {len(file_paths)} files for session {session_id}")

  try:
    # Get session info
    from ..repositories import SessionRepository, SubmissionRepository, ProblemMetadataRepository
    from ..domain.common import SessionStatus
    session_repo = SessionRepository()

    session = session_repo.get_by_id(session_id)
    if not session:
      log.error(f"Session {session_id} not found")
      return

    course_id = session.course_id
    assignment_id = session.assignment_id
    use_prod = session.use_prod_canvas

    # Get Canvas students
    canvas_interface = CanvasInterface(prod=use_prod)
    course = canvas_interface.get_course(course_id)
    assignment = course.get_assignment(assignment_id)
    students = assignment.get_students()

    # Get students who already have submissions in this session
    submission_repo = SubmissionRepository()
    existing_user_ids = submission_repo.get_existing_canvas_users(session_id)

    # Convert to simple dicts for processor, excluding students who already have submissions
    canvas_students = [{
      "name": s.name,
      "user_id": s.user_id
    } for s in students if s.user_id not in existing_user_ids]

    log.info(
      f"Found {len(students)} total students, {len(existing_user_ids)} already have submissions, {len(canvas_students)} available for matching"
    )

    # Check for duplicate files (same hash already processed)
    submission_repo = SubmissionRepository()
    existing_hashes = submission_repo.get_existing_hashes(session_id)

    # Filter out duplicate files
    new_file_paths = []
    duplicate_files = []
    for file_path in file_paths:
      file_hash = file_metadata[file_path]["hash"]
      if file_hash in existing_hashes:
        log.info(
          f"Skipping duplicate file: {file_path.name} (hash={file_hash[:8]}..., already processed as {existing_hashes[file_hash]})"
        )
        duplicate_files.append(file_path.name)
      else:
        new_file_paths.append(file_path)

    if duplicate_files:
      log.info(
        f"Skipped {len(duplicate_files)} duplicate file(s): {', '.join(duplicate_files)}"
      )

    if not new_file_paths:
      log.info("No new files to process (all were duplicates)")
      session_repo = SessionRepository()
      session_repo.update_status(
        session_id,
        SessionStatus.READY,
        'All uploaded files were duplicates - no new exams added'
      )
      return

    file_paths = new_file_paths
    log.info(f"Processing {len(file_paths)} new file(s) after duplicate detection")

    # Get the highest existing document_id to avoid conflicts
    start_document_id = submission_repo.get_max_document_id(session_id) + 1
    log.info(f"Starting document_id offset: {start_document_id}")

    # Get current totals for progress tracking
    session_repo = SessionRepository()
    session = session_repo.get_by_id(session_id)
    base_total = session.total_exams
    base_processed = session.processed_exams
    base_matched = session.matched_exams

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

      # Update database using repository
      progress_repo = SessionRepository()
      progress_session = progress_repo.get_by_id(session_id)
      progress_session.total_exams = total
      progress_session.processed_exams = processed_count
      progress_session.matched_exams = matched_count
      progress_session.processing_message = message
      progress_repo.update(progress_session)

      # Calculate progress based on steps completed
      progress_percent = min(100,
                             int((current_step['count'] / total_steps) * 100))

      # Send SSE progress event from thread to event loop
      try:
        asyncio.run_coroutine_threadsafe(
          sse.send_event(
            stream_id, "progress", {
              "total": total,
              "processed": processed_count,
              "matched": matched_count,
              "progress": progress_percent,
              "current_step": current_step['count'],
              "total_steps": total_steps,
              "message": message
            }), main_loop)
      except Exception as e:
        log.error(f"Failed to send SSE event: {e}")

    # Load existing max_points metadata to avoid re-extracting
    metadata_repo = ProblemMetadataRepository()
    problem_max_points = metadata_repo.get_all_max_points(session_id)

    log.info(
      f"Loaded {len(problem_max_points)} existing max_points values from metadata"
    )

    # Process exams in thread executor so event loop can send SSE events
    processor = ExamProcessor(ai_provider=ai_provider)
    loop = asyncio.get_event_loop()
    matched, unmatched = await loop.run_in_executor(
      None,  # Use default thread pool
      lambda: processor.process_exams(
        input_files=file_paths,
        canvas_students=canvas_students,
        page_ranges=None,  # TODO: Get from session config
        use_ai=True,
        detect_blank=True,  # Enabled with new band-based heuristic
        blank_confidence_threshold=0.8,
        use_ai_for_borderline=False,  # Only use heuristics to save cost
        progress_callback=update_progress,
        document_id_offset=start_document_id,
        file_metadata=file_metadata,
        problem_max_points=problem_max_points,
        extract_max_points_enabled=False,  # Disabled - use manual entry via UI
        manual_split_points=
        manual_split_points,  # Use manual alignment (now percentage-based)
        skip_first_region=
        skip_first_region,  # Skip first region (header/title)
        last_page_blank=last_page_blank  # Skip last page if blank
      ))

    # Store in database using repositories
    from ..repositories import with_transaction
    from ..domain.submission import Submission
    from ..domain.problem import Problem

    with with_transaction() as repos:
      all_submissions_data = matched + unmatched

      # Step 1: Convert submission dicts to domain objects
      submissions_to_create = []
      for sub_data in all_submissions_data:
        submission = Submission(
          id=0,  # Will be populated on create
          session_id=session_id,
          document_id=sub_data["document_id"],
          approximate_name=sub_data.get("approximate_name"),
          name_image_data=sub_data.get("name_image_data"),
          student_name=sub_data["student_name"],
          display_name=None,  # Not set during upload
          canvas_user_id=sub_data["canvas_user_id"],
          page_mappings=sub_data["page_mappings"],
          file_hash=sub_data.get("file_hash"),
          original_filename=sub_data.get("original_filename"),
          exam_pdf_data=sub_data.get("pdf_data")
        )
        # Store problems temporarily for later processing
        submission.problems = sub_data["problems"]
        submissions_to_create.append(submission)

      # Step 2: Bulk create submissions (single transaction)
      created_submissions = repos.submissions.bulk_create(submissions_to_create)

      # Step 3: Build problems list with correct submission_ids and handle metadata
      all_problems = []
      max_points_to_upsert = {}  # {problem_number: max_points}

      for i, created_sub in enumerate(created_submissions):
        for prob_data in submissions_to_create[i].problems:
          problem_number = prob_data["problem_number"]

          # Check if we have max_points from metadata
          existing_max = repos.metadata.get_max_points(session_id, problem_number)
          if existing_max is not None:
            max_points = existing_max
          else:
            # Use extracted max_points and queue for upsert
            max_points = prob_data.get("max_points")
            if max_points is not None:
              max_points_to_upsert[problem_number] = max_points

          # Prepare region_coords dict
          region_coords = None
          if (prob_data.get("page_number") is not None
              and prob_data.get("region_y_start") is not None
              and prob_data.get("region_y_end") is not None):
            region_coords = {
              "page_number": prob_data["page_number"],
              "region_y_start": prob_data["region_y_start"],
              "region_y_end": prob_data["region_y_end"],
              "region_height": prob_data.get("region_height")
            }
            # Add cross-page fields if present
            if prob_data.get("end_page_number") is not None:
              region_coords["end_page_number"] = prob_data["end_page_number"]
              region_coords["end_region_y"] = prob_data["end_region_y"]

          # Create problem domain object
          problem = Problem(
            id=0,
            session_id=session_id,
            submission_id=created_sub.id,  # Now has real ID from bulk_create
            problem_number=problem_number,
            graded=False,
            is_blank=prob_data.get("is_blank", False),
            blank_confidence=prob_data.get("blank_confidence", 0.0),
            blank_method=prob_data.get("blank_method"),
            blank_reasoning=prob_data.get("blank_reasoning"),
            max_points=max_points,
            region_coords=region_coords,
            qr_encrypted_data=prob_data.get("qr_encrypted_data")
          )
          all_problems.append(problem)

      # Step 4: Bulk create all problems
      repos.problems.bulk_create(all_problems)

      # Step 5: Upsert metadata for new max_points
      for problem_num, max_pts in max_points_to_upsert.items():
        repos.metadata.upsert_max_points(session_id, problem_num, max_pts)

      # Step 6: Update session status
      repos.sessions.update_status(
        session_id,
        SessionStatus.NAME_MATCHING_NEEDED
      )

    log.info(
      f"Completed processing for session {session_id}: {len(matched)} matched, {len(unmatched)} unmatched"
    )

    # Send completion event
    await sse.send_event(
      stream_id, "complete", {
        "total":
        len(matched) + len(unmatched),
        "matched":
        len(matched),
        "unmatched":
        len(unmatched),
        "message":
        f"Processing complete: {len(matched)} matched, {len(unmatched)} unmatched"
      })

  except Exception as e:
    log.error(f"Error processing exams: {e}", exc_info=True)

    # Send error event
    await sse.send_event(stream_id, "error", {
      "error": str(e),
      "message": f"Processing failed: {str(e)}"
    })

    # Update session to error state
    error_repo = SessionRepository()
    error_repo.update_status(session_id, SessionStatus.ERROR, f"Processing failed: {str(e)}")
