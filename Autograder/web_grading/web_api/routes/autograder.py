"""
Autograding endpoints for AI-assisted grading.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import logging
import asyncio

from ..database import get_db_connection
from ..services.autograder import AutograderService
from .. import sse

router = APIRouter()
log = logging.getLogger(__name__)


class ExtractQuestionRequest(BaseModel):
    """Request to extract question text from a problem"""
    problem_number: int


class ExtractQuestionResponse(BaseModel):
    """Response with extracted question text"""
    problem_number: int
    question_text: str
    message: str


class AutogradeRequest(BaseModel):
    """Request to autograde a problem"""
    problem_number: int
    question_text: str  # User-verified question text
    max_points: float  # Maximum points for this problem


class AutogradeResponse(BaseModel):
    """Response when autograding starts"""
    status: str
    problem_number: int
    message: str


@router.get("/{session_id}/autograde-stream")
async def autograde_progress_stream(session_id: int):
    """SSE stream for autograding progress"""
    stream_id = sse.make_stream_id("autograde", session_id)

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


@router.post("/{session_id}/extract-question", response_model=ExtractQuestionResponse)
async def extract_question(session_id: int, request: ExtractQuestionRequest):
    """Extract question text from a problem image"""

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Get a sample problem image for this problem number
        cursor.execute("""
            SELECT image_data
            FROM problems
            WHERE session_id = ? AND problem_number = ?
            LIMIT 1
        """, (session_id, request.problem_number))

        problem = cursor.fetchone()
        if not problem:
            raise HTTPException(
                status_code=404,
                detail=f"No problems found for problem number {request.problem_number}"
            )

    try:
        # Extract question text
        autograder = AutograderService()
        question_text = autograder.get_or_extract_question(
            session_id,
            request.problem_number,
            problem["image_data"]
        )

        return ExtractQuestionResponse(
            problem_number=request.problem_number,
            question_text=question_text,
            message="Question text extracted successfully"
        )

    except Exception as e:
        log.error(f"Failed to extract question: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to extract question: {str(e)}")


@router.post("/{session_id}/autograde", response_model=AutogradeResponse)
async def start_autograde(session_id: int, request: AutogradeRequest, background_tasks: BackgroundTasks):
    """Start autograding process for a problem"""

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Count ungraded problems
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM problems
            WHERE session_id = ? AND problem_number = ? AND graded = 0 AND is_blank = 0
        """, (session_id, request.problem_number))

        ungraded_count = cursor.fetchone()["count"]
        if ungraded_count == 0:
            raise HTTPException(
                status_code=400,
                detail=f"No ungraded problems found for problem number {request.problem_number}"
            )

        # Update question_text and max_points in metadata
        cursor.execute("""
            INSERT INTO problem_metadata (session_id, problem_number, question_text, max_points)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, problem_number)
            DO UPDATE SET
                question_text = excluded.question_text,
                max_points = excluded.max_points
        """, (session_id, request.problem_number, request.question_text, request.max_points))

    # Create SSE stream for progress updates
    stream_id = sse.make_stream_id("autograde", session_id)
    sse.create_stream(stream_id)

    # Start background autograding
    background_tasks.add_task(
        run_autograding,
        session_id,
        request.problem_number,
        request.max_points,
        stream_id
    )

    return AutogradeResponse(
        status="started",
        problem_number=request.problem_number,
        message=f"Autograding started for {ungraded_count} problems"
    )


async def run_autograding(session_id: int, problem_number: int, max_points: float, stream_id: str):
    """Background task to autograde problems with SSE progress updates"""
    try:
        log.info(f"Starting autograding for session {session_id}, problem {problem_number}")

        # Send start event
        await sse.send_event(stream_id, "start", {
            "message": f"Starting autograding for problem {problem_number}..."
        })

        # Get event loop reference
        loop = asyncio.get_event_loop()

        # Create autograder service
        autograder = AutograderService()

        # Progress callback for SSE updates
        def update_progress(current, total, message):
            progress_percent = min(100, int((current / total) * 100)) if total > 0 else 0

            try:
                asyncio.run_coroutine_threadsafe(
                    sse.send_event(stream_id, "progress", {
                        "current": current,
                        "total": total,
                        "progress": progress_percent,
                        "message": message
                    }),
                    loop
                )
            except Exception as e:
                log.error(f"Failed to send SSE event: {e}")

        # Run autograding in thread executor
        result = await loop.run_in_executor(
            None,
            lambda: autograder.autograde_problem(
                session_id,
                problem_number,
                max_points=max_points,
                progress_callback=update_progress
            )
        )

        log.info(f"Autograding complete for session {session_id}, problem {problem_number}: {result}")

        # Send completion event
        await sse.send_event(stream_id, "complete", {
            "graded": result["graded"],
            "total": result["total"],
            "message": result["message"]
        })

    except Exception as e:
        log.error(f"Autograding failed for session {session_id}, problem {problem_number}: {e}", exc_info=True)

        # Send error event
        await sse.send_event(stream_id, "error", {
            "error": str(e),
            "message": f"Autograding failed: {str(e)}"
        })
