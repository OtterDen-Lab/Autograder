"""
Authentication dependencies and decorators for FastAPI.
"""
from fastapi import Depends, HTTPException, Request, Cookie
from typing import Optional, List
import logging

from .services.auth_service import AuthService
from .database import get_db_connection

log = logging.getLogger(__name__)


# Current user dependency
async def get_current_user(
  request: Request,
  auth_session_id: Optional[str] = Cookie(None, alias="auth_session_id")
) -> dict:
  """
  FastAPI dependency to get current authenticated user.
  Raises 401 if not authenticated.

  Args:
    request: FastAPI request object (for logging)
    auth_session_id: Session ID from cookie

  Returns:
    dict: User info (user_id, username, email, full_name, role)

  Raises:
    HTTPException: 401 if not authenticated or session invalid
  """
  if not auth_session_id:
    log.warning(
      f"Unauthenticated request to {request.url.path} - no session cookie")
    raise HTTPException(status_code=401, detail="Not authenticated")

  auth_service = AuthService()
  with get_db_connection() as conn:
    user = auth_service.validate_session(auth_session_id, conn)

  if not user:
    log.warning(
      f"Invalid session {auth_session_id[:8]}... for {request.url.path}")
    raise HTTPException(status_code=401,
                        detail="Invalid or expired session")

  return user


# Optional user (for public endpoints that can work with/without auth)
async def get_current_user_optional(
  auth_session_id: Optional[str] = Cookie(None, alias="auth_session_id")
) -> Optional[dict]:
  """
  Get current user if authenticated, None otherwise.

  Use this for endpoints that work differently based on auth status
  but don't require authentication.

  Args:
    auth_session_id: Session ID from cookie

  Returns:
    dict: User info if authenticated, None otherwise
  """
  if not auth_session_id:
    return None

  auth_service = AuthService()
  with get_db_connection() as conn:
    return auth_service.validate_session(auth_session_id, conn)


# Role checking
def require_role(allowed_roles: List[str]):
  """
  Dependency factory to require specific role(s).

  Args:
    allowed_roles: List of allowed roles (e.g., ['instructor'])

  Returns:
    FastAPI dependency function

  Usage:
    @app.get("/api/admin")
    async def admin_endpoint(user: dict = Depends(require_role(['instructor']))):
      ...
  """

  async def role_checker(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in allowed_roles:
      log.warning(
        f"User {user['username']} (role={user['role']}) denied access - required role: {', '.join(allowed_roles)}"
      )
      raise HTTPException(
        status_code=403,
        detail=
        f"Access denied. Required role: {', '.join(allowed_roles)}")
    return user

  return role_checker


# Instructor-only shorthand
require_instructor = require_role(["instructor"])


# Session access checker
def require_session_access(session_id_param: str = "session_id"):
  """
  Dependency factory to check if user can access a specific session.
  Instructors: full access to all sessions
  TAs: only assigned sessions

  Args:
    session_id_param: Name of the path parameter containing session_id
                      (default: "session_id")

  Returns:
    FastAPI dependency function

  Usage:
    @app.get("/api/sessions/{session_id}")
    async def get_session(
      session_id: int,
      user: dict = Depends(require_session_access())
    ):
      ...
  """

  async def session_access_checker(
    request: Request,
    user: dict = Depends(get_current_user)
  ) -> dict:
    # Get session_id from path params
    session_id = request.path_params.get(session_id_param)
    if not session_id:
      raise HTTPException(status_code=400, detail="Session ID not provided")

    try:
      session_id = int(session_id)
    except ValueError:
      raise HTTPException(status_code=400, detail="Invalid session ID")

    # Instructors have access to everything
    if user["role"] == "instructor":
      return user

    # TAs must be assigned to the session
    from .repositories.session_assignment_repository import SessionAssignmentRepository
    assignment_repo = SessionAssignmentRepository()

    if not assignment_repo.is_user_assigned(session_id, user["user_id"]):
      log.warning(
        f"TA {user['username']} denied access to session {session_id} - not assigned"
      )
      raise HTTPException(
        status_code=403,
        detail="You do not have access to this grading session")

    return user

  return session_access_checker
