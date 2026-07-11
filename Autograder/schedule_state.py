from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
import tempfile
import threading
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import yaml
from dateutil.rrule import rrulestr

from Autograder import exceptions as autograder_exceptions
from Autograder.config_models import ScheduleConfig

log = logging.getLogger(__name__)

STATE_VERSION = 1
STATE_FILE_NAME = "schedule_state.yaml"


@dataclass
class ScheduleStateEntry:
  last_completed_at: Optional[datetime] = None


@dataclass
class ScheduleState:
  version: int = STATE_VERSION
  assignment_types: Dict[str, ScheduleStateEntry] = field(default_factory=dict)


def _parse_iso_datetime(value: Any, context_label: str) -> Optional[datetime]:
  if value is None:
    return None
  if isinstance(value, datetime):
    dt = value
  elif isinstance(value, str):
    text = value.strip()
    if not text:
      return None
    if text.endswith("Z"):
      text = f"{text[:-1]}+00:00"
    try:
      dt = datetime.fromisoformat(text)
    except ValueError as e:
      raise autograder_exceptions.ConfigurationError(
        f"Invalid datetime value for {context_label}: {value!r}") from e
  else:
    raise autograder_exceptions.ConfigurationError(
      f"Invalid datetime value for {context_label}: {value!r}")

  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(timezone.utc)


def _format_iso_datetime(value: datetime) -> str:
  return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
    "+00:00", "Z")


def _resolve_schedule_state_path() -> str:
  explicit = os.getenv("AUTOGRADER_SCHEDULE_STATE_PATH")
  if explicit:
    return os.path.abspath(os.path.expanduser(explicit))

  log_dir = os.getenv("LOG_DIR", os.path.abspath(os.path.expanduser(
    "~/.autograder/logs")))
  return os.path.join(os.path.abspath(os.path.expanduser(log_dir)),
                      STATE_FILE_NAME)


def load_schedule_state(path: str) -> ScheduleState:
  if not os.path.exists(path):
    return ScheduleState()

  try:
    with open(path, "r", encoding="utf-8") as f:
      raw = yaml.safe_load(f) or {}
  except Exception as e:
    raise autograder_exceptions.ConfigurationError(
      f"Failed to load schedule state from '{path}': {e}") from e

  if not isinstance(raw, dict):
    raise autograder_exceptions.ConfigurationError(
      f"Schedule state file '{path}' must contain a mapping")

  version = raw.get("version", STATE_VERSION)
  if version != STATE_VERSION:
    raise autograder_exceptions.ConfigurationError(
      f"Unsupported schedule state version in '{path}': {version!r}")

  raw_assignment_types = raw.get("assignment_types", {})
  if not isinstance(raw_assignment_types, dict):
    raise autograder_exceptions.ConfigurationError(
      f"Schedule state file '{path}' has invalid assignment_types section")

  assignment_types: Dict[str, ScheduleStateEntry] = {}
  for type_name, raw_entry in raw_assignment_types.items():
    if not isinstance(raw_entry, dict):
      raise autograder_exceptions.ConfigurationError(
        f"Schedule state entry for '{type_name}' must be a mapping")
    last_completed_at = _parse_iso_datetime(
      raw_entry.get("last_completed_at"),
      f"schedule_state.assignment_types.{type_name}.last_completed_at",
    )
    assignment_types[type_name] = ScheduleStateEntry(
      last_completed_at=last_completed_at)

  return ScheduleState(version=version, assignment_types=assignment_types)


def save_schedule_state(path: str, state: ScheduleState) -> None:
  directory = os.path.dirname(path)
  if directory:
    os.makedirs(directory, exist_ok=True)

  payload: Dict[str, Any] = {
    "version": state.version,
    "assignment_types": {},
  }
  for type_name in sorted(state.assignment_types):
    entry = state.assignment_types[type_name]
    if entry.last_completed_at is None:
      continue
    payload["assignment_types"][type_name] = {
      "last_completed_at": _format_iso_datetime(entry.last_completed_at)
    }

  tmp_path = None
  try:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=directory or None,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        delete=False,
    ) as f:
      tmp_path = f.name
      yaml.safe_dump(payload, f, sort_keys=True, default_flow_style=False)
    os.replace(tmp_path, path)
  except Exception as e:
    raise autograder_exceptions.FileProcessingError(
      f"Failed to save schedule state to '{path}': {e}") from e
  finally:
    if tmp_path and os.path.exists(tmp_path):
      try:
        os.unlink(tmp_path)
      except Exception:
        pass


def _compile_rule(schedule: ScheduleConfig):
  tz = ZoneInfo(schedule.timezone)
  dtstart = datetime(1970, 1, 1, tzinfo=tz)
  return rrulestr(schedule.rrule, dtstart=dtstart)


def estimate_schedule_interval_seconds(schedule: ScheduleConfig) -> Optional[float]:
  rule = _compile_rule(schedule)
  first = rule.after(datetime(1970, 1, 1, tzinfo=ZoneInfo(schedule.timezone)),
                     inc=True)
  if first is None:
    return None
  second = rule.after(first, inc=False)
  if second is None:
    return None
  interval_seconds = (second - first).total_seconds()
  if interval_seconds <= 0:
    return None
  return interval_seconds


def latest_due_occurrence(schedule: ScheduleConfig,
                          now_utc: Optional[datetime] = None) -> Optional[datetime]:
  now_utc = now_utc or datetime.now(timezone.utc)
  if now_utc.tzinfo is None:
    now_utc = now_utc.replace(tzinfo=timezone.utc)
  now_local = now_utc.astimezone(ZoneInfo(schedule.timezone))
  rule = _compile_rule(schedule)
  due_local = rule.before(now_local, inc=True)
  if due_local is None:
    return None
  return due_local.astimezone(timezone.utc)


class ScheduleStateManager:
  def __init__(self, path: Optional[str] = None,
               state: Optional[ScheduleState] = None):
    self.path = path or _resolve_schedule_state_path()
    self.state = state or load_schedule_state(self.path)
    self._planned_counts: Counter[str] = Counter()
    self._seen_counts: Counter[str] = Counter()
    self._successful_counts: Counter[str] = Counter()
    self._failed_types: set[str] = set()
    self._completed_types: set[str] = set()
    self.write_error: Optional[Exception] = None
    self._lock = threading.Lock()

  @classmethod
  def load_default(cls) -> "ScheduleStateManager":
    return cls()

  def register_planned_assignments(self, assignments: list[Any]) -> None:
    with self._lock:
      self._planned_counts = Counter(a.assignment_type for a in assignments)
      self._seen_counts = Counter()
      self._successful_counts = Counter()
      self._failed_types = set()
      self._completed_types = set()

  def is_assignment_type_due(self, assignment_type_name: str,
                             schedule: Optional[ScheduleConfig],
                             now_utc: Optional[datetime] = None) -> bool:
    if schedule is None:
      return True
    latest_due = latest_due_occurrence(schedule, now_utc=now_utc)
    if latest_due is None:
      return False
    last_completed_at = self.state.assignment_types.get(
      assignment_type_name, ScheduleStateEntry()).last_completed_at
    if last_completed_at is None:
      return True
    return last_completed_at < latest_due

  def _mark_completed(self, assignment_type_name: str,
                      completed_at: Optional[datetime] = None) -> None:
    completed_at = (completed_at or datetime.now(timezone.utc)).astimezone(
      timezone.utc)
    self.state.assignment_types[assignment_type_name] = ScheduleStateEntry(
      last_completed_at=completed_at)
    save_schedule_state(self.path, self.state)

  def record_assignment_result(self, assignment_data: Any,
                               result: Dict[str, Any]) -> None:
    assignment_type_name = assignment_data.assignment_type
    with self._lock:
      if assignment_type_name not in self._planned_counts:
        return
      self._seen_counts[assignment_type_name] += 1
      if result.get("success"):
        self._successful_counts[assignment_type_name] += 1
      else:
        self._failed_types.add(assignment_type_name)

      if assignment_type_name in self._completed_types:
        return

      planned = self._planned_counts[assignment_type_name]
      if self._seen_counts[assignment_type_name] < planned:
        return
      if assignment_type_name in self._failed_types:
        return
      if self._successful_counts[assignment_type_name] != planned:
        return

      self._completed_types.add(assignment_type_name)
      try:
        self._mark_completed(assignment_type_name)
      except autograder_exceptions.FileProcessingError as e:
        self.write_error = e
        log.error(
          f"Failed to persist schedule state for assignment type '{assignment_type_name}': {e}"
        )
