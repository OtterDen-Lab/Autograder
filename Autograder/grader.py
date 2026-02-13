#!env python
from __future__ import annotations

import abc

from typing import List, Tuple, Optional

from Autograder.assignment import Assignment
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback, Submission, FileSubmission, TextSubmission

import logging

log = logging.getLogger(__name__)


class Grader(abc.ABC):
  """
  Base abstract class for all graders.

  Provides the framework for grading assignments by processing submissions
  and generating feedback.
  """

  def __init__(self, *args, **kwargs):
    super().__init__()
    self.ready_to_finalize = True
    # Store assignment identifier for logging (prefer repo_path, then assignment_name, then assignment_path)
    self.assignment_identifier = (kwargs.get('assignment_path')
                                  or kwargs.get('repo_path')
                                  or kwargs.get('assignment_name')
                                  or 'unknown')

    # Store Slack configuration from kwargs (for error reporting)
    self.slack_channel = kwargs.get('slack_channel')
    self.slack_webhook = kwargs.get('slack_webhook')
    self.slack_token = kwargs.get('slack_token')
    self.report_errors = kwargs.get('report_errors', True)

  def grade_assignment(self, assignment: Assignment, *args, **kwargs) -> None:
    """
    Takes an assignment and walks through its submissions and grades each.
    :param assignment: Takes in an assignment.Assignment object to grade
    :param kwargs: Additional arguments including:
                   - do_regrade: If True, regrade already-graded submissions
                   - merge_only: If True, only merge results without grading
    :return:
    """
    total_submissions = len(assignment.submissions)
    assignment_id = self.assignment_identifier

    log.info(
      f"[{assignment_id}] Starting to grade {total_submissions} submissions")
    reveal_identity = kwargs.get("reveal_identity", False)

    for i, submission in enumerate(assignment.submissions, 1):
      student_name = getattr(getattr(submission, "student", None), "name",
                             "Unknown Student")
      user_id = getattr(getattr(submission, "student", None), "user_id", None)
      if reveal_identity and user_id is not None and str(user_id) not in str(
          student_name):
        student_name = f"{student_name} [canvas_user_id={user_id}]"
      log.info(
        f"[{assignment_id}] Grading submission {i}/{total_submissions} (Student: {student_name})"
      )

      try:
        # Check if this grader can handle this submission type
        if not self.can_grade_submission(submission):
          submission.feedback = Feedback(
            0.0,
            f"Cannot grade {type(submission).__name__} with {type(self).__name__}"
          )
          continue

        if submission.status == Submission.Status.GRADED and not kwargs.get(
            'do_regrade', False):
          continue

        submission.feedback = self.grade_submission(submission, **kwargs)
      except Exception as e:
        log.exception(
          f"[{assignment_id}] Failed to grade submission for {student_name}: {e}"
        )
        submission.feedback = Feedback(
          0.0,
          "Autograder internal error while grading this submission.")

    log.info(
      f"[{assignment.lms_assignment.canvas_course.name} {assignment_id}] Finished grading all {total_submissions} submissions"
    )

  def grade_submission(self, submission: Submission, *args,
                       **kwargs) -> Feedback:
    """
    Takes in a submission, grades it, and returns back a Feedback
    :param submission: A Submission object that may have files associated with it
    :param kwargs:
    :return: returns a Feedback object for the submission
    """
    grading_kwargs = dict(kwargs)
    # Ensure graders receive the concrete submission without requiring global state.
    grading_kwargs.setdefault("submission", submission)
    execution_results = self.execute_grading(*args, **grading_kwargs)
    return self.score_grading(execution_results, *args, **grading_kwargs)

  @abc.abstractmethod
  def execute_grading(self, *args, **kwargs) -> any:
    """
    Implements the steps to actually execute the grading, such as running a make command.
    :param args:
    :param kwargs:
    :return:
    """
    pass

  @abc.abstractmethod
  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    """
    Scores the grading based on execution results, such as stdout or stderr, but can also perform other actions
    :param execution_results:
    :param args:
    :param kwargs:
    :return:
    """
    pass

  @abc.abstractmethod
  def can_grade_submission(self, submission: Submission) -> bool:
    """
    Check if this grader can handle the given submission type.
    Subclasses must override this to specify their supported submission types.

    :param submission: The submission to check
    :return: True if this grader can grade the submission, False otherwise
    """
    pass

  def assignment_needs_preparation(self) -> bool:
    return True

  def prepare(self, *args, **kwargs) -> None:
    """
    Anything that is needed to take the assignment and prepare it for grading.
    For example, building intermediate artifacts from submissions before grading
    :param args:
    :param kwargs:
    :return:
    """

  def finalize(self, *args, **kwargs) -> None:
    """
    anything that is needed to connect the grades/feedback to the submissions after grading.
    For example, reconciling intermediate grading artifacts back to submissions
    :param args:
    :param kwargs:
    :return:
    """

  def cleanup(self) -> None:
    pass

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self.cleanup()
    return False


@GraderRegistry.register("Dummy")
class FileBasedGrader(Grader):
  """
  Base class for graders that work with file submissions (e.g., programming assignments).
  This class applies file-specific validation while preserving the base grading flow.
  """

  def can_grade_submission(self, submission: Submission) -> bool:
    """
    File-based graders can only grade FileSubmission objects that have files.
    """
    return isinstance(submission, FileSubmission) and bool(submission.files)

  def grade_assignment(self, assignment: Assignment, *args, **kwargs) -> None:
    """
    Override to add file-specific error messages while maintaining original logic.
    """
    total_submissions = len(assignment.submissions)
    assignment_id = self.assignment_identifier

    log.info(
      f"[{assignment_id}] Starting to grade {total_submissions} submissions")
    reveal_identity = kwargs.get("reveal_identity", False)

    for i, submission in enumerate(assignment.submissions, 1):
      student_name = getattr(getattr(submission, "student", None), "name",
                             "Unknown Student")
      user_id = getattr(getattr(submission, "student", None), "user_id", None)
      if reveal_identity and user_id is not None and str(user_id) not in str(
          student_name):
        student_name = f"{student_name} [canvas_user_id={user_id}]"
      log.info(
        f"[{assignment_id}] Grading submission {i}/{total_submissions} (Student: {student_name})"
      )

      try:
        # Check if this grader can handle this submission type
        if not self.can_grade_submission(submission):
          if isinstance(submission, FileSubmission) and not submission.files:
            submission.feedback = Feedback(
              0.0, "Assignment submission files missing")
          else:
            submission.feedback = Feedback(
              0.0,
              f"Cannot grade {type(submission).__name__} with {type(self).__name__}"
            )
          continue

        if submission.status == Submission.Status.GRADED and not kwargs.get(
            'do_regrade', False):
          continue

        submission.feedback = self.grade_submission(submission, **kwargs)
      except Exception as e:
        log.exception(
          f"[{assignment_id}] Failed to grade submission for {student_name}: {e}"
        )
        submission.feedback = Feedback(
          0.0,
          "Autograder internal error while grading this submission.")

    log.info(
      f"[{assignment.lms_assignment.canvas_course.name} {assignment_id}] Finished grading all {total_submissions} submissions"
    )
