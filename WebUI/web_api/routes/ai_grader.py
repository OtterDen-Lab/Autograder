"""
AI grading endpoints for AI-assisted grading.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import logging
import asyncio

from ..repositories import SessionRepository, ProblemRepository, SubmissionRepository, ProblemMetadataRepository
from ..services.ai_grader import AIGraderService
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


class GenerateRubricRequest(BaseModel):
  """Request to generate a grading rubric"""
  problem_number: int
  question_text: str
  max_points: float
  num_examples: int = 3  # Number of manually graded examples to include


class GenerateRubricResponse(BaseModel):
  """Response with generated rubric"""
  problem_number: int
  rubric: str
  message: str


class SaveRubricRequest(BaseModel):
  """Request to save/update a rubric"""
  problem_number: int
  rubric: str


@router.get("/{session_id}/autograde-stream")
async def autograde_progress_stream(session_id: int):
  """SSE stream for autograding progress"""
  stream_id = sse.make_stream_id("autograde", session_id)

  # Create stream if it doesn't exist
  if not sse.get_stream(stream_id):
    sse.create_stream(stream_id)

  return StreamingResponse(sse.event_generator(stream_id),
                           media_type="text/event-stream",
                           headers={
                             "Cache-Control": "no-cache",
                             "Connection": "keep-alive",
                           })


@router.post("/{session_id}/extract-question",
             response_model=ExtractQuestionResponse)
async def extract_question(session_id: int, request: ExtractQuestionRequest):
  """Extract question text from a problem image"""

  session_repo = SessionRepository()
  problem_repo = ProblemRepository()
  submission_repo = SubmissionRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  # Get a sample problem for this problem number
  problem = problem_repo.get_sample_for_problem_number(
    session_id, request.problem_number)

  if not problem:
    raise HTTPException(
      status_code=404,
      detail=f"No problems found for problem number {request.problem_number}"
    )

  # Get image data - either directly or extract from PDF
  image_data = None
  if problem.image_data:
    # Legacy: image_data is stored
    image_data = problem.image_data
  elif problem.region_coords:
    # New: extract from PDF using region_coords
    import json
    import base64
    import fitz

    region_data = problem.region_coords

    # Get PDF data from submission
    submission = submission_repo.get_by_id(problem.submission_id)

    if submission and submission.exam_pdf_data:
      # Extract region from PDF
      pdf_bytes = base64.b64decode(submission.exam_pdf_data)
      pdf_document = fitz.open("pdf", pdf_bytes)
      page = pdf_document[region_data["page_number"]]

      region = fitz.Rect(0, region_data["region_y_start"], page.rect.width,
                         region_data["region_y_end"])

      # Extract region as new PDF page
      problem_pdf = fitz.open()
      problem_page = problem_pdf.new_page(width=region.width,
                                          height=region.height)
      problem_page.show_pdf_page(problem_page.rect,
                                 pdf_document,
                                 region_data["page_number"],
                                 clip=region)

      # Convert to PNG
      pix = problem_page.get_pixmap(dpi=150)
      img_bytes = pix.tobytes("png")
      image_data = base64.b64encode(img_bytes).decode("utf-8")

      # Cleanup
      problem_pdf.close()
      pdf_document.close()

  if not image_data:
    raise HTTPException(status_code=500,
                        detail="Problem image data not available")

  try:
    # Extract question text
    ai_grader = AIGraderService()
    question_text = ai_grader.get_or_extract_question(session_id,
                                                      request.problem_number,
                                                      image_data)

    return ExtractQuestionResponse(
      problem_number=request.problem_number,
      question_text=question_text,
      message="Question text extracted successfully")

  except Exception as e:
    log.error(f"Failed to extract question: {e}", exc_info=True)
    raise HTTPException(status_code=500,
                        detail=f"Failed to extract question: {str(e)}")


@router.post("/{session_id}/autograde", response_model=AutogradeResponse)
async def start_autograde(session_id: int, request: AutogradeRequest,
                          background_tasks: BackgroundTasks):
  """Start autograding process for a problem"""

  session_repo = SessionRepository()
  problem_repo = ProblemRepository()
  metadata_repo = ProblemMetadataRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  # Count ungraded problems (include blank submissions for feedback)
  ungraded_count = problem_repo.count_ungraded_for_problem_number(
    session_id, request.problem_number)

  if ungraded_count == 0:
    raise HTTPException(
      status_code=400,
      detail=
      f"No ungraded problems found for problem number {request.problem_number}"
    )

  # Update question_text and max_points in metadata
  metadata_repo.upsert_question_text(session_id, request.problem_number,
                                     request.question_text)
  metadata_repo.upsert_max_points(session_id, request.problem_number,
                                  request.max_points)

  # Create SSE stream for progress updates
  stream_id = sse.make_stream_id("autograde", session_id)
  sse.create_stream(stream_id)

  # Start background autograding
  background_tasks.add_task(run_autograding, session_id,
                            request.problem_number, request.max_points,
                            stream_id)

  return AutogradeResponse(
    status="started",
    problem_number=request.problem_number,
    message=f"Autograding started for {ungraded_count} problems")


async def run_autograding(session_id: int, problem_number: int,
                          max_points: float, stream_id: str):
  """Background task to autograde problems with SSE progress updates"""
  try:
    log.info(
      f"Starting autograding for session {session_id}, problem {problem_number}"
    )

    # Send start event
    await sse.send_event(
      stream_id, "start",
      {"message": f"Starting autograding for problem {problem_number}..."})

    # Get event loop reference
    loop = asyncio.get_event_loop()

    # Create AI grader service
    ai_grader = AIGraderService()

    # Progress callback for SSE updates
    def update_progress(current, total, message):
      progress_percent = min(100, int(
        (current / total) * 100)) if total > 0 else 0

      try:
        asyncio.run_coroutine_threadsafe(
          sse.send_event(
            stream_id, "progress", {
              "current": current,
              "total": total,
              "progress": progress_percent,
              "message": message
            }), loop)
      except Exception as e:
        log.error(f"Failed to send SSE event: {e}")

    # Run AI grading in thread executor
    result = await loop.run_in_executor(
      None,
      lambda: ai_grader.autograde_problem(session_id,
                                          problem_number,
                                          max_points=max_points,
                                          progress_callback=update_progress))

    log.info(
      f"Autograding complete for session {session_id}, problem {problem_number}: {result}"
    )

    # Send completion event
    await sse.send_event(
      stream_id, "complete", {
        "graded": result["graded"],
        "total": result["total"],
        "message": result["message"]
      })

  except Exception as e:
    log.error(
      f"Autograding failed for session {session_id}, problem {problem_number}: {e}",
      exc_info=True)

    # Send error event
    await sse.send_event(stream_id, "error", {
      "error": str(e),
      "message": f"Autograding failed: {str(e)}"
    })


@router.post("/{session_id}/generate-rubric",
             response_model=GenerateRubricResponse)
async def generate_rubric(session_id: int, request: GenerateRubricRequest):
  """Generate a grading rubric using AI and representative examples"""

  session_repo = SessionRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  try:
    ai_grader = AIGraderService()

    # Get grading examples (manually graded submissions)
    example_answers = ai_grader.get_grading_examples(
      session_id, request.problem_number, limit=request.num_examples)

    if not example_answers:
      raise HTTPException(
        status_code=400,
        detail=
        f"No manually graded examples found for problem {request.problem_number}. "
        "Please manually grade at least a few submissions first.")

    # Generate rubric
    rubric = ai_grader.generate_rubric(request.question_text,
                                       request.max_points,
                                       example_answers=example_answers)

    return GenerateRubricResponse(
      problem_number=request.problem_number,
      rubric=rubric,
      message=f"Generated rubric based on {len(example_answers)} example(s)")

  except HTTPException:
    raise
  except Exception as e:
    log.error(f"Failed to generate rubric: {e}", exc_info=True)
    raise HTTPException(status_code=500,
                        detail=f"Failed to generate rubric: {str(e)}")


@router.post("/{session_id}/save-rubric")
async def save_rubric(session_id: int, request: SaveRubricRequest):
  """Save or update a grading rubric"""

  session_repo = SessionRepository()
  metadata_repo = ProblemMetadataRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  # Save rubric to metadata
  metadata_repo.upsert_grading_rubric(session_id, request.problem_number,
                                      request.rubric)

  return {"status": "success", "message": "Rubric saved successfully"}


@router.get("/{session_id}/rubric/{problem_number}")
async def get_rubric(session_id: int, problem_number: int):
  """Get the current rubric for a problem"""

  session_repo = SessionRepository()
  metadata_repo = ProblemMetadataRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  # Get rubric from metadata
  rubric = metadata_repo.get_grading_rubric(session_id, problem_number)

  return {"problem_number": problem_number, "rubric": rubric}
