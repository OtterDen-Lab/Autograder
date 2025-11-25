"""
Name matching endpoints for unmatched submissions.
"""
from fastapi import APIRouter, HTTPException
from typing import List
import json

from ..models import NameMatchRequest
from ..database import get_db_connection
from ..repositories import SessionRepository, SubmissionRepository
from lms_interface.canvas_interface import CanvasInterface

router = APIRouter()


@router.get("/{session_id}/submissions")
async def get_all_submissions(session_id: int):
  """Get all submissions for a session (unmatched first, then matched)"""
  submission_repo = SubmissionRepository()

  # Get all submissions, ordered by match status
  all_submissions = submission_repo.get_by_session(session_id)

  # Sort: unmatched first, then by document_id
  all_submissions.sort(key=lambda s: (s.is_matched(), s.document_id))

  submissions = []
  for sub in all_submissions:
    submissions.append({
      "id": sub.id,
      "document_id": sub.document_id,
      "approximate_name": sub.approximate_name or "(no name detected)",
      "name_image_data": sub.name_image_data,
      "student_name": sub.student_name,
      "canvas_user_id": sub.canvas_user_id,
      "is_matched": sub.is_matched()
    })

  return {"submissions": submissions}


@router.get("/{session_id}/students")
async def get_all_students(session_id: int):
  """Get all Canvas students with match status (unmatched first, then matched)"""
  session_repo = SessionRepository()
  submission_repo = SubmissionRepository()

  # Get session info
  session = session_repo.get_by_id(session_id)
  if not session:
    raise HTTPException(status_code=404, detail="Session not found")

  # Get Canvas students
  canvas_interface = CanvasInterface(prod=session.use_prod_canvas)
  course = canvas_interface.get_course(session.course_id)
  assignment = course.get_assignment(session.assignment_id)
  all_students = assignment.get_students()

  # Get already matched user IDs
  matched_ids = submission_repo.get_existing_canvas_users(session_id)

  # Create list with all students, marked as matched or not
  students = [{
    "user_id": s.user_id,
    "name": s.name,
    "is_matched": s.user_id in matched_ids
  } for s in all_students]

  # Sort: unmatched first, then alphabetically within each group
  students.sort(key=lambda s: (s["is_matched"], s["name"]))

  return {"students": students}


@router.post("/{session_id}/match")
async def match_submission(session_id: int, match: NameMatchRequest):
  """Manually match a submission to a Canvas student (allows reassignment)"""
  session_repo = SessionRepository()
  submission_repo = SubmissionRepository()

  # Verify the submission exists and belongs to this session
  submission = submission_repo.get_by_id(match.submission_id)
  if not submission or submission.session_id != session_id:
    raise HTTPException(status_code=404, detail="Submission not found")

  # Get session info for Canvas access
  session = session_repo.get_by_id(session_id)
  if not session:
    raise HTTPException(status_code=404, detail="Session not found")

  # Get student name from Canvas
  canvas_interface = CanvasInterface(prod=session.use_prod_canvas)
  course = canvas_interface.get_course(session.course_id)
  assignment = course.get_assignment(session.assignment_id)
  students = assignment.get_students()

  student = next((s for s in students if s.user_id == match.canvas_user_id), None)
  if not student:
    raise HTTPException(status_code=404, detail="Student not found in Canvas")

  # Check if this student is already matched to another submission
  previous_submission = submission_repo.get_by_canvas_user(session_id, match.canvas_user_id)
  previous_submission_id = None

  if previous_submission and previous_submission.id != match.submission_id:
    # If student was previously matched to a different submission, unassign them
    previous_submission_id = previous_submission.id
    submission_repo.clear_match(previous_submission_id)

  # Update submission with new match
  submission_repo.update_match(match.submission_id, match.canvas_user_id, student.name)

  # Check if all submissions are now matched
  unmatched_count = submission_repo.count_unmatched(session_id)

  # DON'T auto-update status to 'ready' - wait for user to click "Confirm All Matches"
  # This allows the user to review all matches before proceeding

  return {
    "status": "matched",
    "student_name": student.name,
    "remaining_unmatched": unmatched_count,
    "reassigned_from": previous_submission_id
  }
