#!/usr/bin/env python
from __future__ import annotations

import itertools
import logging
import os
import queue
import random
import tempfile
import threading
import time
from datetime import datetime, timezone

import canvasapi
import canvasapi.assignment
import canvasapi.course
import canvasapi.exceptions
import canvasapi.quiz
import canvasapi.submission
import dotenv
import requests

from .classes import (
  FileSubmission__Canvas,
  LMSWrapper,
  QuizSubmission,
  Student,
  Submission,
  Submission__Canvas,
  TextSubmission__Canvas,
)
from .privacy import PrivacyContext

log = logging.getLogger(__name__)

MAX_UPLOAD_RETRIES = 50
UPLOAD_MAX_WORKERS = 4
UPLOAD_MAX_IN_FLIGHT = 8
RETRY_BACKOFF_BASE = 1.0
RETRY_BACKOFF_MAX = 10.0
RETRY_BACKOFF_JITTER_RATIO = 0.2
RETRY_TOTAL_TIMEOUT_SECONDS = 120.0


def _canvas_exception_status(exc: Exception) -> int | None:
  status = getattr(exc, "status_code", None)
  if status is not None:
    return status
  response = getattr(exc, "response", None)
  return getattr(response, "status_code", None)


def _is_retryable_canvas_exception(exc: Exception) -> bool:
  status = _canvas_exception_status(exc)
  if status is None:
    return True
  if status == 429:
    return True
  if 500 <= status <= 599:
    return True
  return False


def _format_canvas_exception(exc: Exception) -> str:
  status = _canvas_exception_status(exc)
  parts = []
  if status is not None:
    parts.append(f"status={status}")
  response = getattr(exc, "response", None)
  if response is not None:
    method = getattr(response, "request", None)
    if method is not None:
      req_method = getattr(method, "method", None)
      req_url = getattr(method, "url", None)
      if req_method or req_url:
        parts.append(f"request={req_method} {req_url}".strip())
    try:
      json_payload = response.json()
      parts.append(f"response={json_payload}")
    except Exception:
      try:
        text = getattr(response, "text", None)
        if text:
          parts.append(f"response_text={text[:200]}")
      except Exception:
        pass
  return " | ".join(parts)


def _compute_retry_delay_seconds(
    attempt: int,
    *,
    retry_backoff_base: float,
    retry_backoff_max: float,
    retry_backoff_jitter_ratio: float,
) -> float:
  base_delay = min(retry_backoff_base * (2 ** (attempt - 1)),
                   retry_backoff_max)
  if retry_backoff_jitter_ratio <= 0:
    return max(0.0, base_delay)

  jitter_window = max(0.0, base_delay * retry_backoff_jitter_ratio)
  jittered = base_delay + random.uniform(-jitter_window, jitter_window)
  return max(0.0, min(jittered, retry_backoff_max))



class CanvasInterface:
  def __init__(
      self,
      *,
      prod: bool = False,
      env_path: str | None = None,
      canvas_url: str | None = None,
      canvas_key: str | None = None,
      privacy_mode: str | None = None,
      reveal_identity: bool = False,
      blind_id_map_path: str | None = None
  ):
    self.env_path = env_path
    if canvas_url is not None or canvas_key is not None:
      if not canvas_url or not canvas_key:
        raise ValueError("Both canvas_url and canvas_key are required when providing credentials.")
      self.canvas_url = canvas_url
      self.canvas_key = canvas_key
    else:
      if env_path:
        dotenv.load_dotenv(env_path)
      else:
        dotenv.load_dotenv(os.path.join(os.path.expanduser("~"), ".env"))

    self.prod = prod
    self.privacy_mode = privacy_mode or "id_only"
    if self.privacy_mode not in {"none", "id_only", "blind"}:
      raise ValueError("privacy_mode must be one of: none, id_only, blind.")
    self.reveal_identity = reveal_identity
    self.blind_id_map_path = blind_id_map_path
    self.privacy_context = PrivacyContext(
      privacy_mode=self.privacy_mode,
      reveal_identity=self.reveal_identity,
      blind_id_map_path=blind_id_map_path)
    if canvas_url is None and canvas_key is None:
      if self.prod:
        log.warning("Using canvas PROD!")
        self.canvas_url = os.environ.get("CANVAS_API_URL_prod")
        self.canvas_key = os.environ.get("CANVAS_API_KEY_prod")
      else:
        log.info("Using canvas DEV")
        self.canvas_url = os.environ.get("CANVAS_API_URL")
        self.canvas_key = os.environ.get("CANVAS_API_KEY")

    if not self.canvas_url or not self.canvas_key:
      env_hint = "CANVAS_API_URL[_prod] and CANVAS_API_KEY[_prod]"
      raise ValueError(
        "Canvas credentials are missing. "
        f"Set {env_hint} in your .env or environment variables."
      )

    # Monkeypatch BEFORE constructing Canvas so all children use RobustRequester.
    # cap_req.Requester = RobustRequester
    # cap_canvas.Requester = RobustRequester
    self.canvas = canvasapi.Canvas(self.canvas_url, self.canvas_key)

  def resolve_student_name(self,
                           user_id: int,
                           raw_name: str | None = None) -> str:
    return self.privacy_context.resolve_student_name(user_id, raw_name=raw_name)

  def get_course(self, course_id: int) -> CanvasCourse:
    if course_id is None:
      raise ValueError("course_id is required to fetch a Canvas course.")
    canvasapi_course = self.canvas.get_course(course_id)
    if not hasattr(canvasapi_course, "id"):
      raise ValueError(
        f"Canvas course lookup failed for course_id={course_id}. "
        "Ensure the course exists and your API key has access."
      )
    return CanvasCourse(
      canvas_interface = self,
      canvasapi_course = canvasapi_course
    )


class CanvasCourse(LMSWrapper):
  def __init__(self, *args, canvas_interface : CanvasInterface, canvasapi_course : canvasapi.course.Course, **kwargs):
    self.canvas_interface = canvas_interface
    self.course = canvasapi_course
    super().__init__(_inner=self.course)

  @staticmethod
  def _ensure_zero_weight_assignment_group(assignment_group) -> None:
    """
    Best-effort normalization so practice groups stay excluded from overall grade.
    Handles common CanvasAPI edit signatures defensively.
    """
    edit_payloads = [
      {"group_weight": 0.0, "position": 0},
      {"assignment_group": {"group_weight": 0.0, "position": 0}},
    ]
    for payload in edit_payloads:
      try:
        assignment_group.edit(**payload)
        return
      except TypeError:
        continue
      except Exception as exc:
        log.warning(f"Could not update assignment group weight to 0: {exc}")
        return

  def create_assignment_group(self, name="dev", delete_existing=False) -> canvasapi.course.AssignmentGroup:
    env_name = os.environ.get("QUIZGEN_ASSIGNMENT_GROUP")
    if env_name:
      name = env_name
    if not hasattr(self.course, "id"):
      raise ValueError(
        "Canvas course object is missing an id. "
        "Check that the course exists and your API key has access."
      )
    for assignment_group in self.course.get_assignment_groups():
      if assignment_group.name == name:
        if delete_existing:
          assignment_group.delete()
          break
        self._ensure_zero_weight_assignment_group(assignment_group)
        log.info("Found group existing, returning")
        return assignment_group
    assignment_group = self.course.create_assignment_group(
      name=name,
      group_weight=0.0,
      position=0,
    )
    self._ensure_zero_weight_assignment_group(assignment_group)
    return assignment_group
  
  def add_quiz(
      self,
      assignment_group: canvasapi.course.AssignmentGroup,
      title = None,
      *,
      is_practice=False,
      description=None
  ):
    if title is None:
      title = f"New Quiz {datetime.now().strftime('%m/%d/%y %H:%M:%S.%f')}"

    if description is None:
      description = """
        This quiz is aimed to help you practice skills.
        Please take it as many times as necessary to get full marks!
        Please note that although the answers section may be a bit lengthy,
        below them is often an in-depth explanation on solving the problem!
      """

    q = self.course.create_quiz(quiz={
      "title": title,
      "hide_results" : None,
      "show_correct_answers": True,
      "scoring_policy": "keep_highest",
      "allowed_attempts": -1,
      "shuffle_answers": True,
      "assignment_group_id": assignment_group.id,
      "quiz_type" : "assignment" if not is_practice else "practice_quiz",
      "description": description
    })
    return q

  def create_question(
      self,
      canvas_quiz: canvasapi.quiz.Quiz,
      question_payloads,
      *,
      group_name: str | None = None,
      question_points: float | None = None,
      pick_count: int = 1,
      max_upload_retries: int = MAX_UPLOAD_RETRIES,
      retry_backoff_base: float = RETRY_BACKOFF_BASE,
      retry_backoff_max: float = RETRY_BACKOFF_MAX,
      max_workers: int = UPLOAD_MAX_WORKERS,
      max_in_flight: int = UPLOAD_MAX_IN_FLIGHT,
      progress_callback=None,
      show_progress_bar: bool = False,
      progress_label: str | None = None
  ) -> canvasapi.quiz.QuizGroup | None:
    """
    Upload one question or a group of questions to Canvas.

    Args:
      canvas_quiz: Canvas quiz object to receive questions.
      question_payloads: A single payload dict or an iterable of payload dicts.
      group_name: If provided (or if multiple payloads), create a question group.
      question_points: Points per question in the group (required for grouping).
      pick_count: Number of questions to pick from the group.
      progress_callback: Optional callable receiving progress events.
      show_progress_bar: If True, display a tqdm progress bar when available.
      progress_label: Optional label for progress bars.

    Note:
      If question_payloads is an iterator, uploads are streamed without buffering.
    """
    total_questions = None
    if isinstance(question_payloads, dict):
      payload_iter = iter([question_payloads])
      total_questions = 1
      use_group = False
    elif isinstance(question_payloads, (list, tuple)):
      payload_iter = iter(question_payloads)
      total_questions = len(question_payloads)
      use_group = True
    else:
      payload_iter = iter(question_payloads)
      use_group = True

    if group_name is not None or question_points is not None:
      use_group = True

    try:
      first_payload = next(payload_iter)
    except StopIteration:
      log.warning("No question payloads provided; skipping upload.")
      return None

    payloads_iter = itertools.chain([first_payload], payload_iter)

    group = None
    if use_group:
      if question_points is None:
        question_points = first_payload.get("points_possible")
      if question_points is None:
        raise ValueError("question_points is required when creating a question group.")
      if group_name is None:
        group_name = first_payload.get("question_name", "Question Group")

      group = canvas_quiz.create_question_group([
        {
          "name": group_name,
          "pick_count": pick_count,
          "question_points": question_points
        }
      ])
      def attach_group_id():
        for payload in payloads_iter:
          payload["quiz_group_id"] = group.id
          yield payload

      self._upload_question_payloads(
        canvas_quiz,
        attach_group_id(),
        max_upload_retries=max_upload_retries,
        retry_backoff_base=retry_backoff_base,
        retry_backoff_max=retry_backoff_max,
        max_workers=max_workers,
        max_in_flight=max_in_flight,
        progress_callback=progress_callback,
        show_progress_bar=show_progress_bar,
        total_questions=total_questions,
        progress_label=progress_label
      )
      return group

    self._upload_question_payloads(
      canvas_quiz,
      payloads_iter,
      max_upload_retries=max_upload_retries,
      retry_backoff_base=retry_backoff_base,
      retry_backoff_max=retry_backoff_max,
      max_workers=max_workers,
      max_in_flight=max_in_flight,
      progress_callback=progress_callback,
      show_progress_bar=show_progress_bar,
      total_questions=total_questions,
      progress_label=progress_label
    )

    return group

  def _upload_question_payloads(
      self,
      canvas_quiz: canvasapi.quiz.Quiz,
      payloads,
      *,
      max_upload_retries: int = MAX_UPLOAD_RETRIES,
      retry_backoff_base: float = RETRY_BACKOFF_BASE,
      retry_backoff_max: float = RETRY_BACKOFF_MAX,
      max_workers: int = UPLOAD_MAX_WORKERS,
      max_in_flight: int = UPLOAD_MAX_IN_FLIGHT,
      progress_callback=None,
      show_progress_bar: bool = False,
      total_questions: int | None = None,
      progress_label: str | None = None
  ) -> None:
    total = total_questions
    if total is None and isinstance(payloads, (list, tuple)):
      total = len(payloads)

    stats = {"completed": 0, "succeeded": 0, "failed": 0}
    progress_lock = threading.Lock()

    tqdm_bar = None
    if show_progress_bar:
      try:
        from tqdm import tqdm
      except Exception as exc:
        log.warning(f"Progress bar requested but tqdm is unavailable: {exc}")
      else:
        tqdm_bar = tqdm(
          total=total,
          unit="q",
          smoothing=0.1,
          leave=True,
          desc=progress_label
        )

    if progress_callback is None and tqdm_bar is None:
      def _default_progress_callback(event):
        event_type = event.get("event")
        total_count = event.get("total")
        completed_count = event.get("completed", 0)
        succeeded_count = event.get("succeeded", 0)
        failed_count = event.get("failed", 0)
        if event_type == "start":
          if total_count is None:
            log.info("Uploading question variations (streaming total).")
          else:
            log.info(f"Uploading question variations (total={total_count}).")
          return
        if event_type == "complete":
          log.info(
            f"Upload complete: {succeeded_count} succeeded, {failed_count} failed."
          )
          return

        label = event.get("label") or "question"
        if total_count:
          bar_width = 20
          filled = min(bar_width, int((completed_count / total_count) * bar_width))
          bar = "[" + ("#" * filled) + ("-" * (bar_width - filled)) + "]"
          log.info(
            f"{bar} {completed_count}/{total_count} {label} "
            f"({succeeded_count} ok, {failed_count} failed)"
          )
        else:
          log.info(
            f"{completed_count} uploaded ({succeeded_count} ok, "
            f"{failed_count} failed): {label}"
          )

      progress_callback = _default_progress_callback
    elif tqdm_bar is not None:
      user_callback = progress_callback

      def _tqdm_callback(event):
        event_type = event.get("event")
        if event_type in {"success", "failed"}:
          tqdm_bar.update(1)
        if event_type == "complete":
          tqdm_bar.close()
        if user_callback is not None:
          user_callback(event)

      progress_callback = _tqdm_callback

    def report(event: str, *, label: str | None = None):
      if progress_callback is None:
        return
      with progress_lock:
        snapshot = {
          "event": event,
          "label": label,
          "completed": stats["completed"],
          "succeeded": stats["succeeded"],
          "failed": stats["failed"],
          "total": total
        }
      try:
        progress_callback(snapshot)
      except Exception as exc:
        log.warning(f"Progress callback failed: {exc}")

    report("start")

    if max_workers <= 1:
      for index, payload in enumerate(payloads):
        label = payload.get("question_name", f"question_{index}")
        log.info(f"Uploading {index + 1} to canvas!")
        success = self._call_canvas_with_retry(
          label,
          lambda: canvas_quiz.create_question(question=payload),
          max_upload_retries=max_upload_retries,
          retry_backoff_base=retry_backoff_base,
          retry_backoff_max=retry_backoff_max,
          backoff_controller=None
        )
        with progress_lock:
          stats["completed"] += 1
          if success:
            stats["succeeded"] += 1
            event = "success"
          else:
            stats["failed"] += 1
            event = "failed"
      report(event, label=label)
      report("complete")
      return

    quiz_id = getattr(canvas_quiz, "id", None)
    if quiz_id is None:
      raise ValueError("canvas_quiz must have an id for multi-threaded uploads.")

    payload_queue: queue.Queue = queue.Queue()
    for payload in payloads:
      payload_queue.put(payload)
    for _ in range(max_workers):
      payload_queue.put(None)

    in_flight = threading.Semaphore(max_in_flight)
    backoff = _CanvasBackoffController()

    def worker(worker_index: int):
      quiz = self._create_quiz_client(quiz_id)
      while True:
        payload = payload_queue.get()
        if payload is None:
          break
        label = payload.get("question_name", f"question_{worker_index}")
        with in_flight:
          success = self._call_canvas_with_retry(
            label,
            lambda: quiz.create_question(question=payload),
            max_upload_retries=max_upload_retries,
            retry_backoff_base=retry_backoff_base,
            retry_backoff_max=retry_backoff_max,
            backoff_controller=backoff
          )
        with progress_lock:
          stats["completed"] += 1
          if success:
            stats["succeeded"] += 1
            event = "success"
          else:
            stats["failed"] += 1
            event = "failed"
        report(event, label=label)

    threads = [
      threading.Thread(target=worker, args=(i,), daemon=True)
      for i in range(max_workers)
    ]
    for thread in threads:
      thread.start()
    for thread in threads:
      thread.join()

    report("complete")

  def _create_quiz_client(self, quiz_id: int) -> canvasapi.quiz.Quiz:
    canvas_interface = CanvasInterface(
      prod=self.canvas_interface.prod,
      env_path=self.canvas_interface.env_path,
      canvas_url=self.canvas_interface.canvas_url,
      canvas_key=self.canvas_interface.canvas_key,
      privacy_mode=self.canvas_interface.privacy_mode,
      reveal_identity=self.canvas_interface.reveal_identity,
      blind_id_map_path=self.canvas_interface.blind_id_map_path
    )
    course = canvas_interface.get_course(self.course.id)
    return course.course.get_quiz(quiz_id)

  def _call_canvas_with_retry(
      self,
      label: str,
      func,
      *,
      max_upload_retries: int,
      retry_backoff_base: float,
      retry_backoff_max: float,
      backoff_controller: "_CanvasBackoffController | None",
      retry_backoff_jitter_ratio: float = RETRY_BACKOFF_JITTER_RATIO,
      retry_total_timeout_seconds: float | None = RETRY_TOTAL_TIMEOUT_SECONDS
  ) -> bool:
    started_at = time.monotonic()
    deadline = None
    if (retry_total_timeout_seconds is not None
        and retry_total_timeout_seconds > 0):
      deadline = started_at + retry_total_timeout_seconds

    for attempt in range(1, max_upload_retries + 1):
      if backoff_controller is not None:
        backoff_controller.wait()
      if deadline is not None and time.monotonic() >= deadline:
        elapsed = time.monotonic() - started_at
        log.error(
          f"Exceeded retry duration ({elapsed:.1f}s, cap={retry_total_timeout_seconds:.1f}s); dropping question: {label}"
        )
        return False
      try:
        func()
        return True
      except canvasapi.exceptions.CanvasException as e:
        status = _canvas_exception_status(e)
        retryable = _is_retryable_canvas_exception(e)
        error_type = "transient" if retryable else "permanent"
        log.warning(
          f"Encountered {error_type} Canvas error for {label} "
          f"(status={status}, attempt={attempt}/{max_upload_retries})."
        )
        log.warning(e)
        extra = _format_canvas_exception(e)
        if extra:
          log.warning(extra)
        if not retryable:
          log.error(f"Non-retryable Canvas error; dropping question: {label}")
          return False
        if attempt >= max_upload_retries:
          log.error(f"Exceeded max retries ({max_upload_retries}); dropping question: {label}")
          return False
        sleep_s = _compute_retry_delay_seconds(
          attempt,
          retry_backoff_base=retry_backoff_base,
          retry_backoff_max=retry_backoff_max,
          retry_backoff_jitter_ratio=retry_backoff_jitter_ratio,
        )
        if deadline is not None:
          remaining = deadline - time.monotonic()
          if remaining <= 0:
            elapsed = time.monotonic() - started_at
            log.error(
              f"Exceeded retry duration ({elapsed:.1f}s, cap={retry_total_timeout_seconds:.1f}s); dropping question: {label}"
            )
            return False
          sleep_s = min(sleep_s, remaining)

        if backoff_controller is not None and status == 429:
          backoff_controller.defer(sleep_s)
        log.warning(
          f"Retrying {label} in {sleep_s:.2f}s "
          f"(attempt {attempt}/{max_upload_retries})"
        )
        if sleep_s > 0:
          time.sleep(sleep_s)
    return False

  @staticmethod
  def _validate_assignment_metadata(canvasapi_assignment,
                                    assignment_id: int) -> None:
    missing_fields = []
    if not hasattr(canvasapi_assignment, "id"):
      missing_fields.append("id")
    if not hasattr(canvasapi_assignment, "name"):
      missing_fields.append("name")

    if missing_fields:
      missing = ", ".join(missing_fields)
      raise ValueError(
        f"Canvas returned incomplete metadata for assignment id={assignment_id} "
        f"(missing: {missing}). This can happen during Canvas maintenance or partial API outages."
      )

  def get_assignment(self, assignment_id : int) -> CanvasAssignment | None:
    try:
      canvas_assignment = self.course.get_assignment(assignment_id)
      self._validate_assignment_metadata(canvas_assignment, assignment_id)
      return CanvasAssignment(
        canvasapi_interface=self.canvas_interface,
        canvasapi_course=self,
        canvasapi_assignment=canvas_assignment
      )
    except canvasapi.exceptions.ResourceDoesNotExist:
      log.error(f"Assignment {assignment_id} not found in course \"{self.name}\"")
      return None
    
  def get_assignments(self, **kwargs) -> list[CanvasAssignment]:
    assignments : list[CanvasAssignment] = []
    for canvasapi_assignment in self.course.get_assignments(**kwargs):
      assignments.append(
        CanvasAssignment(
          canvasapi_interface=self.canvas_interface,
          canvasapi_course=self,
          canvasapi_assignment=canvasapi_assignment
        )
      )
    return assignments
  
  def get_username(self, user_id: int):
    return self.course.get_user(user_id).name
  
  def get_students(self, *, include_names: bool = False) -> list[Student]:
    students = [Student(s.name, s.id, s) for s in self.course.get_users(enrollment_type=["student"])]
    if self.canvas_interface.privacy_mode == "none" and include_names:
      return students
    return [self._apply_privacy(s, raw_name=s.name) for s in students]

  def get_quiz(self, quiz_id: int) -> CanvasQuiz | None:
    """Get a specific quiz by ID"""
    try:
      return CanvasQuiz(
        canvas_interface=self.canvas_interface,
        canvasapi_course=self,
        canvasapi_quiz=self.course.get_quiz(quiz_id)
      )
    except canvasapi.exceptions.ResourceDoesNotExist:
      log.error(f"Quiz {quiz_id} not found in course \"{self.name}\"")
      return None

  def get_quizzes(self, **kwargs) -> list[CanvasQuiz]:
    """Get all quizzes in the course"""
    quizzes: list[CanvasQuiz] = []
    for canvasapi_quiz in self.course.get_quizzes(**kwargs):
      quizzes.append(
        CanvasQuiz(
          canvas_interface=self.canvas_interface,
          canvasapi_course=self,
          canvasapi_quiz=canvasapi_quiz
        )
      )
    return quizzes

  def _apply_privacy(self, student: Student, raw_name: str | None = None) -> Student:
    student.name = self.canvas_interface.resolve_student_name(student.user_id,
                                                              raw_name=raw_name)
    return student


class _CanvasBackoffController:
  def __init__(self):
    self._lock = threading.Lock()
    self._next_allowed = 0.0

  def wait(self) -> None:
    while True:
      with self._lock:
        delay = self._next_allowed - time.monotonic()
      if delay <= 0:
        return
      time.sleep(min(delay, RETRY_BACKOFF_MAX))

  def defer(self, seconds: float) -> None:
    with self._lock:
      self._next_allowed = max(self._next_allowed, time.monotonic() + seconds)


class CanvasAssignment(LMSWrapper):
  def __init__(self, *args, canvasapi_interface: CanvasInterface, canvasapi_course : CanvasCourse, canvasapi_assignment: canvasapi.assignment.Assignment, **kwargs):
    self.canvas_interface = canvasapi_interface
    self.canvas_course = canvasapi_course
    self.assignment = canvasapi_assignment
    super().__init__(_inner=canvasapi_assignment)
  
  def push_feedback(self, user_id, score: float, comments: str, attachments=None, keep_previous_best=True, clobber_feedback=False):
    log.debug(f"Adding feedback for {user_id}")
    if attachments is None:
      attachments = []
    
    # Get the previous score to check to see if we should reuse it
    try:
      submission = self.assignment.get_submission(user_id)
      if keep_previous_best and score is not None and submission.score is not None and submission.score > score:
        log.warning(f"Current score ({submission.score}) higher than new score ({score}).  Going to use previous score.")
        score = submission.score
    except (requests.exceptions.RequestException, canvasapi.exceptions.CanvasException) as e:
      log.warning(f"No previous submission found for {user_id}: {e}")
      extra = _format_canvas_exception(e)
      if extra:
        log.warning(extra)
    
    # Update the assignment
    # Note: the bulk_update will create a submission if none exists
    try:
      self.assignment.submissions_bulk_update(
        grade_data={
          'submission[posted_grade]' : score
        },
        student_ids=[user_id]
      )
      
      submission = self.assignment.get_submission(user_id)
    except (requests.exceptions.RequestException, canvasapi.exceptions.CanvasException) as e:
      log.error(e)
      extra = _format_canvas_exception(e)
      if extra:
        log.error(extra)
      log.debug(f"Failed on user_id = {user_id})")
      log.debug(f"username: {self.canvas_course.get_user(user_id)}")
      return False
    
    # Push feedback to canvas
    submission.edit(
      submission={
        'posted_grade':score,
      },
    )
    
    # If we should overwrite previous comments then remove all the previous submissions
    if clobber_feedback:
      log.debug("Clobbering...")
      # todo: clobbering should probably be moved up or made into a different function for cleanliness.
      for comment in submission.submission_comments:
        comment_id = comment['id']
        
        # Construct the URL to delete the comment
        api_path = f"/api/v1/courses/{self.canvas_course.course.id}/assignments/{self.assignment.id}/submissions/{user_id}/comments/{comment_id}"
        response = self.canvas_interface.canvas._Canvas__requester.request("DELETE", api_path)
        if response.status_code == 200:
          log.info(f"Deleted comment {comment_id}")
        else:
          log.warning(f"Failed to delete comment {comment_id}: {response.json()}")
    
    def upload_buffer_as_file(buffer: bytes, name: str):
      suffix = os.path.splitext(name)[1]  # keep extension if needed
      with tempfile.NamedTemporaryFile(mode="wb",
                                       delete=False,
                                       prefix="lms_interface_feedback_upload_",
                                       suffix=suffix) as tmp:
        tmp.write(buffer)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = tmp.name  # str path
      
      try:
        submission.upload_comment(temp_path)  # ✅ PathLike | str
      finally:
        os.remove(temp_path)
    
    if len(comments) > 0:
      upload_buffer_as_file(comments.encode('utf-8'), "feedback.txt")
    
    for i, attachment_buffer in enumerate(attachments):
      upload_buffer_as_file(attachment_buffer.read(), attachment_buffer.name)
    return True
  
  def get_submissions(self, only_include_most_recent: bool = True, **kwargs) -> list[Submission]:
    """
    Gets submission objects (in this case Submission__Canvas objects) that have students and potentially attachments
    :param only_include_most_recent: Include only the most recent submission
    :param kwargs:
    :return:
    """
    
    if "limit" in kwargs and kwargs["limit"] is not None:
      limit = kwargs["limit"]
    else:
      limit = 1_000_000 # magically large number
    
    test_only = kwargs.get("test", False)
    
    submissions: list[Submission] = []
    
    # Get all submissions and their history (which is necessary for attachments when students can resubmit)
    for student_index, canvaspai_submission in enumerate(self.assignment.get_submissions(include='submission_history', **kwargs)):
      
      # Get the student object for the submission
      include_names = kwargs.get("include_names", False)
      user_id = canvaspai_submission.user_id
      need_raw_name = (self.canvas_course.canvas_interface.privacy_mode == "none"
                       or include_names or test_only)
      raw_name = None
      if need_raw_name:
        try:
          raw_name = self.canvas_course.get_username(user_id)
        except Exception as e:
          log.warning(f"Failed to fetch username for user_id {user_id}: {e}")

      student = Student(
        raw_name or f"Student {user_id}",
        user_id=user_id,
        _inner=(self.canvas_course.get_user(user_id) if include_names else None)
      )
      student = self.canvas_course._apply_privacy(student, raw_name=raw_name)
      
      if test_only and not (raw_name and "Test Student" in raw_name):
        continue
      
      log.debug(f"Checking submissions for {student.name} ({len(canvaspai_submission.submission_history)} submissions)")
      
      # Walk through submissions in the reverse order, so we'll default to grabbing the most recent submission first
      # This is important when we are going to be only including most recent
      for student_submission_index, student_submission in (
          reversed(list(enumerate(canvaspai_submission.submission_history)))):
        log.debug(f"Submission: {student_submission['workflow_state']} " +
                  (f"{student_submission['score']:0.2f}" if student_submission['score'] is not None else "None"))
        
        # Determine submission type based on content
        has_attachments = student_submission.get("attachments") is not None and len(student_submission.get("attachments", [])) > 0
        has_text_body = student_submission.get("body") is not None and student_submission.get("body").strip() != ""

        if has_text_body:
          # Text submission - create object-like structure from dict
          log.debug(f"Detected text submission for {student.name}")
          class SubmissionObject:
            def __init__(self, data):
              for key, value in data.items():
                setattr(self, key, value)

          submissions.append(
            TextSubmission__Canvas(
              student=student,
              status=Submission.Status.from_string(student_submission["workflow_state"], student_submission['score']),
              canvas_submission_data=SubmissionObject(student_submission),
              submission_index=student_submission_index
            )
          )
        elif has_attachments:
          # File submission
          log.debug(f"Detected file submission for {student.name}")
          submissions.append(
            FileSubmission__Canvas(
              student=student,
              status=Submission.Status.from_string(student_submission["workflow_state"], student_submission['score']),
              attachments=student_submission["attachments"],
              submission_index=student_submission_index
            )
          )
        else:
          # No submission content found
          log.debug(f"No submission content found for {student.name}")
          continue
        
        # Check if we should only include the most recent
        if only_include_most_recent: break
      
      # Check if we are limiting how many students we are checking
      if student_index >= (limit - 1): break
      
    # Reverse the submissions again so we are preserving temporal order.  This isn't necessary but makes me feel happy.
    submissions = list(reversed(submissions))
    return submissions
  
  def get_students(self, *, include_names: bool = False):
    return self.canvas_course.get_students(include_names=include_names)


class CanvasQuiz(LMSWrapper):
  """Canvas quiz interface for handling quiz submissions and responses"""

  def __init__(self, *args, canvas_interface: CanvasInterface, canvasapi_course: CanvasCourse, canvasapi_quiz: canvasapi.quiz.Quiz, **kwargs):
    self.canvas_interface = canvas_interface
    self.canvas_course = canvasapi_course
    self.quiz = canvasapi_quiz
    super().__init__(_inner=canvasapi_quiz)

  def get_quiz_submissions(self, **kwargs) -> list[QuizSubmission]:
    """
    Get all quiz submissions with student responses
    :param kwargs: Additional parameters for filtering
    :return: List of QuizSubmission objects
    """
    test_only = kwargs.get("test", False)
    limit = kwargs.get("limit", 1_000_000)

    quiz_submissions: list[QuizSubmission] = []

    # Get all quiz submissions
    for student_index, canvasapi_quiz_submission in enumerate(self.quiz.get_submissions(**kwargs)):

      # Get the student object for the submission
      try:
        include_names = kwargs.get("include_names", False)
        user_id = canvasapi_quiz_submission.user_id
        need_raw_name = (self.canvas_course.canvas_interface.privacy_mode
                         == "none" or include_names or test_only)
        raw_name = None
        if need_raw_name:
          raw_name = self.canvas_course.get_username(user_id)

        student = Student(
          raw_name or f"Student {user_id}",
          user_id=user_id,
          _inner=(self.canvas_course.get_user(user_id) if include_names else None)
        )
        student = self.canvas_course._apply_privacy(student, raw_name=raw_name)
      except Exception as e:
        log.warning(
          f"Could not get student info for user_id {canvasapi_quiz_submission.user_id}: {e}"
        )
        continue

      if test_only and not (raw_name and "Test Student" in raw_name):
        continue

      log.debug(f"Processing quiz submission for {student.name}")

      # Get detailed submission responses
      try:
        submission_questions = canvasapi_quiz_submission.get_submission_questions()

        # Convert to our format: question_id -> response
        student_responses = {}
        quiz_questions = {}

        for question in submission_questions:
          question_id = question.id
          student_responses[question_id] = {
            'answer': question.answer,
            'correct': getattr(question, 'correct', None),
            'points': getattr(question, 'points', 0),
            'question_type': getattr(question, 'question_type', 'unknown')
          }

          # Store question metadata
          quiz_questions[question_id] = {
            'question_name': getattr(question, 'question_name', ''),
            'question_text': getattr(question, 'question_text', ''),
            'question_type': getattr(question, 'question_type', 'unknown'),
            'points_possible': getattr(question, 'points_possible', 0)
          }

        # Create QuizSubmission object
        quiz_submission = QuizSubmission(
          student=student,
          status=Submission.Status.from_string(canvasapi_quiz_submission.workflow_state, canvasapi_quiz_submission.percentage_score),
          quiz_submission_data=canvasapi_quiz_submission,
          student_responses=student_responses,
          quiz_questions=quiz_questions
        )

        quiz_submissions.append(quiz_submission)

      except Exception as e:
        log.error(f"Failed to get submission questions for {student.name}: {e}")
        continue

      # Check if we are limiting how many students we are checking
      if student_index >= (limit - 1):
        break

    return quiz_submissions

  def get_questions(self):
    """Get all quiz questions"""
    return self.quiz.get_questions()

  def push_feedback(self, user_id, score: float, comments: str, **kwargs):
    """
    Push feedback for a quiz submission
    Note: Quiz feedback mechanisms may be different from assignment feedback
    """
    # Quiz submissions typically don't support the same feedback mechanisms as assignments
    # This is a placeholder for quiz-specific feedback handling
    log.warning("Quiz feedback pushing not yet implemented")
    return False


    
