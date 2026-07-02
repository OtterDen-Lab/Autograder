from datetime import datetime, timezone
from types import SimpleNamespace

from Autograder.config_models import ScheduleConfig
from Autograder.schedule_state import (
  ScheduleState,
  ScheduleStateEntry,
  ScheduleStateManager,
  load_schedule_state,
)


def test_schedule_state_manager_respects_rrule_and_last_completed_at(tmp_path):
  state_path = tmp_path / "schedule_state.yaml"
  manager = ScheduleStateManager(path=str(state_path), state=ScheduleState())
  schedule = ScheduleConfig(
    timezone="UTC",
    rrule="FREQ=DAILY;BYHOUR=12;BYMINUTE=0;BYSECOND=0",
  )

  due_now = datetime(2026, 6, 25, 12, 1, tzinfo=timezone.utc)
  assert manager.is_assignment_type_due("text", schedule, now_utc=due_now)

  manager.state.assignment_types["text"] = ScheduleStateEntry(
    last_completed_at=datetime(2026, 6, 25, 12, 5, tzinfo=timezone.utc))
  assert not manager.is_assignment_type_due("text", schedule, now_utc=due_now)


def test_schedule_state_manager_writes_yaml_atomically(tmp_path):
  state_path = tmp_path / "schedule_state.yaml"
  manager = ScheduleStateManager(path=str(state_path), state=ScheduleState())
  assignments = [
    SimpleNamespace(assignment_type="programming"),
    SimpleNamespace(assignment_type="programming"),
  ]

  manager.register_planned_assignments(assignments)
  manager.record_assignment_result(assignments[0], {"success": True})
  assert not state_path.exists()

  manager.record_assignment_result(assignments[1], {"success": True})
  assert state_path.exists()
  assert not list(tmp_path.glob("*.tmp"))

  loaded = load_schedule_state(str(state_path))
  assert "programming" in loaded.assignment_types
  assert loaded.assignment_types["programming"].last_completed_at is not None

