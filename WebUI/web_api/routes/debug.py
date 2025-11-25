"""
Debug endpoints for testing and troubleshooting.
These endpoints provide alternative views and data access patterns
for development and debugging purposes.
"""
import logging
from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pydantic import BaseModel

from ..repositories import SessionRepository, SubmissionRepository, ProblemRepository

router = APIRouter(prefix="/debug", tags=["debug"])
log = logging.getLogger(__name__)


class SubmissionBlankStats(BaseModel):
  """Statistics about blank detection for a submission"""
  submission_id: int
  student_name: Optional[str]
  display_name: Optional[str]
  total_problems: int
  blank_detected: int
  blank_percentage: float
  graded_problems: int


@router.get("/sessions/{session_id}/submissions-by-blank-rate")
async def get_submissions_by_blank_rate(
    session_id: int) -> List[SubmissionBlankStats]:
  """
    Get all submissions sorted by percentage of problems detected as blank.
    Useful for debugging blank detection algorithm.

    Args:
        session_id: The grading session ID

    Returns:
        List of submissions with blank detection stats, sorted by blank_percentage descending
    """
  log.info(f"Getting submissions by blank rate for session {session_id}")

  session_repo = SessionRepository()
  submission_repo = SubmissionRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  # Get blank stats for each submission
  stats = submission_repo.get_blank_stats(session_id)

  results = []
  for row in stats:
    results.append(
      SubmissionBlankStats(
        submission_id=row["submission_id"],
        student_name=row["student_name"],
        display_name=row["display_name"],
        total_problems=row["total_problems"],
        blank_detected=row["blank_detected"],
        blank_percentage=row["blank_percentage"],
        graded_problems=row["graded_problems"],
      ))

  log.info(f"Found {len(results)} submissions for session {session_id}")
  return results


@router.get("/sessions/{session_id}/problem-blank-distribution")
async def get_problem_blank_distribution(session_id: int):
  """
    Get the distribution of blank detection across all problems.
    Shows how many submissions were marked blank for each problem number.

    Args:
        session_id: The grading session ID

    Returns:
        Dict with problem numbers as keys and blank counts as values
    """
  log.info(f"Getting problem blank distribution for session {session_id}")

  session_repo = SessionRepository()
  problem_repo = ProblemRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  distribution = problem_repo.get_blank_distribution(session_id)

  return distribution


@router.get(
  "/sessions/{session_id}/problems/{problem_number}/submissions-by-ink")
async def get_submissions_by_ink_for_problem(session_id: int,
                                             problem_number: int):
  """
    Get all submissions for a specific problem, sorted by black pixel ratio (ink density).
    Useful for visually inspecting blank detection across all students for one problem.

    Args:
        session_id: The grading session ID
        problem_number: The problem number to inspect

    Returns:
        List of submissions sorted by black_pixel_ratio (ascending - least ink first)
    """
  import base64
  import json
  import fitz
  from ..services.exam_processor import ExamProcessor

  log.info(
    f"Getting submissions for session {session_id}, problem {problem_number}, sorted by ink"
  )

  session_repo = SessionRepository()
  problem_repo = ProblemRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  # Get all problems for this problem number with submission data
  problems = problem_repo.get_problems_with_submission_data(session_id, problem_number)

  if not problems:
    return []

  exam_processor = ExamProcessor()
  submissions_with_ink = []

  for problem in problems:
    # Extract image
    region_coords = json.loads(problem["region_coords"])
    pdf_base64 = problem["exam_pdf_data"]

    start_page = region_coords["page_number"]
    start_y = region_coords["region_y_start"]
    end_page = region_coords.get("end_page_number", start_page)
    end_y = region_coords["region_y_end"]

    try:
      problem_image_base64, _ = exam_processor._extract_cross_page_region(
        fitz.open(stream=base64.b64decode(pdf_base64), filetype="pdf"),
        start_page,
        start_y,
        end_page,
        end_y,
        dpi=150)

      # Extract black_pixel_ratio from blank_reasoning field
      # Format: "Black ratio: 0.0420, Threshold (gap): 0.0255"
      black_ratio = 0.0
      reasoning = problem["blank_reasoning"]
      if reasoning:
        import re
        match = re.search(r'Black ratio:\s*([0-9.]+)', reasoning)
        if match:
          black_ratio = float(match.group(1))

    except Exception as e:
      log.error(f"Failed to process problem {problem['problem_id']}: {e}")
      problem_image_base64 = ""
      black_ratio = 0

    submissions_with_ink.append({
      "problem_id":
      problem["problem_id"],
      "submission_id":
      problem["submission_id"],
      "problem_number":
      problem["problem_number"],
      "student_name":
      problem["student_name"],
      "display_name":
      problem["display_name"],
      "is_blank":
      bool(problem["is_blank"]),
      "blank_confidence":
      problem["blank_confidence"] or 0.0,
      "blank_method":
      problem["blank_method"],
      "blank_reasoning":
      problem["blank_reasoning"],
      "score":
      problem["score"],
      "feedback":
      problem["feedback"],
      "graded":
      bool(problem["graded"]),
      "image_data":
      problem_image_base64,
      "black_pixel_ratio":
      black_ratio,
    })

  # Sort by black pixel ratio (ascending - least ink first)
  # This uses the ACTUAL ratio calculated by the population algorithm
  submissions_with_ink.sort(key=lambda x: x["black_pixel_ratio"])

  log.info(
    f"Returning {len(submissions_with_ink)} submissions for problem {problem_number}"
  )
  return submissions_with_ink


@router.post("/sessions/{session_id}/clear-all-blank-flags")
async def clear_all_blank_flags(session_id: int):
  """
    Clear all is_blank flags for ungraded problems in a session.
    Useful for testing blank detection from scratch.

    WARNING: This will reset auto-detection for all ungraded problems.

    Args:
        session_id: The grading session ID

    Returns:
        Status and count of cleared flags
    """
  log.warning(f"Clearing all blank flags for session {session_id}")

  session_repo = SessionRepository()
  problem_repo = ProblemRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  # Clear blank flags only for ungraded problems
  rows_updated = problem_repo.clear_blank_flags_for_session(session_id)

  log.info(
    f"Cleared blank flags for {rows_updated} ungraded problems in session {session_id}"
  )

  return {
    "status": "success",
    "rows_updated": rows_updated,
    "message": f"Cleared blank flags for {rows_updated} ungraded problems"
  }
