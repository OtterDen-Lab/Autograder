"""
Repository for feedback tags.
"""
from typing import Optional, List
import sqlite3
from datetime import datetime

from .base import BaseRepository
from ..domain.feedback_tag import FeedbackTag


class FeedbackTagRepository(BaseRepository[FeedbackTag]):
  """
  Data access for feedback tags.

  Feedback tags are reusable grading comments for specific problems.
  """

  def _row_to_domain(self, row: sqlite3.Row) -> FeedbackTag:
    """Convert database row to FeedbackTag domain object."""
    created_at = None
    if row["created_at"]:
      try:
        created_at = datetime.fromisoformat(row["created_at"])
      except (ValueError, TypeError):
        created_at = datetime.now()

    return FeedbackTag(
      id=row["id"],
      session_id=row["session_id"],
      problem_number=row["problem_number"],
      short_name=row["short_name"],
      comment_text=row["comment_text"],
      created_at=created_at,
      use_count=row["use_count"]
    )

  def get_by_id(self, tag_id: int) -> Optional[FeedbackTag]:
    """
    Get feedback tag by ID.

    Args:
      tag_id: Tag primary key

    Returns:
      FeedbackTag or None if not found
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        "SELECT * FROM feedback_tags WHERE id = ?",
        (tag_id,)
      )

  def get_for_problem(self, session_id: int, problem_number: int) -> List[FeedbackTag]:
    """
    Get all feedback tags for a specific session and problem number.

    Returns tags sorted by use_count (most used first), then by short_name.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      List of FeedbackTag objects
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        """
        SELECT * FROM feedback_tags
        WHERE session_id = ? AND problem_number = ?
        ORDER BY use_count DESC, short_name ASC
        """,
        (session_id, problem_number)
      )

  def create(self, session_id: int, problem_number: int,
             short_name: str, comment_text: str) -> FeedbackTag:
    """
    Create a new feedback tag.

    Args:
      session_id: Session primary key
      problem_number: Problem number
      short_name: Display name (1-30 chars)
      comment_text: Full feedback text (1-500 chars)

    Returns:
      Created FeedbackTag with ID

    Raises:
      sqlite3.IntegrityError: If short_name already exists for this problem
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()

      cursor.execute("""
        INSERT INTO feedback_tags (session_id, problem_number, short_name, comment_text)
        VALUES (?, ?, ?, ?)
      """, (session_id, problem_number, short_name, comment_text))

      tag_id = cursor.lastrowid

      # Fetch the created tag
      cursor.execute("SELECT * FROM feedback_tags WHERE id = ?", (tag_id,))
      row = cursor.fetchone()
      return self._row_to_domain(row)

  def delete(self, tag_id: int) -> bool:
    """
    Delete a feedback tag.

    Args:
      tag_id: Tag primary key

    Returns:
      True if deleted, False if not found
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("DELETE FROM feedback_tags WHERE id = ?", (tag_id,))
      return cursor.rowcount > 0

  def increment_use_count(self, tag_id: int) -> bool:
    """
    Increment the use_count for a tag.

    Called when a tag is applied to a grade.

    Args:
      tag_id: Tag primary key

    Returns:
      True if incremented, False if tag not found
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE feedback_tags
        SET use_count = use_count + 1
        WHERE id = ?
      """, (tag_id,))
      return cursor.rowcount > 0
