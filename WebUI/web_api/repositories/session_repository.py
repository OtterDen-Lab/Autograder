"""
Repository for grading sessions.
"""
from typing import Optional, List
import sqlite3
import json
from datetime import datetime

from .base import BaseRepository
from ..domain.session import GradingSession
from ..domain.common import SessionStatus


class SessionRepository(BaseRepository[GradingSession]):
  """
  Data access for grading sessions.

  Provides CRUD operations and session-specific queries.
  """

  def _row_to_domain(self, row: sqlite3.Row) -> GradingSession:
    """Convert database row to GradingSession domain object."""
    # Parse JSON metadata if present
    metadata = None
    if row["metadata"]:
      try:
        metadata = json.loads(row["metadata"])
      except json.JSONDecodeError:
        metadata = None

    return GradingSession(
      id=row["id"],
      assignment_id=row["assignment_id"],
      assignment_name=row["assignment_name"],
      course_id=row["course_id"],
      course_name=row["course_name"],
      status=SessionStatus(row["status"]),
      created_at=datetime.fromisoformat(row["created_at"]),
      updated_at=datetime.fromisoformat(row["updated_at"]),
      canvas_points=row["canvas_points"],
      metadata=metadata,
      total_exams=row["total_exams"],
      processed_exams=row["processed_exams"],
      matched_exams=row["matched_exams"],
      processing_message=row["processing_message"],
      use_prod_canvas=bool(row["use_prod_canvas"])
    )

  def get_by_id(self, session_id: int) -> Optional[GradingSession]:
    """
    Get session by ID.

    Args:
      session_id: Session primary key

    Returns:
      GradingSession or None if not found
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        "SELECT * FROM grading_sessions WHERE id = ?",
        (session_id,)
      )

  def exists(self, session_id: int) -> bool:
    """
    Check if session exists.

    Args:
      session_id: Session primary key

    Returns:
      True if session exists, False otherwise
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "SELECT 1 FROM grading_sessions WHERE id = ? LIMIT 1",
        (session_id,)
      )
      return cursor.fetchone() is not None

  def create(self, session: GradingSession) -> GradingSession:
    """
    Create new session.

    Args:
      session: GradingSession to create (id will be ignored)

    Returns:
      GradingSession with id populated
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()

      # Serialize metadata if present
      metadata_json = json.dumps(session.metadata) if session.metadata else None

      cursor.execute("""
        INSERT INTO grading_sessions
        (assignment_id, assignment_name, course_id, course_name, status,
         canvas_points, metadata, total_exams, processed_exams, matched_exams,
         processing_message, use_prod_canvas)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """, (
        session.assignment_id,
        session.assignment_name,
        session.course_id,
        session.course_name,
        session.status.value,
        session.canvas_points,
        metadata_json,
        session.total_exams,
        session.processed_exams,
        session.matched_exams,
        session.processing_message,
        1 if session.use_prod_canvas else 0
      ))

      # Get created session with ID (use same connection)
      session_id = cursor.lastrowid
      cursor.execute("SELECT * FROM grading_sessions WHERE id = ?", (session_id,))
      row = cursor.fetchone()
      return self._row_to_domain(row)

  def update(self, session: GradingSession) -> None:
    """
    Update existing session.

    Updates all fields based on session.id.

    Args:
      session: GradingSession with changes to persist
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()

      metadata_json = json.dumps(session.metadata) if session.metadata else None

      cursor.execute("""
        UPDATE grading_sessions
        SET assignment_id = ?,
            assignment_name = ?,
            course_id = ?,
            course_name = ?,
            status = ?,
            canvas_points = ?,
            metadata = ?,
            total_exams = ?,
            processed_exams = ?,
            matched_exams = ?,
            processing_message = ?,
            use_prod_canvas = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
      """, (
        session.assignment_id,
        session.assignment_name,
        session.course_id,
        session.course_name,
        session.status.value,
        session.canvas_points,
        metadata_json,
        session.total_exams,
        session.processed_exams,
        session.matched_exams,
        session.processing_message,
        1 if session.use_prod_canvas else 0,
        session.id
      ))

  def update_status(self, session_id: int, status: SessionStatus,
                   message: Optional[str] = None) -> None:
    """
    Update just status and optional message.

    More efficient than full update when only changing status.

    Args:
      session_id: Session primary key
      status: New status
      message: Optional processing message
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()

      if message is not None:
        cursor.execute("""
          UPDATE grading_sessions
          SET status = ?, processing_message = ?, updated_at = CURRENT_TIMESTAMP
          WHERE id = ?
        """, (status.value, message, session_id))
      else:
        cursor.execute("""
          UPDATE grading_sessions
          SET status = ?, updated_at = CURRENT_TIMESTAMP
          WHERE id = ?
        """, (status.value, session_id))

  def update_progress(self, session_id: int, total: Optional[int] = None,
                     processed: Optional[int] = None,
                     matched: Optional[int] = None) -> None:
    """
    Update progress counters.

    Common operation during upload processing.
    Only updates non-None values.

    Args:
      session_id: Session primary key
      total: Total exams (optional)
      processed: Processed exams (optional)
      matched: Matched exams (optional)
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()

      updates = []
      params = []

      if total is not None:
        updates.append("total_exams = ?")
        params.append(total)
      if processed is not None:
        updates.append("processed_exams = ?")
        params.append(processed)
      if matched is not None:
        updates.append("matched_exams = ?")
        params.append(matched)

      if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(session_id)

        sql = f"UPDATE grading_sessions SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(sql, params)

  def delete(self, session_id: int) -> bool:
    """
    Delete session.

    Note: Caller is responsible for deleting related records first
    (submissions, problems, etc.) to maintain referential integrity.

    Args:
      session_id: Session primary key

    Returns:
      True if session was deleted, False if not found
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("DELETE FROM grading_sessions WHERE id = ?", (session_id,))
      return cursor.rowcount > 0

  def list_all(self, order_by_created: bool = True) -> List[GradingSession]:
    """
    Get all sessions.

    Args:
      order_by_created: Order by creation date (newest first)

    Returns:
      List of all GradingSession objects
    """
    with self._get_connection() as conn:
      sql = "SELECT * FROM grading_sessions"
      if order_by_created:
        sql += " ORDER BY created_at DESC"

      return self._execute_and_fetch_all(conn, sql)

  def get_metadata(self, session_id: int) -> Optional[dict]:
    """
    Get just metadata JSON for a session.

    Avoids loading full session object when only metadata is needed.

    Args:
      session_id: Session primary key

    Returns:
      Metadata dict or None if session not found or no metadata
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "SELECT metadata FROM grading_sessions WHERE id = ?",
        (session_id,)
      )
      row = cursor.fetchone()
      if row and row["metadata"]:
        try:
          return json.loads(row["metadata"])
        except json.JSONDecodeError:
          return None
      return None

  def update_metadata(self, session_id: int, metadata: dict) -> None:
    """
    Update just metadata field.

    Common operation during upload processing.

    Args:
      session_id: Session primary key
      metadata: New metadata dict (will be JSON serialized)
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE grading_sessions
        SET metadata = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
      """, (json.dumps(metadata), session_id))
