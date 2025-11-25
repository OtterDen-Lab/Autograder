"""
Repository for individual problems within submissions.
"""
from typing import Optional, List, Dict
import sqlite3
import json
from datetime import datetime

from .base import BaseRepository
from ..domain.problem import Problem


class ProblemRepository(BaseRepository[Problem]):
  """
  Data access for problems.

  Handles individual problem instances within student submissions.
  Supports bulk operations and grading workflows.
  """

  def _row_to_domain(self, row: sqlite3.Row) -> Problem:
    """Convert database row to Problem domain object."""
    region_coords = None
    if row["region_coords"]:
      try:
        region_coords = json.loads(row["region_coords"])
      except json.JSONDecodeError:
        region_coords = None

    # Parse timestamps
    graded_at = None
    if row["graded_at"]:
      try:
        graded_at = datetime.fromisoformat(row["graded_at"])
      except (ValueError, TypeError):
        pass

    transcription_cached_at = None
    if row["transcription_cached_at"]:
      try:
        transcription_cached_at = datetime.fromisoformat(row["transcription_cached_at"])
      except (ValueError, TypeError):
        pass

    return Problem(
      id=row["id"],
      session_id=row["session_id"],
      submission_id=row["submission_id"],
      problem_number=row["problem_number"],
      score=row["score"],
      feedback=row["feedback"],
      graded=bool(row["graded"]),
      graded_at=graded_at,
      is_blank=bool(row["is_blank"]),
      blank_confidence=row["blank_confidence"] or 0.0,
      blank_method=row["blank_method"],
      blank_reasoning=row["blank_reasoning"],
      max_points=row["max_points"],
      ai_reasoning=row["ai_reasoning"],
      region_coords=region_coords,
      qr_encrypted_data=row["qr_encrypted_data"],
      transcription=row["transcription"],
      transcription_model=row["transcription_model"],
      transcription_cached_at=transcription_cached_at
    )

  def get_by_id(self, problem_id: int) -> Optional[Problem]:
    """
    Get problem by ID.

    Args:
      problem_id: Problem primary key

    Returns:
      Problem or None if not found
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        "SELECT * FROM problems WHERE id = ?",
        (problem_id,)
      )

  def get_by_submission(self, submission_id: int) -> List[Problem]:
    """
    Get all problems for a submission.

    Args:
      submission_id: Submission primary key

    Returns:
      List of Problem objects ordered by problem_number
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        """
        SELECT * FROM problems
        WHERE submission_id = ?
        ORDER BY problem_number
        """,
        (submission_id,)
      )

  def get_by_session_batch(self, session_id: int) -> List[Problem]:
    """
    Get all problems for all submissions in a session.

    More efficient than per-submission queries (avoids N+1).
    Use for export operations.

    Args:
      session_id: Session primary key

    Returns:
      List of Problem objects ordered by submission_id, problem_number
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_all(
        conn,
        """
        SELECT * FROM problems
        WHERE session_id = ?
        ORDER BY submission_id, problem_number
        """,
        (session_id,)
      )

  def create(self, problem: Problem) -> Problem:
    """
    Create single problem.

    Args:
      problem: Problem to create (id will be ignored)

    Returns:
      Problem with id populated
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()

      region_coords_json = None
      if problem.region_coords:
        region_coords_json = json.dumps(problem.region_coords)

      cursor.execute("""
        INSERT INTO problems
        (session_id, submission_id, problem_number, graded,
         is_blank, blank_confidence, blank_method, blank_reasoning,
         max_points, region_coords, qr_encrypted_data,
         score, feedback, graded_at, ai_reasoning,
         transcription, transcription_model, transcription_cached_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """, (
        problem.session_id,
        problem.submission_id,
        problem.problem_number,
        1 if problem.graded else 0,
        1 if problem.is_blank else 0,
        problem.blank_confidence,
        problem.blank_method,
        problem.blank_reasoning,
        problem.max_points,
        region_coords_json,
        problem.qr_encrypted_data,
        problem.score,
        problem.feedback,
        problem.graded_at.isoformat() if problem.graded_at else None,
        problem.ai_reasoning,
        problem.transcription,
        problem.transcription_model,
        problem.transcription_cached_at.isoformat() if problem.transcription_cached_at else None
      ))

      problem_id = cursor.lastrowid
      cursor.execute("SELECT * FROM problems WHERE id = ?", (problem_id,))
      row = cursor.fetchone()
      return self._row_to_domain(row)

  def bulk_create(self, problems: List[Problem]) -> List[Problem]:
    """
    Create multiple problems in one transaction.

    Critical for uploads.py - creates 1000+ problems per upload.
    Must be fast and maintain transaction boundary.

    Args:
      problems: List of Problem objects to create

    Returns:
      List of created Problem objects with IDs
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      created_problems = []

      for prob in problems:
        region_coords_json = None
        if prob.region_coords:
          region_coords_json = json.dumps(prob.region_coords)

        cursor.execute("""
          INSERT INTO problems
          (session_id, submission_id, problem_number, graded,
           is_blank, blank_confidence, blank_method, blank_reasoning,
           max_points, region_coords, qr_encrypted_data,
           score, feedback, graded_at, ai_reasoning,
           transcription, transcription_model, transcription_cached_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
          prob.session_id,
          prob.submission_id,
          prob.problem_number,
          1 if prob.graded else 0,
          1 if prob.is_blank else 0,
          prob.blank_confidence,
          prob.blank_method,
          prob.blank_reasoning,
          prob.max_points,
          region_coords_json,
          prob.qr_encrypted_data,
          prob.score,
          prob.feedback,
          prob.graded_at.isoformat() if prob.graded_at else None,
          prob.ai_reasoning,
          prob.transcription,
          prob.transcription_model,
          prob.transcription_cached_at.isoformat() if prob.transcription_cached_at else None
        ))

        problem_id = cursor.lastrowid
        cursor.execute("SELECT * FROM problems WHERE id = ?", (problem_id,))
        row = cursor.fetchone()
        created_problems.append(self._row_to_domain(row))

      return created_problems

  def get_sample_for_problem_number(self, session_id: int, problem_number: int) -> Optional[Problem]:
    """
    Get a sample problem for a specific problem number.

    Used for AI question extraction - any problem instance will do.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      A sample Problem or None if not found
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        """
        SELECT * FROM problems
        WHERE session_id = ? AND problem_number = ?
        LIMIT 1
        """,
        (session_id, problem_number)
      )

  def get_next_ungraded(self, session_id: int, problem_number: int) -> Optional[Problem]:
    """
    Get next ungraded problem for a specific problem number.

    Orders non-blank before blank, then random.
    Used in grading workflow.

    Args:
      session_id: Session primary key
      problem_number: Problem number to grade

    Returns:
      Next ungraded Problem or None if all graded
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        """
        SELECT * FROM problems
        WHERE session_id = ? AND problem_number = ? AND graded = 0
        ORDER BY is_blank ASC, RANDOM()
        LIMIT 1
        """,
        (session_id, problem_number)
      )

  def get_previous_graded(self, session_id: int, problem_number: int) -> Optional[Problem]:
    """
    Get most recently graded problem for a problem number.

    Used for review functionality.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      Most recently graded Problem or None
    """
    with self._get_connection() as conn:
      return self._execute_and_fetch_one(
        conn,
        """
        SELECT * FROM problems
        WHERE session_id = ? AND problem_number = ? AND graded = 1
        ORDER BY graded_at DESC
        LIMIT 1
        """,
        (session_id, problem_number)
      )

  def update_grade(self, problem_id: int, score: float,
                  feedback: Optional[str] = None,
                  ai_reasoning: Optional[str] = None) -> None:
    """
    Update problem with grade.

    Common operation - called frequently during grading.

    Args:
      problem_id: Problem primary key
      score: Points awarded
      feedback: Optional grader feedback
      ai_reasoning: Optional AI-generated reasoning
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE problems
        SET score = ?, feedback = ?, ai_reasoning = ?,
            graded = 1, graded_at = CURRENT_TIMESTAMP
        WHERE id = ?
      """, (score, feedback, ai_reasoning, problem_id))

  def mark_as_blank(self, problem_id: int, feedback: Optional[str] = None) -> None:
    """
    Mark problem as blank (manual detection).

    Sets score to 0, graded to 1, is_blank to 1, and records manual blank method.

    Args:
      problem_id: Problem primary key
      feedback: Optional grader feedback
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE problems
        SET score = 0,
            feedback = ?,
            graded = 1,
            graded_at = CURRENT_TIMESTAMP,
            is_blank = 1,
            blank_method = 'manual',
            blank_reasoning = 'Manually marked as blank by grader (dash in score field)'
        WHERE id = ?
      """, (feedback, problem_id))

  def update_transcription(self, problem_id: int, transcription: str, model: str) -> None:
    """
    Cache transcription for a problem.

    Args:
      problem_id: Problem primary key
      transcription: Transcribed text
      model: Model name used for transcription
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE problems
        SET transcription = ?, transcription_model = ?, transcription_cached_at = CURRENT_TIMESTAMP
        WHERE id = ?
      """, (transcription, model, problem_id))

  def update_qr_data(self, problem_id: int, max_points: float, encrypted_data: Optional[str] = None) -> None:
    """
    Update problem with QR code data.

    Args:
      problem_id: Problem primary key
      max_points: Maximum points from QR code
      encrypted_data: Encrypted QR data
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE problems
        SET max_points = ?, qr_encrypted_data = ?
        WHERE id = ?
      """, (max_points, encrypted_data, problem_id))

  def get_counts_for_problem_number(self, session_id: int,
                                   problem_number: int) -> Dict[str, int]:
    """
    Get various counts for a problem number.

    Used in problem display to show progress.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      Dict with: total, graded, ungraded_blank, ungraded_nonblank
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT
          COUNT(*) as total,
          SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as graded,
          SUM(CASE WHEN graded = 0 AND is_blank = 1 THEN 1 ELSE 0 END) as ungraded_blank,
          SUM(CASE WHEN graded = 0 AND is_blank = 0 THEN 1 ELSE 0 END) as ungraded_nonblank
        FROM problems
        WHERE session_id = ? AND problem_number = ?
      """, (session_id, problem_number))

      row = cursor.fetchone()
      return {
        "total": row["total"] or 0,
        "graded": row["graded"] or 0,
        "ungraded_blank": row["ungraded_blank"] or 0,
        "ungraded_nonblank": row["ungraded_nonblank"] or 0
      }

  def count_ungraded_for_problem_number(self, session_id: int, problem_number: int) -> int:
    """
    Count ungraded problems for specific problem number.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      Count of ungraded problems
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        """
        SELECT COUNT(*) as count
        FROM problems
        WHERE session_id = ? AND problem_number = ? AND graded = 0
        """,
        (session_id, problem_number)
      )
      return cursor.fetchone()["count"]

  def count_ungraded(self, session_id: int) -> int:
    """
    Count ungraded problems in session.

    Args:
      session_id: Session primary key

    Returns:
      Number of ungraded problems
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute(
        "SELECT COUNT(*) FROM problems WHERE session_id = ? AND graded = 0",
        (session_id,)
      )
      return cursor.fetchone()[0]

  def get_all_for_problem_number(self, session_id: int,
                                 problem_number: int,
                                 graded_only: bool = False) -> List[Problem]:
    """
    Get all instances of a specific problem number.

    Used for statistics and AI grading.

    Args:
      session_id: Session primary key
      problem_number: Problem number
      graded_only: Only return graded problems

    Returns:
      List of Problem objects
    """
    with self._get_connection() as conn:
      if graded_only:
        return self._execute_and_fetch_all(
          conn,
          """
          SELECT * FROM problems
          WHERE session_id = ? AND problem_number = ? AND graded = 1
          ORDER BY submission_id
          """,
          (session_id, problem_number)
        )
      else:
        return self._execute_and_fetch_all(
          conn,
          """
          SELECT * FROM problems
          WHERE session_id = ? AND problem_number = ?
          ORDER BY submission_id
          """,
          (session_id, problem_number)
        )

  def get_graded_with_student_names(self, session_id: int, problem_number: int,
                                     limit: int = 20, offset: int = 0) -> tuple[List[dict], int]:
    """
    Get graded problems with student names for review.

    Returns problems joined with submission data for display.

    Args:
      session_id: Session primary key
      problem_number: Problem number
      limit: Max number of problems to return
      offset: Pagination offset

    Returns:
      Tuple of (list of problem dicts with student_name, total count)
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()

      # Get total count
      cursor.execute("""
        SELECT COUNT(*) as count
        FROM problems
        WHERE session_id = ? AND problem_number = ? AND graded = 1
      """, (session_id, problem_number))

      total_count = cursor.fetchone()["count"]

      if total_count == 0:
        return ([], 0)

      # Get graded problems with student names
      cursor.execute("""
        SELECT p.*, s.student_name
        FROM problems p
        LEFT JOIN submissions s ON p.submission_id = s.id
        WHERE p.session_id = ? AND p.problem_number = ? AND p.graded = 1
        ORDER BY p.graded_at DESC
        LIMIT ? OFFSET ?
      """, (session_id, problem_number, limit, offset))

      problems = []
      for row in cursor.fetchall():
        problems.append(dict(row))

      return (problems, total_count)

  def delete_by_session(self, session_id: int) -> int:
    """
    Delete all problems for session.

    Args:
      session_id: Session primary key

    Returns:
      Number of problems deleted
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("DELETE FROM problems WHERE session_id = ?", (session_id,))
      return cursor.rowcount

  def get_distinct_problem_numbers(self, session_id: int) -> List[int]:
    """
    Get list of all problem numbers in session.

    Args:
      session_id: Session primary key

    Returns:
      Sorted list of problem numbers
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT DISTINCT problem_number
        FROM problems
        WHERE session_id = ?
        ORDER BY problem_number
      """, (session_id,))
      return [row["problem_number"] for row in cursor.fetchall()]

  def update_max_points_bulk(self, session_id: int, problem_number: int,
                            max_points: float) -> int:
    """
    Update max_points for all problems with given problem_number.

    Used when metadata max_points is updated.

    Args:
      session_id: Session primary key
      problem_number: Problem number
      max_points: New max points value

    Returns:
      Number of problems updated
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE problems
        SET max_points = ?
        WHERE session_id = ? AND problem_number = ?
      """, (max_points, session_id, problem_number))
      return cursor.rowcount

  def get_session_overall_stats(self, session_id: int) -> Dict:
    """
    Get overall statistics for a session.

    Returns total_submissions, total_problems, problems_graded.

    Args:
      session_id: Session primary key

    Returns:
      Dict with overall statistics
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT
          COUNT(DISTINCT submission_id) as total_submissions,
          COUNT(*) as total_problems,
          SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as problems_graded
        FROM problems
        WHERE session_id = ?
      """, (session_id,))

      row = cursor.fetchone()
      return {
        "total_submissions": row["total_submissions"] or 0,
        "total_problems": row["total_problems"] or 0,
        "problems_graded": row["problems_graded"] or 0
      }

  def get_problem_scores_and_blanks(self, session_id: int, problem_number: int) -> tuple[List[float], int]:
    """
    Get scores and blank count for a specific problem.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      Tuple of (list of scores, num_blank)
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT score, is_blank
        FROM problems
        WHERE session_id = ? AND problem_number = ? AND graded = 1
      """, (session_id, problem_number))

      results = cursor.fetchall()
      scores = [row["score"] for row in results if row["score"] is not None]
      num_blank = sum(1 for row in results if row["is_blank"])
      return (scores, num_blank)

  def get_blank_distribution(self, session_id: int) -> Dict[int, Dict]:
    """
    Get blank detection distribution across all problems.

    Used by debug endpoints to see how many submissions were marked blank
    for each problem number.

    Args:
      session_id: Session primary key

    Returns:
      Dict mapping problem_number to dict with: total, blank, percentage
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT
          p.problem_number,
          COUNT(*) as total_submissions,
          SUM(CASE WHEN p.is_blank = 1 THEN 1 ELSE 0 END) as blank_count,
          CAST(SUM(CASE WHEN p.is_blank = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) * 100 as blank_percentage
        FROM problems p
        JOIN submissions s ON p.submission_id = s.id
        WHERE s.session_id = ?
        GROUP BY p.problem_number
        ORDER BY p.problem_number
      """, (session_id,))

      distribution = {}
      for row in cursor.fetchall():
        distribution[row["problem_number"]] = {
          "total": row["total_submissions"],
          "blank": row["blank_count"] or 0,
          "percentage": row["blank_percentage"] or 0.0
        }

      return distribution

  def get_problems_with_submission_data(self, session_id: int, problem_number: int) -> List[Dict]:
    """
    Get all problems for a specific problem number with submission data.

    Used by debug endpoints for detailed analysis with submission info.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      List of dicts with problem and submission data
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT
          p.id as problem_id,
          p.submission_id,
          p.problem_number,
          p.region_coords,
          p.is_blank,
          p.blank_confidence,
          p.blank_method,
          p.blank_reasoning,
          p.score,
          p.feedback,
          p.graded,
          s.exam_pdf_data,
          s.student_name,
          s.display_name
        FROM problems p
        JOIN submissions s ON p.submission_id = s.id
        WHERE s.session_id = ? AND p.problem_number = ?
        ORDER BY p.id
      """, (session_id, problem_number))

      results = []
      for row in cursor.fetchall():
        results.append({
          "problem_id": row["problem_id"],
          "submission_id": row["submission_id"],
          "problem_number": row["problem_number"],
          "region_coords": row["region_coords"],
          "is_blank": row["is_blank"],
          "blank_confidence": row["blank_confidence"],
          "blank_method": row["blank_method"],
          "blank_reasoning": row["blank_reasoning"],
          "score": row["score"],
          "feedback": row["feedback"],
          "graded": row["graded"],
          "exam_pdf_data": row["exam_pdf_data"],
          "student_name": row["student_name"],
          "display_name": row["display_name"]
        })

      return results

  def clear_blank_flags_for_session(self, session_id: int) -> int:
    """
    Clear all is_blank flags for ungraded problems in a session.

    Used by debug endpoints for testing blank detection from scratch.

    Args:
      session_id: Session primary key

    Returns:
      Number of problems updated
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE problems
        SET is_blank = 0,
            blank_confidence = NULL,
            blank_method = NULL,
            blank_reasoning = NULL
        WHERE submission_id IN (
          SELECT id FROM submissions WHERE session_id = ?
        ) AND graded = 0
      """, (session_id,))

      return cursor.rowcount

  def get_grading_examples(self, session_id: int, problem_number: int, limit: int = 3) -> List[Dict]:
    """
    Get graded problems for few-shot learning examples.

    Returns random sample of graded, non-blank problems with feedback.
    Used by AI grading service.

    Args:
      session_id: Session primary key
      problem_number: Problem number
      limit: Maximum number of examples

    Returns:
      List of dicts with problem fields
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT p.id, p.image_data, p.region_coords, p.submission_id, p.score, p.feedback
        FROM problems p
        WHERE p.session_id = ? AND p.problem_number = ? AND p.graded = 1
              AND p.is_blank = 0 AND p.feedback IS NOT NULL AND p.feedback != ''
        ORDER BY RANDOM()
        LIMIT ?
      """, (session_id, problem_number, limit))

      results = []
      for row in cursor.fetchall():
        results.append({
          "id": row["id"],
          "image_data": row["image_data"],
          "region_coords": row["region_coords"],
          "submission_id": row["submission_id"],
          "score": row["score"],
          "feedback": row["feedback"]
        })

      return results

  def get_ungraded_for_problem_number(self, session_id: int, problem_number: int) -> List[Dict]:
    """
    Get all ungraded problems for a specific problem number.

    Used by AI autograding service.

    Args:
      session_id: Session primary key
      problem_number: Problem number

    Returns:
      List of dicts with problem fields (id, image_data, region_coords, submission_id, is_blank)
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        SELECT id, image_data, region_coords, submission_id, is_blank
        FROM problems
        WHERE session_id = ? AND problem_number = ? AND graded = 0
        ORDER BY id
      """, (session_id, problem_number))

      results = []
      for row in cursor.fetchall():
        results.append({
          "id": row["id"],
          "image_data": row["image_data"],
          "region_coords": row["region_coords"],
          "submission_id": row["submission_id"],
          "is_blank": row["is_blank"]
        })

      return results

  def update_ai_grade(self, problem_id: int, score: float, feedback: str) -> None:
    """
    Update problem with AI-suggested grade.

    Sets score and feedback but leaves graded=0 for instructor review.
    Used by AI autograding service.

    Args:
      problem_id: Problem primary key
      score: Suggested score
      feedback: AI-generated feedback
    """
    with self._get_connection() as conn:
      cursor = conn.cursor()
      cursor.execute("""
        UPDATE problems
        SET score = ?, feedback = ?, graded = 0
        WHERE id = ?
      """, (score, feedback, problem_id))
