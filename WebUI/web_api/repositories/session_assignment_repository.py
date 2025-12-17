"""
Repository for session assignments (TA access control).
"""
from typing import List, Optional
import sqlite3
from datetime import datetime

from .base import BaseRepository
from dataclasses import dataclass


@dataclass
class SessionAssignment:
  """Session assignment domain model"""
  id: int
  session_id: int
  user_id: int
  assigned_at: datetime
  assigned_by: Optional[int]


class SessionAssignmentRepository(BaseRepository[SessionAssignment]):
  """
  Data access for session assignments.

  Manages which TAs can access which grading sessions.
  """

  def _row_to_domain(self, row: sqlite3.Row) -> SessionAssignment:
    """Convert database row to SessionAssignment domain object."""
    return SessionAssignment(
      id=row["id"],
      session_id=row["session_id"],
      user_id=row["user_id"],
      assigned_at=datetime.fromisoformat(row["assigned_at"]),
      assigned_by=row["assigned_by"]
    )

  def assign_user_to_session(self, session_id: int, user_id: int,
                              assigned_by: int):
    """
    Assign TA to session.

    Uses INSERT OR IGNORE to avoid errors if assignment already exists.

    Args:
      session_id: Grading session ID
      user_id: User ID to assign
      assigned_by: User ID of person making the assignment
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
                INSERT OR IGNORE INTO session_assignments
                (session_id, user_id, assigned_by)
                VALUES (?, ?, ?)
            """, (session_id, user_id, assigned_by))

  def remove_assignment(self, session_id: int, user_id: int):
    """
    Remove TA from session.

    Args:
      session_id: Grading session ID
      user_id: User ID to remove
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
                DELETE FROM session_assignments
                WHERE session_id = ? AND user_id = ?
            """, (session_id, user_id))

  def get_assigned_sessions(self, user_id: int) -> List[int]:
    """
    Get list of session IDs assigned to a user.

    Args:
      user_id: User ID

    Returns:
      List of grading session IDs
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "SELECT session_id FROM session_assignments WHERE user_id = ?",
        (user_id,)
      )
      return [row[0] for row in cursor.fetchall()]

  def get_assigned_users(self, session_id: int) -> List[int]:
    """
    Get list of user IDs assigned to a session.

    Args:
      session_id: Grading session ID

    Returns:
      List of user IDs
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "SELECT user_id FROM session_assignments WHERE session_id = ?",
        (session_id,)
      )
      return [row[0] for row in cursor.fetchall()]

  def get_assignments_for_session(self, session_id: int) -> List[SessionAssignment]:
    """
    Get full assignment details for a session.

    Args:
      session_id: Grading session ID

    Returns:
      List of SessionAssignment objects
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        "SELECT * FROM session_assignments WHERE session_id = ? ORDER BY assigned_at DESC",
        (session_id,)
      )

  def is_user_assigned(self, session_id: int, user_id: int) -> bool:
    """
    Check if user is assigned to session.

    Args:
      session_id: Grading session ID
      user_id: User ID

    Returns:
      True if user is assigned, False otherwise
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
                SELECT 1 FROM session_assignments
                WHERE session_id = ? AND user_id = ?
            """, (session_id, user_id))
      return cursor.fetchone() is not None

  def get_assignments_for_session(
      self, session_id: int) -> List[SessionAssignment]:
    """
    Get all assignments for a session.

    Args:
      session_id: Grading session ID

    Returns:
      List of SessionAssignment objects
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        "SELECT * FROM session_assignments WHERE session_id = ?",
        (session_id,)
      )

  def get_assignments_for_user(self,
                                user_id: int) -> List[SessionAssignment]:
    """
    Get all assignments for a user.

    Args:
      user_id: User ID

    Returns:
      List of SessionAssignment objects
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        "SELECT * FROM session_assignments WHERE user_id = ?",
        (user_id,)
      )
