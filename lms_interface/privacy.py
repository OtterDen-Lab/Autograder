from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass

from .classes import Submission
from .interfaces import LMSAssignment, LMSBackend, LMSCourse, LMSUser

log = logging.getLogger(__name__)


class PrivacyContext:
  """
  Centralized privacy controls for student label resolution.

  Supports:
  - `none`: real names (when available)
  - `id_only`: `Student <canvas_user_id>`
  - `blind`: stable anonymous labels (`Anon 0001`, ...)
  """

  def __init__(self,
               *,
               privacy_mode: str = "id_only",
               reveal_identity: bool = False,
               blind_id_map_path: str | None = None):
    if privacy_mode not in {"none", "id_only", "blind"}:
      raise ValueError("privacy_mode must be one of: none, id_only, blind.")
    self.privacy_mode = privacy_mode
    self.reveal_identity = bool(reveal_identity)
    self._anon_by_user_id: dict[int, str] = {}
    self._anon_lock = threading.Lock()
    self._next_anon_index = 1
    self._blind_id_map_path = self._resolve_blind_id_map_path(blind_id_map_path)

    if self.privacy_mode == "blind":
      self._load_blind_id_map()

  @staticmethod
  def _resolve_blind_id_map_path(path: str | None) -> str:
    if isinstance(path, str) and path.strip():
      return os.path.abspath(os.path.expanduser(path))

    for env_key in ("LMS_BLIND_ID_MAP_PATH", "AUTOGRADER_BLIND_ID_MAP_PATH"):
      env_path = os.getenv(env_key)
      if env_path and env_path.strip():
        return os.path.abspath(os.path.expanduser(env_path))

    return os.path.abspath(
      os.path.expanduser("~/.lms_interface/privacy/blind_id_map.json"))

  def _load_blind_id_map(self) -> None:
    path = self._blind_id_map_path
    if not os.path.exists(path):
      return

    try:
      with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
      users = payload.get("users", {})
      if not isinstance(users, dict):
        return

      max_idx = 0
      for user_id_raw, label in users.items():
        try:
          user_id = int(user_id_raw)
        except Exception:
          continue
        if not isinstance(label, str):
          continue
        label = label.strip()
        if not label:
          continue
        self._anon_by_user_id[user_id] = label
        match = re.search(r"(\d+)$", label)
        if match:
          try:
            max_idx = max(max_idx, int(match.group(1)))
          except Exception:
            pass
      self._next_anon_index = max(max_idx + 1, len(self._anon_by_user_id) + 1)
    except Exception as e:
      log.warning(f"Failed to load blind ID map from '{path}': {e}")

  def _save_blind_id_map_locked(self) -> None:
    path = self._blind_id_map_path
    try:
      os.makedirs(os.path.dirname(path), exist_ok=True)
      payload = {
        "users": {
          str(user_id): label
          for user_id, label in sorted(self._anon_by_user_id.items())
        }
      }
      tmp_path = f"{path}.tmp"
      with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
      os.replace(tmp_path, path)
      try:
        os.chmod(path, 0o600)
      except Exception:
        pass
    except Exception as e:
      log.warning(f"Failed to persist blind ID map to '{path}': {e}")

  def _anonymous_label_for_user(self, user_id: int) -> str:
    with self._anon_lock:
      if user_id not in self._anon_by_user_id:
        label = f"Anon {self._next_anon_index:04d}"
        self._anon_by_user_id[user_id] = label
        self._next_anon_index += 1
        self._save_blind_id_map_locked()
      return self._anon_by_user_id[user_id]

  def resolve_student_name(self,
                           user_id: int,
                           raw_name: str | None = None) -> str:
    if self.privacy_mode == "none":
      if raw_name:
        return raw_name
      return f"Student {user_id}"
    if self.privacy_mode == "id_only":
      return f"Student {user_id}"
    return self._anonymous_label_for_user(user_id)

  def get_label(self, student) -> str:
    if student is None:
      return "Unknown Student"

    user_id = getattr(student, "user_id", None)
    raw_name = getattr(student, "name", None)
    if user_id is None:
      return str(raw_name or "Unknown Student")

    try:
      user_id_int = int(user_id)
    except Exception:
      return str(raw_name or f"Student {user_id}")

    label = self.resolve_student_name(user_id_int, raw_name=raw_name)
    if self.reveal_identity and str(user_id) not in str(label):
      return f"{label} [canvas_user_id={user_id}]"
    return str(label)


def _hash_id(value: str, salt: str) -> str:
  digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()
  return digest


@dataclass(frozen=True)
class PseudonymousStudent(LMSUser):
  name: str
  user_id: str
  real_user_id: str | int | None = None


class PrivacyBackend(LMSBackend):
  def __init__(self, backend: LMSBackend, *, salt: str | None = None, mode: str = "pseudonymous"):
    self._backend = backend
    self._mode = mode
    self._salt = salt or os.environ.get("LMS_PRIVACY_SALT")
    if self._mode not in {"pseudonymous", "id_only"}:
      raise ValueError("Privacy mode must be 'pseudonymous' or 'id_only'.")
    if self._mode == "pseudonymous" and not self._salt:
      raise ValueError("LMS_PRIVACY_SALT is required for pseudonymous privacy mode.")

  def get_course(self, course_id: int) -> LMSCourse:
    return PrivacyCourseAdapter(self._backend.get_course(course_id), salt=self._salt, mode=self._mode)


@dataclass
class PrivacyCourseAdapter(LMSCourse):
  _course: LMSCourse
  salt: str
  mode: str

  @property
  def id(self):
    return self._course.id

  @property
  def name(self):
    return self._course.name

  def _student_alias(self, student: LMSUser) -> PseudonymousStudent:
    raw_id = str(student.user_id)
    if self.mode == "id_only":
      return PseudonymousStudent(
        name=f"Student {raw_id}",
        user_id=raw_id,
        real_user_id=student.user_id
      )
    hashed = _hash_id(f"{self.id}:{raw_id}", self.salt)
    short = hashed[:8]
    return PseudonymousStudent(
      name=f"Student {short}",
      user_id=hashed,
      real_user_id=student.user_id
    )

  def get_assignment(self, assignment_id: int) -> LMSAssignment | None:
    assignment = self._course.get_assignment(assignment_id)
    if assignment is None:
      return None
    return PrivacyAssignmentAdapter(assignment, salt=self.salt, course_id=str(self.id), mode=self.mode)

  def get_assignments(self, **kwargs) -> list[LMSAssignment]:
    return [
      PrivacyAssignmentAdapter(a, salt=self.salt, course_id=str(self.id), mode=self.mode)
      for a in self._course.get_assignments(**kwargs)
    ]

  def get_students(self):
    return [self._student_alias(s) for s in self._course.get_students()]


@dataclass
class PrivacyAssignmentAdapter(LMSAssignment):
  _assignment: LMSAssignment
  salt: str
  course_id: str
  mode: str

  @property
  def id(self):
    return self._assignment.id

  @property
  def name(self):
    return self._assignment.name

  def _student_alias(self, student: LMSUser) -> PseudonymousStudent:
    raw_id = str(student.user_id)
    if self.mode == "id_only":
      return PseudonymousStudent(
        name=f"Student {raw_id}",
        user_id=raw_id,
        real_user_id=student.user_id
      )
    hashed = _hash_id(f"{self.course_id}:{raw_id}", self.salt)
    short = hashed[:8]
    return PseudonymousStudent(
      name=f"Student {short}",
      user_id=hashed,
      real_user_id=student.user_id
    )

  def get_submissions(self, **kwargs) -> list[Submission]:
    submissions = self._assignment.get_submissions(**kwargs)
    for submission in submissions:
      if getattr(submission, "student", None) is not None:
        submission.student = self._student_alias(submission.student)
    return submissions

  def push_feedback(
      self,
      user_id,
      score: float,
      comments: str,
      attachments=None,
      keep_previous_best: bool = True,
      clobber_feedback: bool = False
  ) -> None:
    self._assignment.push_feedback(
      user_id=user_id,
      score=score,
      comments=comments,
      attachments=attachments,
      keep_previous_best=keep_previous_best,
      clobber_feedback=clobber_feedback
    )
