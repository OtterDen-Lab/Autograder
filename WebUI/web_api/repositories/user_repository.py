"""
Repository for user management.
"""
from typing import Optional, List
import sqlite3
from datetime import datetime

from .base import BaseRepository
from ..domain.user import User


class UserRepository(BaseRepository[User]):
  """
  Data access for users.

  Provides CRUD operations and user-specific queries.
  """

  def _row_to_domain(self, row: sqlite3.Row) -> User:
    """Convert database row to User domain object."""
    return User(
      id=row["id"],
      username=row["username"],
      email=row["email"],
      password_hash=row["password_hash"],
      full_name=row["full_name"],
      role=row["role"],
      is_active=bool(row["is_active"]),
      created_at=datetime.fromisoformat(row["created_at"]),
      created_by=row["created_by"]
    )

  def get_by_id(self, user_id: int) -> Optional[User]:
    """
    Get user by ID.

    Args:
      user_id: User primary key

    Returns:
      User or None if not found
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
      )

  def get_by_username(self, username: str) -> Optional[User]:
    """
    Get user by username.

    Args:
      username: Unique username

    Returns:
      User or None if not found
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        "SELECT * FROM users WHERE username = ?",
        (username,)
      )

  def get_by_email(self, email: str) -> Optional[User]:
    """
    Get user by email.

    Args:
      email: Unique email address

    Returns:
      User or None if not found
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        "SELECT * FROM users WHERE email = ?",
        (email,)
      )

  def create(self, user: User) -> User:
    """
    Create new user.

    Args:
      user: User to create (id will be ignored)

    Returns:
      User with id populated
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
                INSERT INTO users
                (username, email, password_hash, full_name, role, is_active, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user.username, user.email, user.password_hash, user.full_name,
                  user.role, int(user.is_active), user.created_by))

      user.id = cursor.lastrowid
      return user

  def list_all(self) -> List[User]:
    """
    List all users (active and inactive).

    Returns:
      List of all users ordered by creation date (newest first)
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        "SELECT * FROM users ORDER BY created_at DESC"
      )

  def list_active(self) -> List[User]:
    """
    List all active users.

    Returns:
      List of active users ordered by username
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        "SELECT * FROM users WHERE is_active = 1 ORDER BY username"
      )

  def list_by_role(self, role: str) -> List[User]:
    """
    List all active users with specific role.

    Args:
      role: User role ('instructor' or 'ta')

    Returns:
      List of active users with the specified role
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        "SELECT * FROM users WHERE role = ? AND is_active = 1 ORDER BY username",
        (role,)
      )

  def update_password(self, user_id: int, password_hash: str):
    """
    Update user password.

    Args:
      user_id: User primary key
      password_hash: New bcrypt password hash
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (password_hash, user_id)
      )

  def deactivate(self, user_id: int):
    """
    Deactivate user (soft delete).

    Args:
      user_id: User primary key
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "UPDATE users SET is_active = 0 WHERE id = ?",
        (user_id,)
      )

  def reactivate(self, user_id: int):
    """
    Reactivate previously deactivated user.

    Args:
      user_id: User primary key
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "UPDATE users SET is_active = 1 WHERE id = ?",
        (user_id,)
      )
