from datetime import datetime, timezone
from types import SimpleNamespace

from Autograder.config_models import ScheduleConfig
from Autograder.schedule_state import (
  ScheduleState,
  ScheduleStateEntry,
  ScheduleStateManager,
  load_schedule_state,
)


def test_schedule_state_defaults_to_autograder_directory(monkeypatch, tmp_path):
  monkeypatch.delenv("AUTOGRADER_SCHEDULE_STATE_PATH", raising=False)
  monkeypatch.setenv("HOME", str(tmp_path))

  manager = ScheduleStateManager(state=ScheduleState())

  assert manager.path == str(tmp_path / ".autograder" / "schedule_state.yaml")


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
  successful_push = {
    "success": True,
    "finalize_summary": {
      "push_enabled": True,
      "push_succeeded": 1,
      "push_failed": 0,
    },
  }
  manager.record_assignment_result(assignments[0], successful_push)
  assert not state_path.exists()

  manager.record_assignment_result(assignments[1], successful_push)
  assert state_path.exists()
  assert not list(tmp_path.glob("*.tmp"))

  loaded = load_schedule_state(str(state_path))
  assert "programming" in loaded.assignment_types
  assert loaded.assignment_types["programming"].last_completed_at is not None


def test_schedule_state_manager_requires_enabled_canvas_push(tmp_path):
  state_path = tmp_path / "schedule_state.yaml"
  manager = ScheduleStateManager(path=str(state_path), state=ScheduleState())
  assignment = SimpleNamespace(assignment_type="programming")

  manager.register_planned_assignments([assignment])
  manager.record_assignment_result(assignment, {
    "success": True,
    "finalize_summary": {
      "push_enabled": False,
      "push_succeeded": 0,
      "push_failed": 0,
    },
  })

  assert not state_path.exists()
  assert "programming" not in manager.state.assignment_types


def test_schedule_state_manager_completes_when_no_submissions(tmp_path):
  state_path = tmp_path / "schedule_state.yaml"
  manager = ScheduleStateManager(path=str(state_path), state=ScheduleState())
  assignment = SimpleNamespace(assignment_type="programming")

  manager.register_planned_assignments([assignment])
  manager.record_assignment_result(assignment, {
    "success": True,
    "stage_contract": {
      "prepare": {
        "skipped_reason": "no_submissions",
      },
    },
  })

  assert state_path.exists()
  assert "programming" in manager.state.assignment_types


def test_schedule_state_manager_completes_after_noop_canvas_push(tmp_path):
  state_path = tmp_path / "schedule_state.yaml"
  manager = ScheduleStateManager(path=str(state_path), state=ScheduleState())
  assignment = SimpleNamespace(assignment_type="programming")

  manager.register_planned_assignments([assignment])
  manager.record_assignment_result(assignment, {
    "success": True,
    "finalize_summary": {
      "push_enabled": True,
      "push_succeeded": 0,
      "push_failed": 0,
      "push_skipped": 1,
    },
  })

  assert state_path.exists()
  assert "programming" in manager.state.assignment_types


def test_schedule_state_manager_requires_push_without_failures(tmp_path):
  state_path = tmp_path / "schedule_state.yaml"
  manager = ScheduleStateManager(path=str(state_path), state=ScheduleState())
  assignment = SimpleNamespace(assignment_type="programming")

  manager.register_planned_assignments([assignment])
  manager.record_assignment_result(assignment, {
    "success": True,
    "finalize_summary": {
      "push_enabled": True,
      "push_succeeded": 1,
      "push_failed": 1,
    },
  })

  assert not state_path.exists()
  assert "programming" not in manager.state.assignment_types
