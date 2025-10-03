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

    # Update session status
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE grading_sessions
            SET status = 'preprocessing', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (session_id,))

    return UploadResponse(
        session_id=session_id,
        files_uploaded=len(saved_files),
        status="processing",
        message=f"Processing {len(saved_files)} exam(s)"
    )


async def process_exam_files(session_id: int, file_paths: List[Path]):
    """
    Background task to process uploaded exam files.
    This will eventually call the exam processor service.
    """
    # TODO: Implement exam processing
    # 1. Extract student names
    # 2. Match to Canvas students
    # 3. Shuffle and redact pages
    # 4. Split into problems
    # 5. Store in database

    # Placeholder for now
    import time
    import logging

    log = logging.getLogger(__name__)
    log.info(f"Processing {len(file_paths)} files for session {session_id}")

    # Simulate processing
    time.sleep(2)

    # Update session status
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE grading_sessions
            SET status = 'ready', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (session_id,))

    log.info(f"Completed processing for session {session_id}")
