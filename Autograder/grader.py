#!env python
from __future__ import annotations

import abc

from typing import List, Tuple, Optional

from Autograder.assignment import Assignment
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback, Submission

# Import all grader implementations to ensure they're registered
try:
  import Autograder.graders
except ImportError:
  # Graders package may not be available in all environments
  pass


import logging
log = logging.getLogger(__name__)


@GraderRegistry.register("Dummy")
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
    self.assignment_identifier = (kwargs.get('assignment_path') or 
                                 kwargs.get('repo_path') or 
                                 kwargs.get('assignment_name') or 
                                 'unknown')

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
    
    log.info(f"[{assignment_id}] Starting to grade {total_submissions} submissions")
    
    for i, submission in enumerate(assignment.submissions, 1):
      # Get student identifier for logging (prefer name, fallback to user_id)
      
      log.info(f"[{assignment_id}] Grading submission {i}/{total_submissions} (Student: {submission.student.name})")
      
      if not submission.files:
        submission.feedback = Feedback(0.0, "Assignment submission files missing")
        continue
      if submission.status == Submission.Status.GRADED and not kwargs.get('do_regrade', False):
        continue
      
      submission.feedback = self.grade_submission(submission, **kwargs)
      
    log.info(f"[{assignment_id}] Finished grading all {total_submissions} submissions")

  def grade_submission(self, submission: Submission, *args, **kwargs) -> Feedback:
    """
    Takes in a submission, grades it, and returns back a Feedback
    :param submission: A Submission object that may have files associated with it
    :param kwargs:
    :return: returns a Feedback object for the submission
    """
    execution_results = self.execute_grading(*args, **kwargs)
    return self.score_grading(execution_results, *args, **kwargs)
  
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
  
  def assignment_needs_preparation(self) -> bool:
    return True

  def prepare(self, *args, **kwargs) -> None:
    """
    Anything that is needed to take the assignment and prepare it for grading.
    For example, making a CSV file from the submissions for manual grading
    :param args:
    :param kwargs:
    :return:
    """
  
  def finalize(self, *args, **kwargs) -> None:
    """
    anything that is needed to connect the grades/feedback to the submissions after grading.
    For example, loading up the CSV and connecting grades to the submissions
    :param args:
    :param kwargs:
    :return:
    """
  
  def cleanup(self) -> None:
    pass

