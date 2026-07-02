#!env python
from __future__ import annotations

import math
import re
import tempfile
from datetime import datetime
from typing import List, Dict, Any
import abc
import os
import json
from Autograder.external_tools import (
  PanoptoWatchClient,
  extract_panopto_base_url,
  extract_panopto_session_id,
  extract_student_identifier,
  normalize_panopto_identifier,
  resolve_panopto_access_token,
)
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
    self._temp_dir = None
    self.canvas_points = kwargs.get(
      'canvas_points', None)  # Override for Canvas assignment points

  def __enter__(self) -> Assignment:
    """Enables use as a context manager for temp directory lifecycle."""
    if self.grading_root_dir is None:
      self._temp_dir = tempfile.TemporaryDirectory()
      self.grading_root_dir = self._temp_dir.name
      log.debug(f"Created grading temp dir: {self.grading_root_dir}")
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    """Clean up temp directory if one was created."""
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

  def _filter_submissions_for_student_id(self, student_id) -> None:
    """
    Restrict submissions to a single Canvas student ID when requested.
    """
    if student_id is None:
      return

    before = len(self.submissions)
    target_id = str(student_id)
    missing_student_id = 0
    filtered_submissions = []

    for submission in self.submissions:
      submission_student = getattr(submission, "student", None)
      submission_student_id = getattr(submission_student, "user_id", None)
      if submission_student_id is None:
        missing_student_id += 1
        continue
      if str(submission_student_id) == target_id:
        filtered_submissions.append(submission)

    self.submissions = filtered_submissions
    log.info(
      f"Filtered to {len(self.submissions)} submission(s) for canvas_user_id={student_id} "
      f"(was {before})")
    if missing_student_id:
      log.warning(
        f"Skipped {missing_student_id} submission(s) without a Canvas user ID "
        f"while filtering assignment '{self.lms_assignment.name}'")

  def finalize(self, *args, **kwargs) -> Dict[str, Any]:
    """
    This function is intended to finalize any grading.  This could be reloading the grading CSV and matching names,
    or could just be a noop.
    :param args:
    :param kwargs:
    :return:
    """

    push_enabled = bool(kwargs.get("push", False))
    allow_late_penalty = bool(kwargs.get("allow_late_penalty", True))
    idempotency_key = kwargs.get("idempotency_key")
    idempotency_state_dir = kwargs.get("idempotency_state_dir")
    processed_user_ids = None
    processed_dirty = False
    if push_enabled and idempotency_key:
      processed_user_ids = self._load_idempotency_user_ids(
        idempotency_key, idempotency_state_dir)
      log.debug(
        f"Idempotency tracking enabled for assignment {self.lms_assignment.name}: {len(processed_user_ids)} previously pushed submission(s)"
      )

    log.debug("Pushing")
    push_attempted = 0
    push_succeeded = 0
    push_failed = 0
    push_skipped = 0
    push_skipped_ungraded = 0
    push_failed_students = []
    push_skipped_ungraded_students = []

    def _has_attached_rubric() -> bool:
      """
      Check whether the Canvas assignment has a rubric attached.

      Some graders always generate local rubric-style feedback, but Canvas
      only accepts rubric assessments when the assignment itself has an
      attached rubric definition.
      """
      try:
        rubric_index_getter = getattr(self.lms_assignment,
                                      "_get_rubric_criterion_index", None)
        if callable(rubric_index_getter):
          return bool(rubric_index_getter())
      except Exception as e:
        log.debug(
          f"Unable to determine whether assignment '{self.lms_assignment.name}' has an attached rubric: {e}"
        )
      return False

    for submission in self.submissions:
      user_id = submission.student.user_id
      if push_enabled and processed_user_ids is not None and user_id in processed_user_ids:
        push_skipped += 1
        log.debug(
          f"Skipping push for {submission.student.name} (canvas_user_id={user_id}) due to idempotency key"
        )
        continue

      # Handle record retention before pushing to LMS
      if kwargs.get("record_retention", False):
        feedback = getattr(submission, "feedback", None)
        if feedback is not None:
          self._save_feedback_record(submission.student,
                                     feedback.comments,
                                     kwargs.get("records_dir"),
                                     self.lms_assignment.name)
        else:
          log.warning(
            f"Skipping record retention for assignment '{self.lms_assignment.name}', "
            f"student '{submission.student.name}' (canvas_user_id={user_id}) "
            "because no feedback was generated.")

      if push_enabled:
        feedback = getattr(submission, "feedback", None)
        if feedback is None or feedback.percentage_score is None:
          push_skipped += 1
          push_skipped_ungraded += 1
          push_skipped_ungraded_students.append(str(submission.student.name))
          log.warning(
            f"Skipping push for assignment '{self.lms_assignment.name}', "
            f"student '{submission.student.name}' (canvas_user_id={user_id}) "
            "because grading did not produce valid feedback.")
          continue

        log.debug(f"Pushing feedback for: {submission}")
        push_attempted += 1
        try:
          # Scale the score for Canvas submission
          scaled_score = self.scale_score_for_canvas(
            feedback.percentage_score)
          rubric_assessment = getattr(feedback, "rubric_assessment", None)
          if rubric_assessment is None:
            rubric_assessment = kwargs.get("rubric_assessment",
                                           kwargs.get("rubric"))
          push_kwargs = {
            "score": scaled_score,
            "comments": feedback.comments,
            "attachments": feedback.attachments,
            "user_id": user_id,
            "keep_previous_best": True,
            "clobber_feedback": False,
          }
          if not allow_late_penalty:
            push_kwargs["seconds_late"] = 0
          if rubric_assessment is not None and _has_attached_rubric():
            push_kwargs["rubric_assessment"] = rubric_assessment
          elif rubric_assessment is not None:
            log.info(
              f"Skipping rubric push for assignment '{self.lms_assignment.name}' because Canvas has no attached rubric."
            )
          pushed = self.lms_assignment.push_feedback(
            **push_kwargs)
        except Exception as e:
          push_failed += 1
          push_failed_students.append(str(submission.student.name))
          log.exception(
            f"Failed to push feedback for assignment '{self.lms_assignment.name}', "
            f"student '{submission.student.name}' (canvas_user_id={user_id}): {e}. "
            "Continuing to next submission. Verify Canvas API access, assignment publish state, and score validity."
          )
          continue

        if pushed:
          push_succeeded += 1
        else:
          push_failed += 1
          push_failed_students.append(str(submission.student.name))
          log.warning(
            f"Push feedback returned unsuccessful for assignment '{self.lms_assignment.name}', "
            f"student '{submission.student.name}' (canvas_user_id={user_id}). "
            "Continuing to next submission. Check Canvas assignment settings and submission state."
          )

        if pushed and processed_user_ids is not None:
          processed_user_ids.add(user_id)
          processed_dirty = True

    if push_enabled:
      log.info(
        f"Push summary for {self.lms_assignment.name}: attempted={push_attempted}, "
        f"succeeded={push_succeeded}, failed={push_failed}, skipped={push_skipped}, "
        f"skipped_ungraded={push_skipped_ungraded}"
      )

    if processed_user_ids is not None and processed_dirty:
      try:
        self._save_idempotency_user_ids(
          idempotency_key, idempotency_state_dir, processed_user_ids)
      except Exception as e:
        log.warning(
          f"Failed to persist idempotency state for assignment '{self.lms_assignment.name}': {e}. "
          "Grades may be pushed again on rerun with this key.")

    return {
      "push_enabled": push_enabled,
      "push_attempted": push_attempted,
      "push_succeeded": push_succeeded,
      "push_failed": push_failed,
      "push_skipped": push_skipped,
      "push_skipped_ungraded": push_skipped_ungraded,
      "push_failed_students": push_failed_students,
      "push_skipped_ungraded_students": push_skipped_ungraded_students,
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
      # Use student user_id instead of name for privacy (avoids PII in filenames)
      student_id = student.user_id

      # Sanitize assignment name for filename (remove/replace unsafe characters)
      assignment_safe = re.sub(r'[^\w\-_.]', '',
                               assignment_name.replace(' ', '_'))

      # Create timestamp
      timestamp = datetime.now().strftime("%Y-%b-%d_%H%M")

      # Ensure records directory exists
      if not os.path.exists(records_dir):
        os.makedirs(records_dir)
        log.debug(f"Created records directory: {records_dir}")

      # Create filename: [timestamp].[assignment_name].student_[id].log
      # Uses user_id instead of name to avoid PII in filenames
      filename = f"{timestamp}.{assignment_safe}.student_{student_id}.log"
      filepath = os.path.join(records_dir, filename)

      # Write feedback to file
      with open(filepath, 'w', encoding='utf-8') as f:
        f.write(comments)

      log.debug(f"Saved feedback record: {filepath}")

    except Exception as e:
      log.error(
        f"Failed to save feedback record for student_id={student.user_id} "
        f"to records_dir='{records_dir}' for assignment '{assignment_name}': {e}"
      )

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
        f"Failed to load idempotency state from '{path}': {e}. "
        "Continuing without prior state; previously pushed submissions may be retried.")
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
class ProgrammingAssignment(Assignment):
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
              student_id=None,
              only_include_latest=True,
              **kwargs):

    # Steps:
    #  1. Get the submissions
    #  2. Filter out submissions we don't want
    #  3. possibly download proactively
    log.info(
      f"Preparing assignment with do_regrade={do_regrade}, limit={limit}, student_id={student_id}")
    fetch_limit = None if (not do_regrade or student_id is not None) else limit
    self.submissions = self.lms_assignment.get_submissions(
      limit=fetch_limit, **kwargs)
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

    self._filter_submissions_for_student_id(student_id)

    log.info(f"Total students to grade: {len(self.submissions)}")
    if limit is not None:
      log.info(f"Limiting to {limit} students")
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
class TextAssignment(Assignment):
  """
  Assignment for text-based learning log submissions.
  Handles Canvas text submissions where students submit reflective writing.
  """

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.submission_data = []

  def prepare(self,
              limit=None,
              do_regrade=False,
              student_id=None,
              test=False,
              **kwargs):
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
      f"Preparing text assignment with do_regrade={do_regrade}, limit={limit}, "
      f"student_id={student_id}, test={test}"
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
    fetch_limit = None if (not do_regrade or student_id is not None) else limit
    self.submissions = self.lms_assignment.get_submissions(
      limit=fetch_limit, test=test, **kwargs)
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

    self._filter_submissions_for_student_id(student_id)

    # Apply limit if specified
    if limit is not None:
      log.info(f"Limiting to {limit} students")
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
        f"Error checking assignment dates for '{self.lms_assignment.name}' "
        f"(due_at={getattr(self.lms_assignment, 'due_at', None)}, "
        f"lock_at={getattr(self.lms_assignment, 'lock_at', None)}): {e}. "
        "Defaulting to unlocked."
      )
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


@AssignmentRegistry.register("ExternalToolAssignment")
class ExternalToolAssignment(Assignment):
  """
  Assignment backed by an external system rather than uploaded student work.
  """

  def prepare(self,
              limit=None,
              do_regrade=False,
              student_id=None,
              provider="panopto",
              panopto_url=None,
              panopto_base=None,
              panopto_base_url=None,
              panopto_session_id=None,
              panopto_id=None,
              panopto_access_token=None,
              panopto_access_token_env="PANOPTO_ACCESS_TOKEN",
              panopto_client_id=None,
              panopto_client_secret=None,
              panopto_client_id_env="PANOPTO_CLIENT_ID",
              panopto_client_secret_env="PANOPTO_CLIENT_SECRET",
              panopto_refresh_token=None,
              panopto_refresh_token_env="PANOPTO_REFRESH_TOKEN",
              panopto_refresh_token_path="~/.autograder/panopto_refresh_token.json",
              panopto_token_url=None,
              panopto_scope="api",
              watch_data_path_template="/Panopto/api/v1/sessions/{session_id}/viewers",
              canvas_user_attribute="email",
              external_user_attribute="email",
              student_identifier_overrides=None,
              record_identifier_paths=None,
              record_percent_paths=None,
              record_viewed_seconds_paths=None,
              record_duration_seconds_paths=None,
              request_timeout_seconds=30.0,
              missing_user_score=0.0,
              **kwargs):
    if provider != "panopto":
      raise ValueError(f"Unsupported external tool provider: {provider}")

    base_url = panopto_base or panopto_base_url
    if base_url is None and panopto_url:
      base_url = extract_panopto_base_url(panopto_url)
    if not base_url:
      raise ValueError(
        "panopto_base is required for ExternalToolAssignment "
        "(or provide panopto_url for backwards compatibility)")

    session_id = panopto_id or panopto_session_id
    if session_id is None and panopto_url:
      session_id = extract_panopto_session_id(panopto_url)
    if not session_id:
      raise ValueError(
        "Unable to determine Panopto session ID. Set panopto_id explicitly "
        "or provide panopto_url for backwards compatibility.")
    token = resolve_panopto_access_token(
      panopto_access_token,
      panopto_access_token_env,
      explicit_client_id=panopto_client_id,
      explicit_client_secret=panopto_client_secret,
      client_id_env=panopto_client_id_env,
      client_secret_env=panopto_client_secret_env,
      explicit_refresh_token=panopto_refresh_token,
      refresh_token_env=panopto_refresh_token_env,
      refresh_token_path=panopto_refresh_token_path,
      token_url=panopto_token_url,
      base_url=base_url,
      scope=panopto_scope,
      timeout_seconds=request_timeout_seconds,
    )

    client = PanoptoWatchClient(base_url=base_url,
                                access_token=token,
                                timeout_seconds=request_timeout_seconds)
    watch_records = client.fetch_watch_records(
      session_id=session_id,
      path_template=watch_data_path_template,
      record_identifier_paths=list(record_identifier_paths or []),
      record_percent_paths=list(record_percent_paths or []),
      record_viewed_seconds_paths=list(record_viewed_seconds_paths or []),
      record_duration_seconds_paths=list(record_duration_seconds_paths or []),
    )
    watch_by_key = {}
    for record in watch_records:
      normalized_key = normalize_panopto_identifier(record.user_key)
      if normalized_key is None:
        continue
      watch_by_key[normalized_key] = record

    log.debug(
      f"Panopto returned {len(watch_records)} raw watch record(s) for session {session_id}")
    for i, record in enumerate(sorted(watch_records, key=lambda r: r.user_key), 1):
      log.debug(
        "Panopto record %d: user_key=%s percent_watched=%s viewed_seconds=%s duration_seconds=%s time=%s raw=%s",
        i,
        record.user_key,
        record.percent_watched,
        record.viewed_seconds,
        record.duration_seconds,
        (record.raw or {}).get("LastViewedDateTime"),
        {} #json.dumps(record.raw, indent=2, sort_keys=True, default=str),
      )

    try:
      students = list(self.lms_assignment.get_students(include_names=True))
    except TypeError:
      students = list(self.lms_assignment.get_students())

    student_identifier_overrides = {
      str(k): str(v).strip().lower()
      for k, v in (student_identifier_overrides or {}).items()
    }
    student_statuses = self._load_submission_statuses()

    prepared_submissions: list[Submission] = []
    skipped_no_watch_record = 0
    matched_students = 0
    for student in students:
      raw_user_id = getattr(student, "user_id", None)
      if raw_user_id is None:
        continue
      user_id = int(raw_user_id)

      if student_id is not None and str(user_id) != str(student_id):
        continue

      if not do_regrade and student_statuses.get(user_id) == Submission.Status.GRADED:
        continue

      external_key = student_identifier_overrides.get(str(user_id))
      if external_key is None:
        external_key = extract_student_identifier(student, canvas_user_attribute)
      record = watch_by_key.get(external_key) if external_key else None

      log.debug(
        "Panopto mapping candidate: canvas_user_id=%s canvas_name=%s canvas_key=%s external_key=%s match=%s",
        user_id,
        getattr(student, "name", None),
        extract_student_identifier(student, canvas_user_attribute),
        external_key,
        bool(record),
      )

      if record is None:
        skipped_no_watch_record += 1
        continue

      submission = Submission(
        student=student,
        status=student_statuses.get(user_id, Submission.Status.MISSING))
      submission.set_extra({
        "external_provider": provider,
        "panopto_url": panopto_url,
        "panopto_session_id": session_id,
        "canvas_identifier": external_key,
        "external_identifier": external_key,
        "external_user_attribute": external_user_attribute,
        "missing_user_score": float(missing_user_score),
        "percent_watched": record.percent_watched,
        "viewed_seconds": record.viewed_seconds,
        "duration_seconds": record.duration_seconds,
        "watch_record_found": True,
        "watch_record_raw": record.raw,
      })
      prepared_submissions.append(submission)
      matched_students += 1

    if limit is not None:
      prepared_submissions = prepared_submissions[:limit]

    self.submissions = prepared_submissions
    log.info(
      f"Prepared {len(self.submissions)} external-tool submission(s) from Panopto "
      f"for assignment '{self.lms_assignment.name}'")
    if matched_students == 0:
      log.warning(
        "No Panopto watch records matched Canvas students. "
        "Students without matching records were left ungraded. "
        "Check canvas_user_attribute, external_user_attribute, student_identifier_overrides, "
        "and record_identifier_paths.")
    elif skipped_no_watch_record > 0:
      log.info(
        f"Skipped {skipped_no_watch_record} Canvas student(s) with no matching Panopto watch record; "
        "they were left ungraded.")

  def _load_submission_statuses(self) -> dict[int, Submission.Status]:
    try:
      lms_submissions = list(self.lms_assignment.get_submissions(limit=None))
    except Exception:
      return {}

    statuses: dict[int, Submission.Status] = {}
    for submission in lms_submissions:
      student = getattr(submission, "student", None)
      user_id = getattr(student, "user_id", None)
      if user_id is None:
        continue
      statuses[int(user_id)] = getattr(submission, "status",
                                       Submission.Status.UNGRADED)
    return statuses


# =============================================================================
# Backwards Compatibility Aliases
# =============================================================================
# These aliases preserve backwards compatibility for code that imports the
# old class names with underscores.
Assignment__ProgrammingAssignment = ProgrammingAssignment
Assignment_TextAssignment = TextAssignment
