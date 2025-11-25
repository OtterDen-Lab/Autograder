"""
Repository for problem metadata (max_points, rubrics, etc.).
"""
from typing import Optional, Dict, Tuple
import sqlite3

from .base import BaseRepository


class ProblemMetadataRepository(BaseRepository):
  """
  Data access for problem_metadata table.

  Stores per-problem configuration like max_points, rubrics, default feedback.
  This is more of a lookup table, so it doesn't have a domain model.
  Returns simple dicts/values.
  """

  def _row_to_domain(self, row: sqlite3.Row):
    """Not used - this repository returns dicts/primitives."""
    return dict(row)

  def get_max_points(self, session_id: int, problem_number: int) -> Optional[float]:
    """
    Get max_points for a specific problem number.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      Max points or None if not set
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT max_points FROM problem_metadata
        WHERE session_id = ? AND problem_number = ?
      """, (session_id, problem_number))

      row = cursor.fetchone()
      return row["max_points"] if row else None

  def get_all_max_points(self, session_id: int) -> Dict[int, float]:
    """
    Get max_points for all problems.

    Args:
      session_id: Session primary key

    Returns:
      Dict mapping problem_number to max_points
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT problem_number, max_points
        FROM problem_metadata
        WHERE session_id = ?
      """, (session_id,))

      return {row["problem_number"]: row["max_points"]
              for row in cursor.fetchall()}

  def upsert_max_points(self, session_id: int, problem_number: int,
                       max_points: float) -> None:
    """
    Insert or update max_points for a problem.

    Uses ON CONFLICT to handle both insert and update cases.

    Args:
      session_id: Session primary key
      problem_number: Problem number
      max_points: Maximum points possible
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        INSERT INTO problem_metadata (session_id, problem_number, max_points)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, problem_number)
        DO UPDATE SET max_points = excluded.max_points,
                     updated_at = CURRENT_TIMESTAMP
      """, (session_id, problem_number, max_points))

  def get_default_feedback(self, session_id: int,
                          problem_number: int) -> Optional[Tuple[str, float]]:
    """
    Get default feedback and threshold.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      Tuple of (feedback, threshold) or None if not set
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT default_feedback, default_feedback_threshold
        FROM problem_metadata
        WHERE session_id = ? AND problem_number = ?
      """, (session_id, problem_number))

      row = cursor.fetchone()
      if row:
        return (row["default_feedback"],
                row["default_feedback_threshold"] or 100.0)
      return None

  def upsert_default_feedback(self, session_id: int, problem_number: int,
                             feedback: str, threshold: float = 100.0) -> None:
    """
    Insert or update default feedback for a problem.

    Args:
      session_id: Session primary key
      problem_number: Problem number
      feedback: Default feedback text
      threshold: Score threshold for auto-applying feedback
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        INSERT INTO problem_metadata
        (session_id, problem_number, default_feedback, default_feedback_threshold)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(session_id, problem_number)
        DO UPDATE SET
          default_feedback = excluded.default_feedback,
          default_feedback_threshold = excluded.default_feedback_threshold,
          updated_at = CURRENT_TIMESTAMP
      """, (session_id, problem_number, feedback, threshold))

  def get_question_text(self, session_id: int, problem_number: int) -> Optional[str]:
    """
    Get question text for a problem.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      Question text or None if not set
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT question_text FROM problem_metadata
        WHERE session_id = ? AND problem_number = ?
      """, (session_id, problem_number))

      row = cursor.fetchone()
      return row["question_text"] if row else None

  def upsert_question_text(self, session_id: int, problem_number: int,
                          question_text: str) -> None:
    """
    Insert or update question text for a problem.

    Args:
      session_id: Session primary key
      problem_number: Problem number
      question_text: Question text (AI-extracted or manual)
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        INSERT INTO problem_metadata (session_id, problem_number, question_text)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, problem_number)
        DO UPDATE SET question_text = excluded.question_text,
                     updated_at = CURRENT_TIMESTAMP
      """, (session_id, problem_number, question_text))

  def get_grading_rubric(self, session_id: int, problem_number: int) -> Optional[str]:
    """
    Get grading rubric for a problem.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      Grading rubric (JSON string) or None if not set
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT grading_rubric FROM problem_metadata
        WHERE session_id = ? AND problem_number = ?
      """, (session_id, problem_number))

      row = cursor.fetchone()
      return row["grading_rubric"] if row else None

  def upsert_grading_rubric(self, session_id: int, problem_number: int,
                           grading_rubric: str) -> None:
    """
    Insert or update grading rubric for a problem.

    Args:
      session_id: Session primary key
      problem_number: Problem number
      grading_rubric: Grading rubric (typically JSON string)
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        INSERT INTO problem_metadata (session_id, problem_number, grading_rubric)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id, problem_number)
        DO UPDATE SET grading_rubric = excluded.grading_rubric,
                     updated_at = CURRENT_TIMESTAMP
      """, (session_id, problem_number, grading_rubric))

  def delete_by_session(self, session_id: int) -> int:
    """
    Delete all metadata for session.

    Args:
      session_id: Session primary key

    Returns:
      Number of metadata records deleted
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("DELETE FROM problem_metadata WHERE session_id = ?",
                    (session_id,))
      return cursor.rowcount

  def exists(self, session_id: int, problem_number: int) -> bool:
    """
    Check if metadata exists for a problem.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      True if metadata exists
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT 1 FROM problem_metadata
        WHERE session_id = ? AND problem_number = ?
        LIMIT 1
      """, (session_id, problem_number))
      return cursor.fetchone() is not None
