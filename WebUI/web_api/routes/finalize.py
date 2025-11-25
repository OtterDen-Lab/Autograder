"""
Finalization endpoints for completing grading and uploading to Canvas.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pathlib import Path
import tempfile
import shutil
import logging
import asyncio

from ..database import get_db_connection
from ..repositories import SessionRepository, ProblemRepository
from ..domain.common import SessionStatus
from ..services.finalizer import FinalizationService
from .. import sse

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/{session_id}/finalize-stream")
async def finalize_progress_stream(session_id: int):
  """SSE stream for finalization progress"""
  stream_id = sse.make_stream_id("finalize", session_id)

  # Create stream if it doesn't exist
  if not sse.get_stream(stream_id):
    sse.create_stream(stream_id)

  return StreamingResponse(sse.event_generator(stream_id),
                           media_type="text/event-stream",
                           headers={
                             "Cache-Control": "no-cache",
                             "Connection": "keep-alive",
                           })


@router.post("/{session_id}/finalize")
async def finalize_session(session_id: int, background_tasks: BackgroundTasks):
  """Start finalization process for a session"""
  session_repo = SessionRepository()
  problem_repo = ProblemRepository()

  # Verify session exists
  session = session_repo.get_by_id(session_id)
  if not session:
    raise HTTPException(status_code=404, detail="Session not found")

  # Check if all problems are graded
  ungraded_count = problem_repo.count_ungraded(session_id)
  if ungraded_count > 0:
    raise HTTPException(
      status_code=400,
      detail=f"Cannot finalize: {ungraded_count} problems still ungraded")

  # Update session status with initial progress message
  session_repo.update_status(session_id, SessionStatus.FINALIZING, "Starting finalization...")

  # Create SSE stream for progress updates
  stream_id = sse.make_stream_id("finalize", session_id)
  sse.create_stream(stream_id)

  # Start background finalization
  background_tasks.add_task(run_finalization, session_id, stream_id)

  return {
    "status": "started",
    "session_id": session_id,
    "message": "Finalization started in background"
  }


@router.get("/{session_id}/finalization-status")
async def get_finalization_status(session_id: int):
  """Get status of finalization process"""
  session_repo = SessionRepository()

  session = session_repo.get_by_id(session_id)
  if not session:
    raise HTTPException(status_code=404, detail="Session not found")

  return {
    "status": session.status.value,
    "message": session.processing_message
  }


async def run_finalization(session_id: int, stream_id: str):
  """Background task to finalize grading and upload to Canvas"""
  try:
    log.info(f"Starting finalization for session {session_id}")

    # Send start event
    await sse.send_event(stream_id, "start",
                         {"message": "Starting finalization..."})

    # Create temp directory for PDF processing
    with tempfile.TemporaryDirectory() as temp_dir:
      temp_path = Path(temp_dir)

      # Get event loop reference to pass to finalizer (for thread communication)
      loop = asyncio.get_event_loop()

      # Initialize finalizer with event loop reference
      finalizer = FinalizationService(session_id, temp_path, stream_id, loop)

      # Run finalization in thread executor so event loop can send SSE events
      await loop.run_in_executor(None, finalizer.finalize)

    # Update session to finalized
    session_repo = SessionRepository()
    session_repo.update_status(session_id, SessionStatus.FINALIZED, "Finalized and uploaded to Canvas")

    log.info(f"Finalization complete for session {session_id}")

    # Send completion event
    await sse.send_event(
      stream_id, "complete",
      {"message": "Finalization complete - all grades uploaded to Canvas"})

  except Exception as e:
    log.error(f"Finalization failed for session {session_id}: {e}",
              exc_info=True)

    # Send error event
    await sse.send_event(stream_id, "error", {
      "error": str(e),
      "message": f"Finalization failed: {str(e)}"
    })

    # Update session to error state
    session_repo = SessionRepository()
    session_repo.update_status(session_id, SessionStatus.ERROR, f"Finalization failed: {str(e)}")
