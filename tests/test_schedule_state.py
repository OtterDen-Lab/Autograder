from datetime import datetime, timezone
from types import SimpleNamespace

import yaml

from Autograder.config_models import ScheduleConfig
from Autograder.schedule_state import (
  ScheduleState,
  ScheduleStateEntry,
  ScheduleStateManager,
  assignment_state_key,
  load_schedule_state,
)


def _assignment(course_id=101, assignment_id=501):
  return SimpleNamespace(course_id=course_id, assignment_id=assignment_id)


def _schedule():
  return ScheduleConfig(
    timezone="UTC",
    rrule="FREQ=DAILY;BYHOUR=12;BYMINUTE=0;BYSECOND=0",
  )


def _successful_push():
  return {
    "success": True,
    "finalize_summary": {
      "push_enabled": True,
      "push_succeeded": 1,
      "push_failed": 0,
    },
  }


def test_schedule_state_defaults_to_autograder_directory(monkeypatch, tmp_path):
  monkeypatch.delenv("AUTOGRADER_SCHEDULE_STATE_PATH", raising=False)
  monkeypatch.setenv("HOME", str(tmp_path))
  manager = ScheduleStateManager(state=ScheduleState())
  assert manager.path == str(tmp_path / ".autograder" / "schedule_state.yaml")


def test_schedule_state_manager_tracks_each_assignment_independently(tmp_path):
  manager = ScheduleStateManager(
    path=str(tmp_path / "schedule_state.yaml"), state=ScheduleState())
  due_now = datetime(2026, 6, 25, 12, 1, tzinfo=timezone.utc)

  assert manager.is_assignment_due(101, 501, _schedule(), now_utc=due_now)
  manager.record_assignment_result(_assignment(101, 501), _successful_push())

  assert not manager.is_assignment_due(101, 501, _schedule(), now_utc=due_now)
  assert manager.is_assignment_due(101, 502, _schedule(), now_utc=due_now)


def test_schedule_state_manager_writes_yaml_atomically(tmp_path):
  state_path = tmp_path / "schedule_state.yaml"
  manager = ScheduleStateManager(path=str(state_path), state=ScheduleState())
  manager.record_assignment_result(_assignment(101, 501), _successful_push())
  manager.record_assignment_result(_assignment(102, 501), _successful_push())

  assert state_path.exists()
  assert not list(tmp_path.glob("*.tmp"))
  loaded = load_schedule_state(str(state_path))
  assert set(loaded.assignments) == {
    assignment_state_key(101, 501), assignment_state_key(102, 501)}


def test_schedule_state_manager_does_not_complete_failed_push(tmp_path):
  manager = ScheduleStateManager(
    path=str(tmp_path / "schedule_state.yaml"), state=ScheduleState())
  manager.record_assignment_result(_assignment(), {
    "success": True,
    "finalize_summary": {
      "push_enabled": True,
      "push_succeeded": 1,
      "push_failed": 1,
    },
  })
  assert not manager.state.assignments


def test_schedule_state_manager_completes_when_no_submissions(tmp_path):
  manager = ScheduleStateManager(
    path=str(tmp_path / "schedule_state.yaml"), state=ScheduleState())
  manager.record_assignment_result(_assignment(), {
    "success": True,
    "stage_contract": {"prepare": {"skipped_reason": "no_submissions"}},
  })
  assert assignment_state_key(101, 501) in manager.state.assignments


def test_schedule_state_manager_prunes_unconfigured_assignments(tmp_path):
  manager = ScheduleStateManager(
    path=str(tmp_path / "schedule_state.yaml"), state=ScheduleState(
      assignments={
        assignment_state_key(101, 501): ScheduleStateEntry(
          last_completed_at=datetime.now(timezone.utc)),
        assignment_state_key(101, 502): ScheduleStateEntry(
          last_completed_at=datetime.now(timezone.utc)),
      }))
  manager.reconcile_configured_assignments({assignment_state_key(101, 501)})
  assert set(manager.state.assignments) == {assignment_state_key(101, 501)}


def test_schedule_state_v1_is_migrated_without_reusing_type_timestamp(tmp_path):
  state_path = tmp_path / "schedule_state.yaml"
  state_path.write_text(yaml.safe_dump({
    "version": 1,
    "assignment_types": {
      "panopto_watch": {"last_completed_at": "2026-06-25T12:05:00Z"},
    },
  }), encoding="utf-8")
  state = load_schedule_state(str(state_path))
  assert state.version == 2
  assert not state.assignments
