"""
Authentication endpoints (login, logout, user management).
"""
from fastapi import APIRouter, HTTPException, Response, Depends, Request
from typing import List
from datetime import datetime
import logging

from ..auth import get_current_user, require_instructor
from ..services.auth_service import AuthService
from ..repositories.user_repository import UserRepository
from ..domain.user import User
from ..database import get_db_connection
from ..models import (
  LoginRequest,
  LoginResponse,
  CreateUserRequest,
  UserResponse,
  ChangePasswordRequest
)

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/login", response_model=LoginResponse)
async def login(request: Request, response: Response,
                credentials: LoginRequest):
  """
  Authenticate user and create session.

  Args:
    request: FastAPI request (for IP address logging)
    response: FastAPI response (for setting cookie)
    credentials: Username and password

  Returns:
    LoginResponse with user info

  Raises:
    HTTPException: 401 if credentials invalid
  """
  user_repo = UserRepository()
  auth_service = AuthService()

  # Get user by username
  user = user_repo.get_by_username(credentials.username)

  if not user or not user.is_active:
    log.warning(
      f"Failed login attempt for username: {credentials.username}")
    raise HTTPException(status_code=401,
                        detail="Invalid username or password")

  # Verify password
  if not auth_service.verify_password(credentials.password,
                                      user.password_hash):
    log.warning(
      f"Failed login attempt for username: {credentials.username} (wrong password)"
    )
    raise HTTPException(status_code=401,
                        detail="Invalid username or password")

  # Create session
  with get_db_connection() as conn:
    session_id = auth_service.create_session(
      user.id,
      request.client.host if request.client else "unknown",
      request.headers.get("user-agent", ""),
      conn
    )

  # Set HTTP-only cookie
  response.set_cookie(
    key="auth_session_id",
    value=session_id,
    httponly=True,
    secure=False,  # Set to True in production with HTTPS
    samesite="lax",
    max_age=24 * 60 * 60  # 24 hours
  )

  log.info(f"User {user.username} (role={user.role}) logged in successfully")

  return LoginResponse(
    success=True,
    user={
      "id": user.id,
      "username": user.username,
      "email": user.email,
      "full_name": user.full_name,
      "role": user.role
    },
    message="Login successful"
  )


@router.post("/logout")
async def logout(
  response: Response,
  user: dict = Depends(get_current_user)
):
  """
  Logout current user by deleting session cookie.

  Args:
    response: FastAPI response (for deleting cookie)
    user: Current authenticated user

  Returns:
    Success message
  """
  # Delete session cookie
  response.delete_cookie(key="auth_session_id")

  log.info(f"User {user['username']} logged out")

  return {"success": True, "message": "Logged out successfully"}


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
  """
  Get current user info.

  Requires authentication. Used by frontend to validate session.

  Args:
    user: Current authenticated user

  Returns:
    User info dict
  """
  return user


@router.post("/users", response_model=UserResponse)
async def create_user(
  user_data: CreateUserRequest,
  current_user: dict = Depends(require_instructor)
):
  """
  Create new user (instructor only).

  Args:
    user_data: User creation data
    current_user: Current authenticated user (must be instructor)

  Returns:
    UserResponse with created user info

  Raises:
    HTTPException: 400 if username/email already exists
  """
  user_repo = UserRepository()
  auth_service = AuthService()

  # Validate role
  if user_data.role not in ["instructor", "ta"]:
    raise HTTPException(status_code=400,
                        detail="Role must be 'instructor' or 'ta'")

  # Check if username already exists
  if user_repo.get_by_username(user_data.username):
    raise HTTPException(status_code=400, detail="Username already exists")

  # Check if email already exists
  if user_repo.get_by_email(user_data.email):
    raise HTTPException(status_code=400, detail="Email already exists")

  # Hash password
  password_hash = auth_service.hash_password(user_data.password)

  # Create user domain object
  new_user = User(
    id=0,  # Will be set by DB
    username=user_data.username,
    email=user_data.email,
    password_hash=password_hash,
    full_name=user_data.full_name,
    role=user_data.role,
    is_active=True,
    created_at=datetime.now(),
    created_by=current_user["user_id"]
  )

  # Save to database
  created_user = user_repo.create(new_user)

  log.info(
    f"User {created_user.username} (role={created_user.role}) created by {current_user['username']}"
  )

  return UserResponse.model_validate(created_user)


@router.get("/users", response_model=List[UserResponse])
async def list_users(current_user: dict = Depends(require_instructor)):
  """
  List all users (instructor only).

  Args:
    current_user: Current authenticated user (must be instructor)

  Returns:
    List of UserResponse objects
  """
  user_repo = UserRepository()
  users = user_repo.list_all()

  return [UserResponse.model_validate(user) for user in users]


@router.delete("/users/{user_id}")
async def deactivate_user(
  user_id: int,
  current_user: dict = Depends(require_instructor)
):
  """
  Deactivate user (instructor only).

  Soft delete - user is marked as inactive but not removed from database.

  Args:
    user_id: ID of user to deactivate
    current_user: Current authenticated user (must be instructor)

  Returns:
    Success message

  Raises:
    HTTPException: 404 if user not found
  """
  user_repo = UserRepository()

  # Check if user exists
  user = user_repo.get_by_id(user_id)
  if not user:
    raise HTTPException(status_code=404, detail="User not found")

  # Prevent deactivating self
  if user_id == current_user["user_id"]:
    raise HTTPException(status_code=400,
                        detail="Cannot deactivate your own account")

  # Deactivate user
  user_repo.deactivate(user_id)

  log.info(
    f"User {user.username} deactivated by {current_user['username']}")

  return {"success": True, "message": "User deactivated"}


@router.post("/change-password")
async def change_password(
  request: ChangePasswordRequest,
  current_user: dict = Depends(get_current_user)
):
  """
  Change current user's password.

  Requires current password for verification.

  Args:
    request: Current and new password
    current_user: Current authenticated user

  Returns:
    Success message

  Raises:
    HTTPException: 401 if current password is incorrect
  """
  user_repo = UserRepository()
  auth_service = AuthService()

  # Get user
  user = user_repo.get_by_id(current_user["user_id"])
  if not user:
    raise HTTPException(status_code=404, detail="User not found")

  # Verify current password
  if not auth_service.verify_password(request.current_password,
                                      user.password_hash):
    raise HTTPException(status_code=401,
                        detail="Current password is incorrect")

  # Update password
  new_password_hash = auth_service.hash_password(request.new_password)
  user_repo.update_password(user.id, new_password_hash)

  log.info(f"User {user.username} changed their password")

  return {"success": True, "message": "Password changed successfully"}
