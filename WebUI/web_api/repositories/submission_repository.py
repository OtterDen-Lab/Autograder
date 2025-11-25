"""
Repository for student submissions.
"""
from typing import Optional, List, Dict
import sqlite3
import json
from datetime import datetime

from .base import BaseRepository
from ..domain.submission import Submission


class SubmissionRepository(BaseRepository[Submission]):
  """
  Data access for submissions.

  Handles student exam submissions with associated problems.
  Supports bulk operations critical for uploads.py performance.
  """

  def _row_to_domain(self, row: sqlite3.Row) -> Submission:
    """Convert database row to Submission domain object."""
    page_mappings = {}
    if row["page_mappings"]:
      try:
        page_mappings = json.loads(row["page_mappings"])
      except json.JSONDecodeError:
        page_mappings = {}

    # Parse graded_at if present
    graded_at = None
    if row["graded_at"]:
      try:
        graded_at = datetime.fromisoformat(row["graded_at"])
      except (ValueError, TypeError):
        graded_at = None

    return Submission(
      id=row["id"],
      session_id=row["session_id"],
      document_id=row["document_id"],
      approximate_name=row["approximate_name"],
      name_image_data=row["name_image_data"],
      student_name=row["student_name"],
      display_name=row["display_name"],
      canvas_user_id=row["canvas_user_id"],
      page_mappings=page_mappings,
      total_score=row["total_score"],
      graded_at=graded_at,
      file_hash=row["file_hash"],
      original_filename=row["original_filename"],
      exam_pdf_data=row["exam_pdf_data"]
    )

  def get_by_id(self, submission_id: int) -> Optional[Submission]:
    """
    Get submission by ID.

    Args:
      submission_id: Submission primary key

    Returns:
      Submission or None if not found
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        "SELECT * FROM submissions WHERE id = ?",
        (submission_id,)
      )

  def get_by_session(self, session_id: int) -> List[Submission]:
    """
    Get all submissions for a session.

    Args:
      session_id: Session primary key

    Returns:
      List of Submission objects ordered by document_id
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        "SELECT * FROM submissions WHERE session_id = ? ORDER BY document_id",
        (session_id,)
      )

  def get_by_session_lightweight(self, session_id: int) -> List[Submission]:
    """
    Get submissions without large binary fields.

    Use for list views where PDF data is not needed.
    Significantly reduces memory usage for large sessions.

    Args:
      session_id: Session primary key

    Returns:
      List of Submission objects (without exam_pdf_data, name_image_data)
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT id, session_id, document_id, approximate_name,
               student_name, display_name, canvas_user_id,
               page_mappings, total_score, graded_at,
               file_hash, original_filename
        FROM submissions
        WHERE session_id = ?
        ORDER BY document_id
      """, (session_id,))

      submissions = []
      for row in cursor.fetchall():
        page_mappings = json.loads(row["page_mappings"]) if row["page_mappings"] else {}
        graded_at = None
        if row["graded_at"]:
          try:
            graded_at = datetime.fromisoformat(row["graded_at"])
          except (ValueError, TypeError):
            pass

        sub = Submission(
          id=row["id"],
          session_id=row["session_id"],
          document_id=row["document_id"],
          approximate_name=row["approximate_name"],
          name_image_data=None,  # Not loaded
          student_name=row["student_name"],
          display_name=row["display_name"],
          canvas_user_id=row["canvas_user_id"],
          page_mappings=page_mappings,
          total_score=row["total_score"],
          graded_at=graded_at,
          file_hash=row["file_hash"],
          original_filename=row["original_filename"],
          exam_pdf_data=None  # Not loaded
        )
        submissions.append(sub)

      return submissions

  def create(self, submission: Submission) -> Submission:
    """
    Create single submission.

    Args:
      submission: Submission to create (id will be ignored)

    Returns:
      Submission with id populated
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()

      cursor.execute("""
        INSERT INTO submissions
        (session_id, document_id, approximate_name, student_name,
         canvas_user_id, page_mappings, file_hash, original_filename,
         name_image_data, exam_pdf_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """, (
        submission.session_id,
        submission.document_id,
        submission.approximate_name,
        submission.student_name,
        submission.canvas_user_id,
        json.dumps(submission.page_mappings),
        submission.file_hash,
        submission.original_filename,
        submission.name_image_data,
        submission.exam_pdf_data
      ))

      # Get created submission with ID (use same connection)
      submission_id = cursor.lastrowid
      cursor.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,))
      row = cursor.fetchone()
      return self._row_to_domain(row)

  def bulk_create(self, submissions: List[Submission]) -> List[Submission]:
    """
    Create multiple submissions in one transaction.

    Critical for uploads.py performance (100+ submissions).
    Returns submissions with IDs populated in same order.

    Args:
      submissions: List of Submission objects to create

    Returns:
      List of created Submission objects with IDs

    Example:
      submissions_to_create = [...]
      created = submission_repo.bulk_create(submissions_to_create)
      # Now can use created[i].id for problems
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      created_submissions = []

      for sub in submissions:
        cursor.execute("""
          INSERT INTO submissions
          (session_id, document_id, approximate_name, student_name,
           canvas_user_id, page_mappings, file_hash, original_filename,
           name_image_data, exam_pdf_data)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
          sub.session_id,
          sub.document_id,
          sub.approximate_name,
          sub.student_name,
          sub.canvas_user_id,
          json.dumps(sub.page_mappings),
          sub.file_hash,
          sub.original_filename,
          sub.name_image_data,
          sub.exam_pdf_data
        ))

        # Get created submission with ID
        submission_id = cursor.lastrowid
        cursor.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,))
        row = cursor.fetchone()
        created_submissions.append(self._row_to_domain(row))

      return created_submissions

  def check_duplicate_hash(self, session_id: int, file_hash: str) -> Optional[Submission]:
    """
    Check if submission with same file hash exists in session.

    Used for duplicate detection during upload.

    Args:
      session_id: Session primary key
      file_hash: SHA256 hash of file

    Returns:
      Existing Submission or None if no duplicate
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        """
        SELECT * FROM submissions
        WHERE session_id = ? AND file_hash = ?
        LIMIT 1
        """,
        (session_id, file_hash)
      )

  def get_existing_hashes(self, session_id: int) -> Dict[str, str]:
    """
    Get all file hashes for submissions in session.

    Returns mapping of hash -> original_filename for duplicate detection.

    Args:
      session_id: Session primary key

    Returns:
      Dict mapping file_hash to original_filename
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT file_hash, original_filename
        FROM submissions
        WHERE session_id = ? AND file_hash IS NOT NULL
      """, (session_id,))

      return {row["file_hash"]: row["original_filename"] for row in cursor.fetchall()}

  def get_existing_canvas_users(self, session_id: int) -> set:
    """
    Get set of Canvas user IDs already matched in session.

    Used to exclude already-matched students from matching UI.

    Args:
      session_id: Session primary key

    Returns:
      Set of canvas_user_id values
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT DISTINCT canvas_user_id
        FROM submissions
        WHERE session_id = ? AND canvas_user_id IS NOT NULL
      """, (session_id,))

      return {row[0] for row in cursor.fetchall()}

  def get_unmatched(self, session_id: int) -> List[Submission]:
    """
    Get submissions without Canvas student match.

    Args:
      session_id: Session primary key

    Returns:
      List of unmatched Submission objects
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        """
        SELECT * FROM submissions
        WHERE session_id = ? AND canvas_user_id IS NULL
        ORDER BY document_id
        """,
        (session_id,)
      )

  def update_match(self, submission_id: int, canvas_user_id: int,
                   student_name: str) -> None:
    """
    Update submission with Canvas student match.

    Args:
      submission_id: Submission primary key
      canvas_user_id: Canvas user ID
      student_name: Student's name from Canvas
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE submissions
        SET canvas_user_id = ?, student_name = ?
        WHERE id = ?
      """, (canvas_user_id, student_name, submission_id))

  def clear_match(self, submission_id: int) -> None:
    """
    Remove Canvas student match from submission.

    Args:
      submission_id: Submission primary key
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE submissions
        SET canvas_user_id = NULL, student_name = NULL
        WHERE id = ?
      """, (submission_id,))

  def get_by_canvas_user(self, session_id: int, canvas_user_id: int) -> Optional[Submission]:
    """
    Find submission matched to specific Canvas user.

    Args:
      session_id: Session primary key
      canvas_user_id: Canvas user ID

    Returns:
      Submission or None if not found
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        """
        SELECT * FROM submissions
        WHERE session_id = ? AND canvas_user_id = ?
        LIMIT 1
        """,
        (session_id, canvas_user_id)
      )

  def delete_by_session(self, session_id: int) -> int:
    """
    Delete all submissions for session.

    Args:
      session_id: Session primary key

    Returns:
      Number of submissions deleted
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("DELETE FROM submissions WHERE session_id = ?", (session_id,))
      return cursor.rowcount

  def get_pdf_data(self, submission_id: int) -> Optional[str]:
    """
    Get just PDF data for submission.

    Optimization - don't load full object when only PDF needed.

    Args:
      submission_id: Submission primary key

    Returns:
      Base64-encoded PDF data or None if not found
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "SELECT exam_pdf_data FROM submissions WHERE id = ?",
        (submission_id,)
      )
      row = cursor.fetchone()
      return row["exam_pdf_data"] if row else None

  def get_max_document_id(self, session_id: int) -> int:
    """
    Get highest document_id in session.

    Used to calculate offset for new uploads.

    Args:
      session_id: Session primary key

    Returns:
      Maximum document_id or -1 if no submissions
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "SELECT MAX(document_id) FROM submissions WHERE session_id = ?",
        (session_id,)
      )
      result = cursor.fetchone()[0]
      return result if result is not None else -1

  def count_unmatched(self, session_id: int) -> int:
    """
    Count submissions without Canvas student match.

    Args:
      session_id: Session primary key

    Returns:
      Number of unmatched submissions
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "SELECT COUNT(*) FROM submissions WHERE session_id = ? AND canvas_user_id IS NULL",
        (session_id,)
      )
      return cursor.fetchone()[0]

  def get_student_scores(self, session_id: int) -> List[Dict]:
    """
    Get aggregated scores for all students in a session.

    Returns list of dicts with student_name, canvas_user_id,
    total_problems, graded_problems, total_score, is_complete.

    Args:
      session_id: Session primary key

    Returns:
      List of student score dictionaries
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        """
        SELECT
          s.id,
          s.student_name,
          s.canvas_user_id,
          COUNT(p.id) as total_problems,
          SUM(CASE WHEN p.graded = 1 THEN 1 ELSE 0 END) as graded_problems,
          SUM(CASE WHEN p.graded = 1 THEN p.score ELSE 0 END) as total_score
        FROM submissions s
        LEFT JOIN problems p ON p.submission_id = s.id
        WHERE s.session_id = ?
        GROUP BY s.id
        ORDER BY s.student_name
        """, (session_id,)
      )

      students = []
      for row in cursor.fetchall():
        students.append({
          "student_name": row["student_name"],
          "canvas_user_id": row["canvas_user_id"],
          "total_problems": row["total_problems"],
          "graded_problems": row["graded_problems"],
          "total_score": row["total_score"],
          "is_complete": row["graded_problems"] == row["total_problems"]
        })

      return students

  def get_blank_stats(self, session_id: int) -> List[Dict]:
    """
    Get blank detection statistics for all submissions.

    Used by debug endpoints for analyzing blank detection performance.

    Args:
      session_id: Session primary key

    Returns:
      List of dicts with submission_id, student_name, display_name,
      total_problems, blank_detected, graded_problems, blank_percentage
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT
          s.id as submission_id,
          s.student_name,
          s.display_name,
          COUNT(p.id) as total_problems,
          SUM(CASE WHEN p.is_blank = 1 THEN 1 ELSE 0 END) as blank_detected,
          SUM(CASE WHEN p.graded = 1 THEN 1 ELSE 0 END) as graded_problems,
          CAST(SUM(CASE WHEN p.is_blank = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(p.id) * 100 as blank_percentage
        FROM submissions s
        LEFT JOIN problems p ON p.submission_id = s.id
        WHERE s.session_id = ?
        GROUP BY s.id, s.student_name, s.display_name
        ORDER BY blank_percentage DESC
      """, (session_id,))

      results = []
      for row in cursor.fetchall():
        results.append({
          "submission_id": row["submission_id"],
          "student_name": row["student_name"],
          "display_name": row["display_name"],
          "total_problems": row["total_problems"],
          "blank_detected": row["blank_detected"] or 0,
          "graded_problems": row["graded_problems"] or 0,
          "blank_percentage": row["blank_percentage"] or 0.0
        })

      return results
