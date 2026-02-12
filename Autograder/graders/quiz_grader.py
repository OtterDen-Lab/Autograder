"""
Quiz grader implementation.

Handles grading of Canvas quiz submissions by analyzing student responses
and generating feedback reports.
"""
from typing import Dict, Any

from Autograder.grader import Grader
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback, Submission, QuizSubmission

import logging

log = logging.getLogger(__name__)


@GraderRegistry.register("QuizGrader")
class QuizGrader(Grader):
  """
    Placeholder grader for Canvas quiz submissions.

    Quiz grading is intentionally disabled for now while the pipeline
    is redesigned for robust free-response and partial-credit handling.
    """

  def can_grade_submission(self, submission: Submission) -> bool:
    """
        Quiz grading is disabled.
        """
    return False

  def execute_grading(self, submission: QuizSubmission, *args,
                      **kwargs) -> Dict[str, Any]:
    raise NotImplementedError(
      "Quiz grading is currently disabled. This class is a placeholder.")

  def score_grading(self, execution_results: Dict[str, Any], *args,
                    **kwargs) -> Feedback:
    raise NotImplementedError(
      "Quiz grading is currently disabled. This class is a placeholder.")

  def assignment_needs_preparation(self) -> bool:
    """Quiz grading doesn't require preparation like file-based assignments"""
    return False

  def prepare(self, *args, **kwargs) -> None:
    """No preparation needed for quiz grading"""
    pass

  def finalize(self, *args, **kwargs) -> None:
    """No finalization needed for quiz grading"""
    pass
