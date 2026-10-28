from __future__ import annotations

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

STATE_VERSION = 2
STATE_FILE_NAME = "schedule_state.yaml"


@dataclass
class ScheduleStateEntry:
  last_completed_at: Optional[datetime] = None


@dataclass
class ScheduleState:
  version: int = STATE_VERSION
  assignments: Dict[str, ScheduleStateEntry] = field(default_factory=dict)


def assignment_state_key(course_id: Any, assignment_id: Any) -> str:
  """Return a stable state key for one Canvas assignment in one course."""
  return f"course:{course_id}/assignment:{assignment_id}"


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

  return os.path.join(os.path.abspath(os.path.expanduser("~/.autograder")),
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

  version = raw.get("version", 1)
  if version not in (1, STATE_VERSION):
    raise autograder_exceptions.ConfigurationError(
      f"Unsupported schedule state version in '{path}': {version!r}")

  # Version 1 keyed state by assignment type.  Do not transfer those times to
  # individual assignments: that would incorrectly treat a newly configured
  # assignment as already processed.  Saving after the first successful run
  # upgrades the file to the per-assignment format.
  if version == 1:
    return ScheduleState()

  raw_assignments = raw.get("assignments", {})
  if not isinstance(raw_assignments, dict):
    raise autograder_exceptions.ConfigurationError(
      f"Schedule state file '{path}' has invalid assignments section")

  assignments: Dict[str, ScheduleStateEntry] = {}
  for assignment_key, raw_entry in raw_assignments.items():
    if not isinstance(raw_entry, dict):
      raise autograder_exceptions.ConfigurationError(
        f"Schedule state entry for '{assignment_key}' must be a mapping")
    last_completed_at = _parse_iso_datetime(
      raw_entry.get("last_completed_at"),
      f"schedule_state.assignments.{assignment_key}.last_completed_at",
    )
    assignments[str(assignment_key)] = ScheduleStateEntry(
      last_completed_at=last_completed_at)

  return ScheduleState(version=STATE_VERSION, assignments=assignments)


def save_schedule_state(path: str, state: ScheduleState) -> None:
  directory = os.path.dirname(path)
  if directory:
    os.makedirs(directory, exist_ok=True)

  payload: Dict[str, Any] = {
    "version": STATE_VERSION,
    "assignments": {},
  }
  for assignment_key in sorted(state.assignments):
    entry = state.assignments[assignment_key]
    if entry.last_completed_at is None:
      continue
    payload["assignments"][assignment_key] = {
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
    log.info(f"Using schedule state file: {self.path}")
    self.state = state or load_schedule_state(self.path)
    self.write_error: Optional[Exception] = None
    self._lock = threading.Lock()

  @classmethod
  def load_default(cls) -> "ScheduleStateManager":
    return cls()

  def reconcile_configured_assignments(self, assignment_keys: set[str]) -> None:
    """Prune entries for assignments no longer present in the loaded YAML."""
    with self._lock:
      stale_keys = set(self.state.assignments) - assignment_keys
      if not stale_keys:
        return
      for key in stale_keys:
        del self.state.assignments[key]
      try:
        save_schedule_state(self.path, self.state)
      except autograder_exceptions.FileProcessingError as e:
        self.write_error = e
        log.error("Failed to prune schedule state: %s", e)

  def is_assignment_due(self, course_id: Any, assignment_id: Any,
                             schedule: Optional[ScheduleConfig],
                             now_utc: Optional[datetime] = None) -> bool:
    if schedule is None:
      return True
    latest_due = latest_due_occurrence(schedule, now_utc=now_utc)
    if latest_due is None:
      return False
    key = assignment_state_key(course_id, assignment_id)
    last_completed_at = self.state.assignments.get(
      key, ScheduleStateEntry()).last_completed_at
    if last_completed_at is None:
      return True
    return last_completed_at < latest_due

  def get_assignment_entry(self, course_id: Any,
                           assignment_id: Any) -> ScheduleStateEntry:
    return self.state.assignments.get(
      assignment_state_key(course_id, assignment_id), ScheduleStateEntry())

  def _mark_completed(self, assignment_key: str,
                      completed_at: Optional[datetime] = None) -> None:
    completed_at = (completed_at or datetime.now(timezone.utc)).astimezone(
      timezone.utc)
    self.state.assignments[assignment_key] = ScheduleStateEntry(
      last_completed_at=completed_at)
    save_schedule_state(self.path, self.state)

  def record_assignment_result(self, assignment_data: Any,
                               result: Dict[str, Any]) -> None:
    assignment_key = assignment_state_key(
      getattr(assignment_data, "course_id", None),
      getattr(assignment_data, "assignment_id", None))
    with self._lock:
      if not self._result_completes_schedule(result):
        return
      try:
        self._mark_completed(assignment_key)
      except autograder_exceptions.FileProcessingError as e:
        self.write_error = e
        log.error(
          f"Failed to persist schedule state for assignment '{assignment_key}': {e}"
        )

  @staticmethod
  def _result_completes_schedule(result: Dict[str, Any]) -> bool:
    """Return whether an assignment completed its scheduled work safely.

    Assignments without ungraded submissions return before the publish stage.
    They are a successful no-op, not a failed schedule run. Likewise, a
    publish that has no errors may legitimately make no Canvas changes because
    existing scores are already equal to or better than the computed score.
    """
    if not result.get("success"):
      return False

    stage_contract = result.get("stage_contract") or {}
    prepare = stage_contract.get("prepare") or {}
    if prepare.get("skipped_reason") == "no_submissions":
      return True

    finalize_summary = result.get("finalize_summary")
    if not isinstance(finalize_summary, dict):
      return False
    return (bool(finalize_summary.get("push_enabled"))
            and int(finalize_summary.get("push_failed", 0) or 0) == 0)
