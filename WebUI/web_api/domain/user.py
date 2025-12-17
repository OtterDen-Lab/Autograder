"""
Domain model for users (authentication and authorization).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class User:
  """
  Domain model for users table.

  This is a plain data class - changes are not automatically persisted.
  To save changes, call repository.update(user).

  Attributes:
    id: Primary key
    username: Unique username for login
    email: Unique email address
    password_hash: Bcrypt hashed password
    full_name: Display name (optional)
    role: User role ('instructor' or 'ta')
    is_active: Whether user account is active
    created_at: When user was created
    created_by: User ID of creator (for audit trail)
  """
  id: int
  username: str
  email: str
  password_hash: str
  full_name: Optional[str]
  role: str  # 'instructor' or 'ta'
  is_active: bool
  created_at: datetime
  created_by: Optional[int] = None
