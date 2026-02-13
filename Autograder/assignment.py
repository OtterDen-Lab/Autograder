#!env python
from __future__ import annotations

import math
import re
import threading
import tempfile
from datetime import datetime
from typing import List, Dict, Any
import abc
import os
import json
from Autograder.registry import AssignmentRegistry
from lms_interface.canvas_interface import CanvasAssignment
from lms_interface.classes import Student, Submission

import logging

log = logging.getLogger(__name__)

class Assignment(abc.ABC):
  """
  Class to represent an assignment and act as an abstract base class for other classes.  Will be passed to a grader.
  Two functions need to be overriden for child classes:
  1. prepare : prepares files for grading by downloading, anonymizing, etc.
  2. finalize : combines parts of grading as necessary
  """

  def __init__(self,
               lms_assignment: CanvasAssignment,
               grading_root_dir=None,
               *args,
               **kwargs):
    self.lms_assignment = lms_assignment
    self.grading_root_dir = grading_root_dir
    self.submissions: List[Submission] = []
    self.original_dir = None
    self._temp_dir = None
    self.canvas_points = kwargs.get(
      'canvas_points', None)  # Override for Canvas assignment points

  def __enter__(self) -> Assignment:
    """Enables use as a context manager (e.g. `with [Assignment]`) by managing working directory"""
    if self.grading_root_dir is None:
      self._temp_dir = tempfile.TemporaryDirectory()
      self.grading_root_dir = self._temp_dir.name
      log.debug(f"Created grading temp dir: {self.grading_root_dir}")

    # Only change working directory if we're in the main thread to avoid race conditions
    if threading.current_thread() == threading.main_thread():
      self.original_dir = os.getcwd()
      os.chdir(self.grading_root_dir)
    else:
      # In worker threads, don't change the working directory
      self.original_dir = None
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    """Enables use as a context manager (e.g. `with [Assignment]`) by managing working directory"""
    # Only restore working directory if we changed it
    if self.original_dir is not None:
      os.chdir(self.original_dir)
    if self._temp_dir is not None:
      self._temp_dir.cleanup()
      self._temp_dir = None

  @abc.abstractmethod
  def prepare(self, *args, **kwargs) -> None:
    """
    This function is intended to set up any directories or files as appropriate for grading.
    It should take in some sort of input and prepare Submissions to be passed to a grader object.
    :return: None
    """
    pass

  def finalize(self, *args, **kwargs) -> Dict[str, Any]:
    """
    This function is intended to finalize any grading.  This could be reloading the grading CSV and matching names,
    or could just be a noop.
    :param args:
    :param kwargs:
    :return:
    """

    # If we are only merging then we should exit right here
    if kwargs.get("merge_only", False):
      return {
        "push_enabled": False,
        "merge_only": True,
        "push_attempted": 0,
        "push_succeeded": 0,
        "push_failed": 0,
        "push_skipped": 0,
        "push_failed_students": [],
      }

    push_enabled = bool(kwargs.get("push", False))
    idempotency_key = kwargs.get("idempotency_key")
    idempotency_state_dir = kwargs.get("idempotency_state_dir")
    processed_user_ids = None
    processed_dirty = False
    if push_enabled and idempotency_key:
      processed_user_ids = self._load_idempotency_user_ids(
        idempotency_key, idempotency_state_dir)
      log.info(
        f"Idempotency tracking enabled for assignment {self.lms_assignment.name}: {len(processed_user_ids)} previously pushed submission(s)"
      )

    log.debug("Pushing")
    push_attempted = 0
    push_succeeded = 0
    push_failed = 0
    push_skipped = 0
    push_failed_students = []
    for submission in self.submissions:
      user_id = submission.student.user_id
      if push_enabled and processed_user_ids is not None and user_id in processed_user_ids:
        push_skipped += 1
        log.info(
          f"Skipping push for {submission.student.name} (canvas_user_id={user_id}) due to idempotency key"
        )
        continue

      # Handle record retention before pushing to LMS
      if kwargs.get("record_retention", False):
        self._save_feedback_record(submission.student,
                                   submission.feedback.comments,
                                   kwargs.get("records_dir"),
                                   self.lms_assignment.name)

      if push_enabled:
        log.info(f"Pushing feedback for: {submission}")
        push_attempted += 1
        try:
          # Scale the score for Canvas submission
          scaled_score = self.scale_score_for_canvas(
            submission.feedback.percentage_score)
          pushed = self.lms_assignment.push_feedback(
            score=scaled_score,
            comments=submission.feedback.comments,
            attachments=submission.feedback.attachments,
            user_id=user_id,
            keep_previous_best=True,
            clobber_feedback=False)
        except Exception as e:
          push_failed += 1
          push_failed_students.append(str(submission.student.name))
          log.exception(
            f"Failed to push feedback for {submission.student.name} (canvas_user_id={user_id}): {e}. Continuing to next submission."
          )
          continue

        if pushed:
          push_succeeded += 1
        else:
          push_failed += 1
          push_failed_students.append(str(submission.student.name))
          log.warning(
            f"Push feedback returned unsuccessful for {submission.student.name} (canvas_user_id={user_id}). Continuing to next submission."
          )

        if pushed and processed_user_ids is not None:
          processed_user_ids.add(user_id)
          processed_dirty = True

    if push_enabled:
      log.info(
        f"Push summary for {self.lms_assignment.name}: attempted={push_attempted}, succeeded={push_succeeded}, failed={push_failed}, skipped={push_skipped}"
      )

    if processed_user_ids is not None and processed_dirty:
      self._save_idempotency_user_ids(
        idempotency_key, idempotency_state_dir, processed_user_ids)

    return {
      "push_enabled": push_enabled,
      "merge_only": False,
      "push_attempted": push_attempted,
      "push_succeeded": push_succeeded,
      "push_failed": push_failed,
      "push_skipped": push_skipped,
      "push_failed_students": push_failed_students,
    }

  def _save_feedback_record(self, student: Student, comments: str,
                            records_dir: str, assignment_name: str) -> None:
    """
    Save feedback to records directory for record retention.
    
    Args:
        student: Student object
        comments: Feedback comments to save
        records_dir: Directory path where records should be saved
        assignment_name: Name of the assignment
    """
    try:
      # Sanitize student name for filename (remove/replace unsafe characters)
      student_name = re.sub(r'[^\w\-_.]', '', student.name.replace(' ', '_'))

      # Sanitize assignment name for filename (remove/replace unsafe characters)
      assignment_safe = re.sub(r'[^\w\-_.]', '',
                               assignment_name.replace(' ', '_'))

      # Create timestamp
      timestamp = datetime.now().strftime("%Y-%b-%d_%H%M")

      # Ensure records directory exists
      if not os.path.exists(records_dir):
        os.makedirs(records_dir)
        log.info(f"Created records directory: {records_dir}")

      # Create filename: [timestamp].[assignment_name].[student_name].log
      filename = f"{timestamp}.{assignment_safe}.{student_name}.log"
      filepath = os.path.join(records_dir, filename)

      # Write feedback to file
      with open(filepath, 'w', encoding='utf-8') as f:
        f.write(comments)

      log.info(f"Saved feedback record: {filepath}")

    except Exception as e:
      log.error(
        f"Failed to save feedback record for student {student.name}: {e}")

  def _idempotency_state_path(self, idempotency_key: str,
                              idempotency_state_dir: str | None) -> str:
    key_safe = re.sub(r'[^A-Za-z0-9._-]', '_', idempotency_key)
    state_dir = idempotency_state_dir or "~/.autograder/idempotency"
    state_dir = os.path.abspath(os.path.expanduser(state_dir))

    course = getattr(self.lms_assignment, "canvas_course", None)
    course_id = getattr(course, "id", "unknown_course")
    assignment_id = getattr(self.lms_assignment, "id", "unknown_assignment")
    filename = f"{key_safe}.course_{course_id}.assignment_{assignment_id}.json"
    return os.path.join(state_dir, filename)

  def _load_idempotency_user_ids(self, idempotency_key: str,
                                 idempotency_state_dir: str | None) -> set[int]:
    path = self._idempotency_state_path(idempotency_key, idempotency_state_dir)
    if not os.path.exists(path):
      return set()

    try:
      with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
      user_ids = payload.get("user_ids", [])
      return {int(u) for u in user_ids}
    except Exception as e:
      log.warning(
        f"Failed to load idempotency state from {path}: {e}. Continuing without prior state.")
      return set()

  def _save_idempotency_user_ids(self, idempotency_key: str,
                                 idempotency_state_dir: str | None,
                                 user_ids: set[int]) -> None:
    path = self._idempotency_state_path(idempotency_key, idempotency_state_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"user_ids": sorted(user_ids)}
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
      json.dump(payload, f, indent=2)
    os.replace(tmp_path, path)

  def scale_score_for_canvas(self, percentage_score: float) -> float:
    """
    Scale a percentage score to match Canvas assignment points.

    This method assumes ALL scores coming from graders are percentages (0-100+).
    It scales them to the Canvas assignment's point value.

    Prioritizes:
    1. Explicit canvas_points parameter from YAML config
    2. Canvas assignment's points_possible if available
    3. Percentage score as-is if no scaling info available (fallback)

    Args:
        percentage_score: The percentage score from the grader (0-100+, where 100 = full credit)

    Returns:
        Scaled score for Canvas submission in Canvas points
    """
    try:
      # Determine Canvas points to use (in priority order)
      canvas_points_possible = None

      # 1. Use explicit canvas_points parameter from YAML config
      if self.canvas_points is not None:
        canvas_points_possible = float(self.canvas_points)
        log.info(
          f"Using explicit canvas_points from config: {canvas_points_possible}"
        )

      # 2. Try to get points_possible from Canvas assignment
      elif hasattr(self.lms_assignment, 'points_possible'
                   ) and self.lms_assignment.points_possible is not None:
        canvas_points_possible = float(self.lms_assignment.points_possible)
        log.info(
          f"Using Canvas assignment points_possible: {canvas_points_possible}")

      # Scale percentage to Canvas points
      if canvas_points_possible is not None:
        # Convert percentage to decimal and multiply by Canvas points
        # Example: 101% on 80-point assignment = 1.01 * 80 = 80.8 points
        percentage_decimal = percentage_score / 100.0
        scaled_score = percentage_decimal * canvas_points_possible
        log.info(
          f"Scaled score {percentage_score:.2f}% to {scaled_score:.2f}/{canvas_points_possible} Canvas points"
        )
        return scaled_score
      else:
        # Fallback: no Canvas points info available, pass through percentage as-is
        log.info(
          f"Using percentage score as-is: {percentage_score:.2f} (no Canvas points info available)"
        )
        return percentage_score

    except Exception as e:
      log.warning(
        f"Failed to scale score for Canvas: {e}. Using percentage score as-is: {percentage_score}"
      )
      return percentage_score


@AssignmentRegistry.register("ProgrammingAssignment")
class Assignment__ProgrammingAssignment(Assignment):
  """
  Assignment for programming assignment grading, where prepare will download files and finalize will upload feedback.
  Will hopefully be run automatically.
  """
  
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

  def prepare(self,
              *args,
              limit=None,
              do_regrade=False,
              only_include_latest=True,
              **kwargs):

    # Steps:
    #  1. Get the submissions
    #  2. Filter out submissions we don't want
    #  3. possibly download proactively
    log.info(
      f"Preparing assignment with do_regrade={do_regrade}, limit={limit}")
    self.submissions = self.lms_assignment.get_submissions(
      limit=(None if not do_regrade else limit), **kwargs)
    log.info(f"Retrieved {len(self.submissions)} total submissions from LMS")

    if not do_regrade:
      ungraded_before = len(self.submissions)
      self.submissions = list(
        filter(lambda s: s.status == Submission.Status.UNGRADED,
               self.submissions))
      log.info(
        f"Filtered to {len(self.submissions)} ungraded submissions (was {ungraded_before})"
      )
    else:
      log.info("Regrade mode: processing all submissions regardless of status")

    log.info(f"Total students to grade: {len(self.submissions)}")
    if limit is not None:
      log.warning(f"Limiting to {limit} students")
      self.submissions = self.submissions[:limit]
    for i, submission in enumerate(self.submissions):
      try:
        log.debug(
          f"{i+1 : 0{math.ceil(math.log10(len(self.submissions)))}} : {submission.student.name} -> files: {[f.name for f in submission.files]}"
        )
      except AttributeError as e:
        log.warning(
          f"Failed to log submission info for {submission.student.name}: missing files attribute"
        )
        log.debug(f"AttributeError details: {e}")
      except Exception as e:
        log.error(
          f"Unexpected error logging submission info for {submission.student.name}: {e}"
        )

  def finalize(self, *args, **kwargs):
    return super().finalize(*args, **kwargs)


@AssignmentRegistry.register("TextAssignment")
class Assignment_TextAssignment(Assignment):
  """
  Assignment for text-based learning log submissions.
  Handles Canvas text submissions where students submit reflective writing.
  """

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.submission_data = []

  def prepare(self, limit=None, do_regrade=False, test=False, **kwargs):
    """
    Prepare text submissions by fetching them from Canvas.

    Args:
        limit: Maximum number of submissions to process
        do_regrade: Whether to regrade existing submissions
        test: Whether to only include test student submissions
        **kwargs: Additional arguments including:
                  - grade_after_lock_date: If True, skip preparation if assignment is not locked
    """
    log.info(
      f"Preparing text assignment with do_regrade={do_regrade}, limit={limit}, test={test}"
    )

    # Check if we should wait for lock date before grading (BEFORE any Canvas API calls)
    grade_after_lock_date = kwargs.get('grade_after_lock_date', False)
    if grade_after_lock_date:
      if not self._is_assignment_locked():
        log.info(
          "Assignment is not ready for grading - skipping preparation and all Canvas API calls"
        )
        # Set empty submissions list to skip grading
        self.submissions = []
        self.submission_data = []
        return

    # Get submissions from Canvas
    self.submissions = self.lms_assignment.get_submissions(
      limit=(None if not do_regrade else limit), test=test, **kwargs)
    log.info(f"Retrieved {len(self.submissions)} total submissions from LMS")

    # Filter for ungraded submissions if not regrading
    if not do_regrade:
      ungraded_before = len(self.submissions)
      self.submissions = list(
        filter(lambda s: s.status == Submission.Status.UNGRADED,
               self.submissions))
      log.info(
        f"Filtered to {len(self.submissions)} ungraded submissions (was {ungraded_before})"
      )
    else:
      log.info("Regrade mode: processing all submissions regardless of status")

    # Apply limit if specified
    if limit is not None:
      log.warning(f"Limiting to {limit} students")
      self.submissions = self.submissions[:limit]

    # Process and structure the submission data
    self.submission_data = []
    for i, submission in enumerate(self.submissions):
      # Extract text content from submission
      if hasattr(submission, 'submission_text'):
        # If it's a list, join without extra spaces (newlines preserve natural word boundaries)
        if isinstance(submission.submission_text, list):
          submission_text = '\n'.join(submission.submission_text)
        else:
          submission_text = str(submission.submission_text)
      else:
        submission_text = ""

      word_count = len(submission_text.split()) if submission_text else 0

      self.submission_data.append({
        'student_id': submission.student.user_id,
        'student_name': submission.student.name,
        'text': submission_text,
        'word_count': word_count,
        'submission_obj': submission  # Keep reference to original submission
      })

      log.debug(f"{i+1} : {submission.student.name} -> {word_count} words")

    log.info(
      f"Prepared {len(self.submission_data)} text submissions for grading")

  def _is_assignment_locked(self) -> bool:
    """
    Check if the assignment is fully locked (past both due date and lock date).

    For learning logs, we want to ensure:
    1. Students have submitted their work (due_at passed)
    2. No more submissions can be made (lock_at passed)

    Returns:
        True if assignment is fully locked (past both due_at and lock_at), False otherwise
    """
    from datetime import datetime, timezone
    import dateutil.parser

    try:
      # Get current time in UTC
      now = datetime.now(timezone.utc)

      # Check due_at date
      due_passed = True  # Default to True if no due date
      if hasattr(self.lms_assignment, 'due_at') and self.lms_assignment.due_at:
        if isinstance(self.lms_assignment.due_at, str):
          due_date = dateutil.parser.parse(self.lms_assignment.due_at)
        else:
          due_date = self.lms_assignment.due_at

        # Ensure due_date has timezone info
        if due_date.tzinfo is None:
          due_date = due_date.replace(tzinfo=timezone.utc)

        due_passed = now >= due_date
        log.debug(
          f"Due date check: now={now}, due_at={due_date}, due_passed={due_passed}"
        )

      # Check lock_at date
      lock_passed = True  # Default to True if no lock date
      if hasattr(self.lms_assignment,
                 'lock_at') and self.lms_assignment.lock_at:
        if isinstance(self.lms_assignment.lock_at, str):
          lock_date = dateutil.parser.parse(self.lms_assignment.lock_at)
        else:
          lock_date = self.lms_assignment.lock_at

        # Ensure lock_date has timezone info
        if lock_date.tzinfo is None:
          lock_date = lock_date.replace(tzinfo=timezone.utc)

        lock_passed = now >= lock_date
        log.debug(
          f"Lock date check: now={now}, lock_at={lock_date}, lock_passed={lock_passed}"
        )

      # Assignment is fully locked when both dates have passed
      is_fully_locked = due_passed and lock_passed

      if not due_passed:
        log.info(
          f"Assignment not ready for grading - due date has not passed (due: {self.lms_assignment.due_at})"
        )
      elif not lock_passed:
        log.info(
          f"Assignment not ready for grading - lock date has not passed (locks: {self.lms_assignment.lock_at})"
        )
      else:
        log.debug(f"Assignment is fully locked and ready for grading")

      return is_fully_locked

    except Exception as e:
      log.warning(
        f"Error checking assignment dates: {e}. Defaulting to unlocked.")
      return False

  def get_submission_data(self) -> List[Dict]:
    """
    Return structured submission data for grading.

    Returns:
        List of dictionaries containing student_id, text, word_count, and submission_obj
    """
    return self.submission_data

  def get_all_submission_texts(self) -> List[str]:
    """
    Return just the text content from all submissions for aggregate analysis.

    Returns:
        List of submission text strings
    """
    return [data['text'] for data in self.submission_data if data['text']]

  def finalize(self, *args, **kwargs):
    """
    Finalize grading by pushing scores and feedback to Canvas.
    """
    return super().finalize(*args, **kwargs)
