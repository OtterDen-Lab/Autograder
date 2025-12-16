"""
Session assignment endpoints (for assigning TAs to grading sessions).
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import List
import logging

from ..auth import require_instructor
from ..repositories.session_assignment_repository import SessionAssignmentRepository
from ..repositories.user_repository import UserRepository
from ..repositories.session_repository import SessionRepository
from ..models import AssignUserRequest, AssignmentResponse

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/{session_id}/assign")
async def assign_user_to_session(
  session_id: int,
  request: AssignUserRequest,
  current_user: dict = Depends(require_instructor)
):
  """
  Assign TA to grading session (instructor only).

  Args:
    session_id: Grading session ID
    request: User ID to assign
    current_user: Current authenticated user (must be instructor)

  Returns:
    Success message

  Raises:
    HTTPException: 404 if session or user not found
    HTTPException: 400 if trying to assign non-TA user
  """
  assignment_repo = SessionAssignmentRepository()
  user_repo = UserRepository()
  session_repo = SessionRepository()

  # Verify session exists
  if not session_repo.exists(session_id):
    raise HTTPException(status_code=404, detail="Session not found")

  # Verify user exists
  user = user_repo.get_by_id(request.user_id)
  if not user:
    raise HTTPException(status_code=404, detail="User not found")

  if not user.is_active:
    raise HTTPException(status_code=400, detail="User is not active")

  # Only allow assigning TAs to sessions (instructors have access to all sessions)
  if user.role != "ta":
    raise HTTPException(status_code=400,
                        detail="Can only assign TAs to sessions")

  # Assign user to session
  assignment_repo.assign_user_to_session(
    session_id,
    request.user_id,
    current_user["user_id"]
  )

  log.info(
    f"TA {user.username} assigned to session {session_id} by {current_user['username']}"
  )

  return {
    "success": True,
    "message": f"TA {user.username} assigned to session"
  }


@router.delete("/{session_id}/assign/{user_id}")
async def remove_user_from_session(
  session_id: int,
  user_id: int,
  current_user: dict = Depends(require_instructor)
):
  """
  Remove TA from grading session (instructor only).

  Args:
    session_id: Grading session ID
    user_id: User ID to remove
    current_user: Current authenticated user (must be instructor)

  Returns:
    Success message
  """
  assignment_repo = SessionAssignmentRepository()
  user_repo = UserRepository()

  # Get user info for logging
  user = user_repo.get_by_id(user_id)
  username = user.username if user else f"user {user_id}"

  # Remove assignment
  assignment_repo.remove_assignment(session_id, user_id)

  log.info(
    f"{username} removed from session {session_id} by {current_user['username']}"
  )

  return {
    "success": True,
    "message": f"User removed from session"
  }


@router.get("/{session_id}/assignments", response_model=List[AssignmentResponse])
async def get_session_assignments(
  session_id: int,
  current_user: dict = Depends(require_instructor)
):
  """
  Get list of TAs assigned to session (instructor only).

  Args:
    session_id: Grading session ID
    current_user: Current authenticated user (must be instructor)

  Returns:
    List of AssignmentResponse objects
  """
  assignment_repo = SessionAssignmentRepository()
  user_repo = UserRepository()

  # Get all assignments for this session
  session_assignments = assignment_repo.get_assignments_for_session(session_id)

  # Get user details for each assignment
  assignments = []
  for sa in session_assignments:
    user = user_repo.get_by_id(sa.user_id)
    if user:
      assignments.append(
        AssignmentResponse(
          session_id=sa.session_id,
          user_id=user.id,
          username=user.username,
          full_name=user.full_name,
          assigned_at=sa.assigned_at.isoformat()
        )
      )

  return assignments
