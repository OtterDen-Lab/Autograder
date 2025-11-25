"""
Student name matching service.
"""
from typing import List, Tuple
import logging

log = logging.getLogger(__name__)


class NameMatcher:
  """
    Service for matching extracted names to Canvas students.
    """

  def __init__(self, similarity_threshold: float = 0.95):
    """
        Initialize name matcher.

        Args:
            similarity_threshold: Minimum similarity score (0-1) for auto-match
        """
    self.similarity_threshold = similarity_threshold

  def auto_match(self, submissions: List[dict],
                 canvas_students: List[dict]) -> Tuple[List[dict], List[dict]]:
    """
        Automatically match submissions to students where confidence is high.

        Args:
            submissions: List of submission dicts with approximate_name
            canvas_students: List of student dicts with name and user_id

        Returns:
            Tuple of (matched_submissions, unmatched_submissions)
        """
    # TODO: Extract from Assignment__Exam.match_students_to_submissions()
    # Use fuzzywuzzy for similarity scoring

    matched = []
    unmatched = []

    for submission in submissions:
      best_match = self._find_best_match(submission["approximate_name"],
                                         canvas_students)

      if best_match and best_match["score"] >= self.similarity_threshold:
        submission["canvas_user_id"] = best_match["user_id"]
        submission["student_name"] = best_match["name"]
        matched.append(submission)
      else:
        unmatched.append(submission)

    log.info(f"Auto-matched {len(matched)}/{len(submissions)} submissions")
    return matched, unmatched

  def _find_best_match(self, name: str, students: List[dict]) -> dict:
    """
        Find best matching student for a given name.

        Args:
            name: Name to match
            students: List of student dicts

        Returns:
            Dict with best match and score, or None
        """
    # TODO: Implement fuzzy matching
    # Use fuzzywuzzy.fuzz.ratio()

    return None

  def manual_match(self, submission_id: int, canvas_user_id: int) -> dict:
    """
        Manually match a submission to a Canvas student.

        Args:
            submission_id: ID of submission
            canvas_user_id: Canvas user ID to match to

        Returns:
            Updated submission dict
        """
    # This will be called from the API when user manually matches
    return {
      "submission_id": submission_id,
      "canvas_user_id": canvas_user_id,
      "matched": True
    }
