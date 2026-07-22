import os
import sys
import json
import logging
import contextlib
from types import SimpleNamespace
import requests

import pytest

from Autograder import grade_assignments
from Autograder import exceptions as autograder_exceptions
from Autograder.config_models import (AssignmentRunRequest, CourseConfig,
                                      RunConfig, parse_run_config)
from Autograder.grader_context import GraderContext
from Autograder.cli.validators import resolve_learning_logs_dir
from lms_interface.classes import Feedback, Submission, Student


def test_execute_grading_returns_empty_list_for_no_assignments():
  args = SimpleNamespace(max_workers=None)
  assert grade_assignments.execute_grading([], args) == []


def test_grade_single_assignment_rejects_unsupported_assignment_kind():
  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=None,
      course_name="CST",
      assignment_id=1,
      assignment_type="legacy",
      assignment_kind="LegacyAssignment",
      grader_name="template-grader",
      settings={},
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is False
  assert "not supported" in result["error"].lower()


def test_grade_single_assignment_rejects_unsupported_grader_for_kind():
  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=None,
      course_name="CST",
      assignment_id=2,
      assignment_type="assignment",
      assignment_kind="ProgrammingAssignment",
      grader_name="LegacyGrader",
      settings={},
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is False
  assert "not supported" in result["error"].lower()


def test_grade_single_assignment_wraps_assignment_lookup_as_lms_error():
  class FailingCourse:
    def get_assignment(self, assignment_id):
      raise requests.exceptions.Timeout(f"timeout for assignment {assignment_id}")

  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=FailingCourse(),
      course_name="CST",
      assignment_id=2,
      assignment_type="assignment",
      assignment_kind="ProgrammingAssignment",
      grader_name="template-grader",
      settings={},
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is False
  assert result.get("error_type") == "LMSError"
  assert "failed to load canvas assignment" in result["error"].lower()


def test_grade_single_assignment_wraps_assignment_metadata_errors_as_lms_error():
  class MaintenanceCourse:
    def get_assignment(self, assignment_id):
      raise ValueError(
        f"Canvas returned incomplete metadata for assignment id={assignment_id} (missing: name)."
      )

  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=MaintenanceCourse(),
      course_name="CST",
      assignment_id=42,
      assignment_type="assignment",
      assignment_kind="ProgrammingAssignment",
      grader_name="template-grader",
      settings={},
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is False
  assert result.get("error_type") == "LMSError"
  assert "maintenance" in result["error"].lower()


def test_parse_args_requires_yaml(monkeypatch):
  monkeypatch.setattr(sys, "argv", ["grade-assignments"])
  with pytest.raises(SystemExit) as exc:
    grade_assignments.parse_args()
  assert exc.value.code == 2


def test_parse_args_rejects_legacy_test_subcommand(monkeypatch):
  monkeypatch.setattr(sys, "argv", ["grade-assignments", "TEST"])
  with pytest.raises(SystemExit) as exc:
    grade_assignments.parse_args()
  assert exc.value.code == 2


def test_parse_args_rejects_removed_merge_only_flag(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv",
    ["grade-assignments", "--yaml",
     str(yaml_file), "--merge_only"])
  with pytest.raises(SystemExit) as exc:
    grade_assignments.parse_args()
  assert exc.value.code == 2


def test_parse_args_accepts_explicit_yaml(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("courses: []\n", encoding="utf-8")

  monkeypatch.setattr(sys, "argv",
                      ["grade-assignments", "--yaml",
                       str(yaml_file)])
  args = grade_assignments.parse_args()
  assert args.yaml == str(yaml_file)


def test_parse_args_accepts_refresh_panopto_token_command(monkeypatch):
  monkeypatch.setattr(sys, "argv",
                      ["grade-assignments", "refresh-panopto-token"])

  args = grade_assignments.parse_args()

  assert args.command == "refresh-panopto-token"
  assert args.env == os.path.expanduser("~/.tokens/autograder.env")


def test_main_runs_refresh_panopto_token_command(monkeypatch):
  args = SimpleNamespace(command="refresh-panopto-token")
  monkeypatch.setattr(grade_assignments, "parse_args", lambda: args)
  monkeypatch.setattr(grade_assignments, "refresh_panopto_token",
                      lambda received_args: 17)

  assert grade_assignments.main() == 17


def test_parse_args_accepts_reveal_identity_flag(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv",
    ["grade-assignments", "--yaml",
     str(yaml_file), "--reveal-identity"])
  args = grade_assignments.parse_args()
  assert args.reveal_identity is True


def test_parse_args_accepts_idempotency_options(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv", [
      "grade-assignments", "--yaml",
      str(yaml_file), "--idempotency-key", "run-1", "--idempotency-state-dir",
      "./.state"
    ])
  args = grade_assignments.parse_args()
  assert args.idempotency_key == "run-1"
  assert args.idempotency_state_dir == "./.state"


def test_parse_args_accepts_show_stage_timings(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv",
    ["grade-assignments", "--yaml",
     str(yaml_file), "--show-stage-timings"])
  args = grade_assignments.parse_args()
  assert args.show_stage_timings is True


def test_parse_args_accepts_dump_config(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv",
    ["grade-assignments", "--yaml",
     str(yaml_file), "--dump-config"])
  args = grade_assignments.parse_args()
  assert args.dump_config is True


def test_parse_args_accepts_dry_run(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv",
    ["grade-assignments", "--yaml",
     str(yaml_file), "--dry-run"])
  args = grade_assignments.parse_args()
  assert args.dry_run is True


def test_parse_args_accepts_skip_scheduling_check(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv",
    ["grade-assignments", "--yaml",
     str(yaml_file), "--skip-scheduling-check"])
  args = grade_assignments.parse_args()
  assert args.skip_scheduling_check is True


def test_parse_args_accepts_student_id(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv",
    ["grade-assignments", "--yaml",
     str(yaml_file), "--student-id", "12345"])
  args = grade_assignments.parse_args()
  assert args.student_id == 12345


def test_parse_args_rejects_non_positive_student_id(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv",
    ["grade-assignments", "--yaml",
     str(yaml_file), "--student-id", "0"])
  with pytest.raises(SystemExit) as exc:
    grade_assignments.parse_args()
  assert exc.value.code == 2


def test_parse_args_rejects_non_positive_max_workers(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("assignment_types: {}\ncourses: []\n", encoding="utf-8")

  monkeypatch.setattr(
    sys, "argv",
    ["grade-assignments", "--yaml",
     str(yaml_file), "--max_workers", "0"])
  with pytest.raises(SystemExit) as exc:
    grade_assignments.parse_args()
  assert exc.value.code == 2


def test_configure_logging_suppresses_external_loggers_by_default():
  grade_assignments.configure_logging(debug=False)

  assert logging.getLogger("httpx").level == logging.WARNING
  assert logging.getLogger("httpcore").level == logging.WARNING
  assert logging.getLogger("urllib3").level == logging.WARNING


def test_configure_logging_allows_external_info_in_debug():
  grade_assignments.configure_logging(debug=True)

  assert logging.getLogger("httpx").level == logging.INFO
  assert logging.getLogger("httpcore").level == logging.INFO
  assert logging.getLogger("urllib3").level == logging.INFO


def test_load_and_validate_config_error_includes_path_and_docs(tmp_path):
  yaml_file = tmp_path / "broken.yaml"
  yaml_file.write_text("courses: []\n", encoding="utf-8")

  with pytest.raises(SystemExit) as exc:
    grade_assignments.load_and_validate_config(str(yaml_file))

  message = str(exc.value)
  assert str(yaml_file) in message
  assert "documentation/instructor_onboarding.md" in message


def test_load_and_validate_config_yaml_parse_error_includes_path_and_docs(tmp_path):
  yaml_file = tmp_path / "broken_syntax.yaml"
  yaml_file.write_text("assignment_types:\n  x: [\n", encoding="utf-8")

  with pytest.raises(SystemExit) as exc:
    grade_assignments.load_and_validate_config(str(yaml_file))

  message = str(exc.value)
  assert str(yaml_file) in message
  assert "YAML parse error" in message
  assert "documentation/instructor_onboarding.md" in message


def test_record_retention_requires_explicit_records_dir(monkeypatch):
  class DummyAssignment:
    def __init__(self):
      self.submissions = [
        Submission(student=Student(name="Student A", user_id=1, _inner=None),
                   status=Submission.Status.UNGRADED)
      ]

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def prepare(self, *args, **kwargs):
      return None

    def finalize(self, *args, **kwargs):
      return None

  class DummyGrader:
    ready_to_finalize = True

    def assignment_needs_preparation(self):
      return True

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def grade_assignment(self, assignment, *args, **kwargs):
      for submission in assignment.submissions:
        submission.feedback = Feedback(percentage_score=100.0, comments="ok")

    def cleanup(self):
      return None

  class DummyLmsAssignment:
    name = "PA1"

  class DummyCourse:
    def get_assignment(self, assignment_id):
      return DummyLmsAssignment()

  monkeypatch.setattr(grade_assignments.GraderRegistry, "create",
                      lambda *args, **kwargs: DummyGrader())
  monkeypatch.setattr(grade_assignments.AssignmentRegistry, "create",
                      lambda *args, **kwargs: DummyAssignment())

  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=DummyCourse(),
      course_name="CST",
      assignment_id=42,
      assignment_type="assignment",
      assignment_kind="ProgrammingAssignment",
      grader_name="template-grader",
      settings={
        "record_retention": True
      },
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is False
  assert "explicit records_dir" in result["error"]


def test_record_retention_false_does_not_validate_records_dir(monkeypatch):
  class DummyAssignment:
    def __init__(self):
      self.submissions = [
        Submission(student=Student(name="Student A", user_id=1, _inner=None),
                   status=Submission.Status.UNGRADED)
      ]

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def prepare(self, *args, **kwargs):
      return None

    def finalize(self, *args, **kwargs):
      return None

  class DummyGrader:
    ready_to_finalize = True

    def assignment_needs_preparation(self):
      return True

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def grade_assignment(self, assignment, *args, **kwargs):
      for submission in assignment.submissions:
        submission.feedback = Feedback(percentage_score=100.0, comments="ok")

    def cleanup(self):
      return None

  class DummyLmsAssignment:
    name = "PA1"

  class DummyCourse:
    def get_assignment(self, assignment_id):
      return DummyLmsAssignment()

  monkeypatch.setattr(grade_assignments.GraderRegistry, "create",
                      lambda *args, **kwargs: DummyGrader())
  monkeypatch.setattr(grade_assignments.AssignmentRegistry, "create",
                      lambda *args, **kwargs: DummyAssignment())

  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=DummyCourse(),
      course_name="CST",
      assignment_id=42,
      assignment_type="assignment",
      assignment_kind="ProgrammingAssignment",
      grader_name="template-grader",
      settings={
        "record_retention": False,
        "records_dir": "./records/local-debug-only"
      },
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is True


def test_grade_single_assignment_passes_typed_grader_context(monkeypatch):
  created_grader = {"instance": None}

  class DummyAssignment:
    def __init__(self):
      self.submissions = []

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def prepare(self, *args, **kwargs):
      return None

    def finalize(self, *args, **kwargs):
      return {"push_failed": 0}

  class DummyGrader:
    ready_to_finalize = True

    def assignment_needs_preparation(self):
      return True

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def grade_assignment(self, assignment, *args, **kwargs):
      return None

    def cleanup(self):
      return None

  class DummyLmsAssignment:
    name = "PA1"

  class DummyCourse:
    def get_assignment(self, assignment_id):
      return DummyLmsAssignment()

  def _capture_create(*args, **kwargs):
    del args, kwargs
    instance = DummyGrader()
    created_grader["instance"] = instance
    return instance

  monkeypatch.setattr(grade_assignments.GraderRegistry, "create", _capture_create)
  monkeypatch.setattr(grade_assignments.AssignmentRegistry, "create",
                      lambda *args, **kwargs: DummyAssignment())

  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=DummyCourse(),
      course_name="CST334",
      assignment_id=99,
      assignment_type="assignment",
      assignment_kind="ProgrammingAssignment",
      grader_name="template-grader",
      settings={},
      repo_path="PA1",
      assignment_name="PA1",
      args=SimpleNamespace(
        do_regrade=False, limit=None, test=False),
      push_grades=False,
      slack_channel="C123",
      reveal_identity=True,
      privacy_mode="blind",
    ))

  assert result["success"] is True
  assert isinstance(created_grader["instance"].grader_context, GraderContext)
  assert created_grader["instance"].grader_context.course_name == "CST334"
  assert created_grader["instance"].grader_context.assignment_name == "PA1"
  assert created_grader["instance"].grader_context.reveal_identity is True
  assert created_grader["instance"].grader_context.privacy_mode == "blind"


def test_grade_single_assignment_emits_stage_contract_on_success(monkeypatch):
  class DummyAssignment:
    def __init__(self):
      self.submissions = [
        Submission(student=Student(name="Student A", user_id=1, _inner=None),
                   status=Submission.Status.UNGRADED)
      ]

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def prepare(self, *args, **kwargs):
      return None

    def finalize(self, *args, **kwargs):
      return {"push_failed": 0, "push_succeeded": 1}

  class DummyGrader:
    ready_to_finalize = True

    def assignment_needs_preparation(self):
      return True

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def grade_assignment(self, assignment, *args, **kwargs):
      for submission in assignment.submissions:
        submission.feedback = Feedback(percentage_score=100.0, comments="ok")

    def cleanup(self):
      return None

  class DummyLmsAssignment:
    name = "PA1"

  class DummyCourse:
    def get_assignment(self, assignment_id):
      return DummyLmsAssignment()

  monkeypatch.setattr(grade_assignments.GraderRegistry, "create",
                      lambda *args, **kwargs: DummyGrader())
  monkeypatch.setattr(grade_assignments.AssignmentRegistry, "create",
                      lambda *args, **kwargs: DummyAssignment())

  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=DummyCourse(),
      course_name="CST",
      assignment_id=42,
      assignment_type="assignment",
      assignment_kind="ProgrammingAssignment",
      grader_name="template-grader",
      settings={},
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is True
  assert result["stage_contract"]["prepare"]["has_submissions"] is True
  assert result["stage_contract"]["prepare"]["duration_ms"] >= 0
  assert result["stage_contract"]["grade"]["graded_count"] == 1
  assert result["stage_contract"]["grade"]["duration_ms"] >= 0
  assert result["stage_contract"]["publish"]["finalized"] is True
  assert result["stage_contract"]["publish"]["duration_ms"] >= 0


def test_grade_single_assignment_no_submissions_stage_contract(monkeypatch):
  class DummyAssignment:
    def __init__(self):
      self.submissions = []

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def prepare(self, *args, **kwargs):
      return None

    def finalize(self, *args, **kwargs):
      raise AssertionError("finalize should not be called")

  class DummyGrader:
    ready_to_finalize = True

    def assignment_needs_preparation(self):
      return True

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def grade_assignment(self, assignment, *args, **kwargs):
      raise AssertionError("grade_assignment should not be called")

    def cleanup(self):
      return None

  class DummyLmsAssignment:
    name = "PA1"

  class DummyCourse:
    def get_assignment(self, assignment_id):
      return DummyLmsAssignment()

  monkeypatch.setattr(grade_assignments.GraderRegistry, "create",
                      lambda *args, **kwargs: DummyGrader())
  monkeypatch.setattr(grade_assignments.AssignmentRegistry, "create",
                      lambda *args, **kwargs: DummyAssignment())

  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=DummyCourse(),
      course_name="CST",
      assignment_id=42,
      assignment_type="assignment",
      assignment_kind="ProgrammingAssignment",
      grader_name="template-grader",
      settings={},
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is True
  assert result["stage_contract"]["prepare"]["has_submissions"] is False
  assert result["stage_contract"]["prepare"]["skipped_reason"] == "no_submissions"
  assert result["stage_contract"]["grade"] is None
  assert result["stage_contract"]["publish"] is None


def test_resolve_reveal_identity_defaults_to_false():
  args = SimpleNamespace(reveal_identity=False)
  config = RunConfig(reveal_identity=False)
  assert grade_assignments.resolve_reveal_identity(args, config) is False


def test_resolve_reveal_identity_requires_break_glass(monkeypatch):
  args = SimpleNamespace(reveal_identity=True)
  config = RunConfig(reveal_identity=False)
  monkeypatch.delenv("AUTOGRADER_BREAK_GLASS", raising=False)

  with pytest.raises(SystemExit):
    grade_assignments.resolve_reveal_identity(args, config)


def test_resolve_reveal_identity_with_break_glass(monkeypatch):
  args = SimpleNamespace(reveal_identity=False, yaml="/tmp/config.yaml")
  config = RunConfig(reveal_identity=True)
  monkeypatch.setenv("AUTOGRADER_BREAK_GLASS", "1")
  monkeypatch.setenv("AUTOGRADER_REVEAL_AUDIT_LOG", "/tmp/reveal_audit_test.log")
  assert grade_assignments.resolve_reveal_identity(args, config) is True


def test_resolve_reveal_identity_writes_audit_event(monkeypatch, tmp_path):
  audit_path = tmp_path / "reveal_audit.log"
  args = SimpleNamespace(reveal_identity=True, yaml="/tmp/config.yaml")
  config = RunConfig(reveal_identity=False, privacy_mode="blind", prod=False)
  monkeypatch.setenv("AUTOGRADER_BREAK_GLASS", "1")
  monkeypatch.setenv("AUTOGRADER_REVEAL_AUDIT_LOG", str(audit_path))

  assert grade_assignments.resolve_reveal_identity(args, config) is True
  lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
  assert lines
  payload = json.loads(lines[-1])
  assert payload["yaml_path"] == "/tmp/config.yaml"
  assert payload["privacy_mode"] == "blind"


def test_resolve_idempotency_settings_uses_cli_over_config():
  args = SimpleNamespace(idempotency_key="  run-2  ",
                         idempotency_state_dir="~/grader_state")
  config = RunConfig(idempotency_key="run-1",
                     idempotency_state_dir="/tmp/unused")
  key, state_dir = grade_assignments.resolve_idempotency_settings(args, config)
  assert key == "run-2"
  assert state_dir.endswith("grader_state")


def test_resolve_idempotency_settings_uses_config_defaults():
  args = SimpleNamespace(idempotency_key=None, idempotency_state_dir=None)
  config = RunConfig(idempotency_key="run-1",
                     idempotency_state_dir="~/.autograder/idempotency")
  key, state_dir = grade_assignments.resolve_idempotency_settings(args, config)
  assert key == "run-1"
  assert state_dir.endswith(".autograder/idempotency")


def test_collect_assignments_wraps_canvas_interface_initialization_errors(
    monkeypatch):
  class FailingCanvasInterface:
    def __init__(self, *args, **kwargs):
      raise ValueError("missing credentials")

  monkeypatch.setattr(grade_assignments, "CanvasInterface",
                      FailingCanvasInterface)

  args = SimpleNamespace(env=None,
                         reveal_identity=False,
                         idempotency_key=None,
                         idempotency_state_dir=None)
  config = RunConfig(courses=[])

  with pytest.raises(autograder_exceptions.LMSError,
                     match="Failed to initialize Canvas interface"):
    grade_assignments.collect_assignments_to_grade(config, args)


def test_collect_assignments_wraps_course_lookup_errors(monkeypatch):
  class FailingCanvasInterface:
    def __init__(self, *args, **kwargs):
      pass

    def get_course(self, course_id):
      raise requests.exceptions.Timeout(f"timeout for {course_id}")

  monkeypatch.setattr(grade_assignments, "CanvasInterface",
                      FailingCanvasInterface)

  args = SimpleNamespace(env=None,
                         reveal_identity=False,
                         idempotency_key=None,
                         idempotency_state_dir=None)
  config = RunConfig(courses=[CourseConfig(id=101, name="CST334")])

  with pytest.raises(autograder_exceptions.LMSError,
                     match="Failed to load Canvas course id=101"):
    grade_assignments.collect_assignments_to_grade(config, args)


def test_collect_assignments_skips_not_due_assignment_types(monkeypatch):
  class DummyCourse:
    name = "CST334"

  class DummyCanvasInterface:
    def __init__(self, *args, **kwargs):
      pass

    def get_course(self, _):
      return DummyCourse()

  class DummyScheduleManager:
    def is_assignment_type_due(self, assignment_type_name, schedule):
      return False

  monkeypatch.setattr(grade_assignments, "CanvasInterface", DummyCanvasInterface)

  config = parse_run_config({
    "assignment_types": {
      "programming": {
        "kind": "ProgrammingAssignment",
        "grader": "template-grader",
        "schedule": {
          "timezone": "UTC",
          "rrule": "FREQ=DAILY;BYHOUR=0;BYMINUTE=0;BYSECOND=0",
        }
      }
    },
    "courses": [{
      "id": 101,
      "name": "CST334",
      "assignment_groups": [{
        "type": "programming",
        "assignments": [{
          "id": 555
        }]
      }]
    }]
  })
  args = SimpleNamespace(env=None,
                         reveal_identity=False,
                         idempotency_key=None,
                         idempotency_state_dir=None,
                         schedule_state_manager=DummyScheduleManager())

  assignments = grade_assignments.collect_assignments_to_grade(config, args)

  assert assignments == []


def test_collect_assignments_force_schedule_ignores_due_checks(monkeypatch):
  class DummyCourse:
    name = "CST334"

  class DummyCanvasInterface:
    def __init__(self, *args, **kwargs):
      pass

    def get_course(self, _):
      return DummyCourse()

  class DummyScheduleManager:
    def is_assignment_type_due(self, assignment_type_name, schedule):
      return False

  monkeypatch.setattr(grade_assignments, "CanvasInterface", DummyCanvasInterface)

  config = parse_run_config({
    "assignment_types": {
      "programming": {
        "kind": "ProgrammingAssignment",
        "grader": "template-grader",
        "schedule": {
          "timezone": "America/Los_Angeles",
          "rrule": "FREQ=DAILY;BYHOUR=0;BYMINUTE=0;BYSECOND=0",
        }
      }
    },
    "courses": [{
      "id": 101,
      "name": "CST334",
      "assignment_groups": [{
        "type": "programming",
        "assignments": [{
          "id": 555
        }]
      }]
    }]
  })
  args = SimpleNamespace(env=None,
                         reveal_identity=False,
                         idempotency_key=None,
                         idempotency_state_dir=None,
                         schedule_state_manager=DummyScheduleManager(),
                         skip_scheduling_check=True)

  assignments = grade_assignments.collect_assignments_to_grade(config, args)

  assert len(assignments) == 1
  assert assignments[0].assignment_id == 555


def test_resolve_records_dir_requires_absolute_path():
  with pytest.raises(ValueError) as exc:
    grade_assignments.resolve_records_dir("./records/local")
  assert "absolute path" in str(exc.value)


def test_resolve_records_dir_blocks_repo_subpath(monkeypatch):
  monkeypatch.delenv("AUTOGRADER_ALLOW_IN_REPO_RECORDS", raising=False)
  repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  records_path = os.path.join(repo_root, "records", "workhorse")

  with pytest.raises(ValueError) as exc:
    grade_assignments.resolve_records_dir(records_path)
  assert "outside the repository root" in str(exc.value)


def test_resolve_records_dir_allows_repo_subpath_with_override(monkeypatch):
  monkeypatch.setenv("AUTOGRADER_ALLOW_IN_REPO_RECORDS", "1")
  repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  records_path = os.path.join(repo_root, "records", "workhorse")

  resolved = grade_assignments.resolve_records_dir(records_path)
  assert resolved.endswith(os.path.join("records", "workhorse"))


def test_resolve_learning_logs_dir_requires_safe_absolute_path(monkeypatch):
  with pytest.raises(ValueError, match="absolute path"):
    resolve_learning_logs_dir("./learning-logs")

  monkeypatch.delenv("AUTOGRADER_ALLOW_IN_REPO_RECORDS", raising=False)
  repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  with pytest.raises(ValueError, match="outside the repository root"):
    resolve_learning_logs_dir(os.path.join(repo_root, "learning-logs"))


def test_collect_push_failure_lines_summarizes_results():
  total, lines = grade_assignments.collect_push_failure_lines([
    {
      "success": True,
      "course_name": "CST334",
      "assignment_name": "PA1",
      "finalize_summary": {
        "push_failed": 2,
        "push_failed_students": ["Student 10", "Student 11"]
      }
    },
    {
      "success": True,
      "course_name": "CST334",
      "assignment_name": "PA2",
      "finalize_summary": {
        "push_failed": 0
      }
    },
  ])
  assert total == 2
  assert len(lines) == 1
  assert "PA1" in lines[0]
  assert "Student 10" in lines[0]


def test_build_dump_config_payload_includes_effective_assignment_settings():
  args = SimpleNamespace(yaml="/tmp/config.yaml")
  config = RunConfig(
    prod=True,
    push=True,
    privacy_mode="blind",
    reveal_identity=False,
    idempotency_key=None,
    idempotency_state_dir="~/.autograder/idempotency",
  )

  assignment = AssignmentRunRequest(
    course=SimpleNamespace(name="CST334"),
    course_name="CST334",
    assignment_id=123,
    assignment_type="programming",
    assignment_kind="ProgrammingAssignment",
    grader_name="template-grader",
    settings={"base_image_name": "python:3.12"},
    repo_path="PA1",
    assignment_name="PA1",
    args=SimpleNamespace(),
    push_grades=True,
    slack_channel="C123",
    reveal_identity=True,
    privacy_mode="blind",
    idempotency_key="run-42",
    idempotency_state_dir="/tmp/idempotency",
  )

  payload = grade_assignments.build_dump_config_payload(config, [assignment], args)
  assert payload["yaml_path"] == "/tmp/config.yaml"
  assert payload["run"]["assignment_count"] == 1
  assert payload["run"]["privacy_mode"] == "blind"
  assert payload["run"]["reveal_identity"] is True
  assert payload["assignments"][0]["assignment_id"] == 123
  assert payload["assignments"][0]["push_grades"] is True
  assert payload["assignments"][0]["settings"]["base_image_name"] == "python:3.12"


def test_print_dry_run_summary_logs_plan(monkeypatch):
  messages = []

  monkeypatch.setattr(grade_assignments.log, "info",
                      lambda msg: messages.append(msg))
  assignment = AssignmentRunRequest(
    course=None,
    course_name="CST334",
    assignment_id=123,
    assignment_type="programming",
    assignment_kind="ProgrammingAssignment",
    grader_name="template-grader",
    settings={},
    repo_path="PA1",
    assignment_name="PA1",
    args=SimpleNamespace(),
    push_grades=True,
    slack_channel=None,
  )

  grade_assignments.print_dry_run_summary([assignment])

  rendered = "\n".join(messages)
  assert "Dry-run mode enabled" in rendered
  assert "CST334 / PA1" in rendered
  assert "kind=ProgrammingAssignment" in rendered
  assert "grader=template-grader" in rendered


def test_main_dry_run_skips_execute_grading(monkeypatch):
  args = SimpleNamespace(
    yaml="config.yaml",
    env=None,
    limit=None,
    do_regrade=False,
    max_workers=None,
    test=False,
    report=None,
    error_slack_channel=None,
    debug=False,
    show_stage_timings=False,
    reveal_identity=False,
    idempotency_key=None,
    idempotency_state_dir=None,
    dump_config=False,
    dry_run=True,
  )
  cleaned = {"called": False}
  dry_run_called = {"called": False}
  execute_called = {"called": False}
  assignments = [
    AssignmentRunRequest(
      course=None,
      course_name="CST334",
      assignment_id=123,
      assignment_type="programming",
      assignment_kind="ProgrammingAssignment",
      grader_name="template-grader",
      settings={},
      repo_path="PA1",
      assignment_name="PA1",
      args=args,
      push_grades=False,
      slack_channel=None,
    )
  ]

  @contextlib.contextmanager
  def fake_lock():
    yield

  def fake_execute(assignments_to_grade, parsed_args):
    execute_called["called"] = True
    return []

  monkeypatch.setattr(grade_assignments, "parse_args", lambda: args)
  monkeypatch.setattr(grade_assignments, "ensure_single_instance", fake_lock)
  monkeypatch.setattr(grade_assignments, "load_and_validate_config",
                      lambda _: RunConfig())
  monkeypatch.setattr(grade_assignments, "collect_assignments_to_grade",
                      lambda _config, _args: assignments)
  monkeypatch.setattr(grade_assignments, "execute_grading", fake_execute)
  monkeypatch.setattr(
    grade_assignments, "print_dry_run_summary",
    lambda _assignments: dry_run_called.__setitem__("called", True))
  monkeypatch.setattr(
    grade_assignments.DockerClient, "cleanup",
    lambda: cleaned.__setitem__("called", True))

  exit_code = grade_assignments.main()

  assert exit_code == 0
  assert dry_run_called["called"] is True
  assert execute_called["called"] is False
  assert cleaned["called"] is True


def test_main_mocked_smoke_run_without_canvas_credentials(monkeypatch, tmp_path):
  yaml_path = tmp_path / "smoke.yaml"
  yaml_path.write_text(
    """
assignment_types:
  programming:
    kind: ProgrammingAssignment
    grader: template-grader
courses:
  - id: 101
    name: CST334
    assignment_groups:
      - type: programming
        assignments:
          - id: 555
            assignment_name: PA1
push: true
privacy_mode: blind
""",
    encoding="utf-8",
  )

  args = SimpleNamespace(
    yaml=str(yaml_path),
    env=None,
    limit=None,
    do_regrade=False,
    max_workers=1,
    test=False,
    report=None,
    error_slack_channel=None,
    debug=False,
    show_stage_timings=False,
    reveal_identity=False,
    idempotency_key=None,
    idempotency_state_dir=None,
    dump_config=False,
    dry_run=False,
  )

  calls = {
    "canvas_init": None,
    "prepare": 0,
    "grade": 0,
    "finalize": 0,
    "cleanup": 0,
    "docker_cleanup": 0,
    "push_flag": None,
  }

  class DummyLmsAssignment:
    name = "PA1"

  class DummyCourse:
    name = "CST334"

    def get_assignment(self, assignment_id):
      assert assignment_id == 555
      return DummyLmsAssignment()

  class DummyCanvasInterface:
    def __init__(self, *args, **kwargs):
      calls["canvas_init"] = kwargs

    def get_course(self, course_id):
      assert course_id == 101
      return DummyCourse()

  class DummyAssignment:
    def __init__(self):
      self.submissions = []

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def prepare(self, *args, **kwargs):
      calls["prepare"] += 1
      self.submissions = [
        Submission(student=Student(name="Anon 0001", user_id=1, _inner=None),
                   status=Submission.Status.UNGRADED)
      ]

    def finalize(self, **kwargs):
      calls["finalize"] += 1
      calls["push_flag"] = kwargs.get("push")
      graded = sum(1 for s in self.submissions if s.feedback is not None)
      return {
        "push_enabled": bool(kwargs.get("push")),
        "push_attempted": len(self.submissions),
        "push_succeeded": graded,
        "push_failed": len(self.submissions) - graded,
        "push_skipped": 0,
      }

  class DummyGrader:
    ready_to_finalize = True

    def assignment_needs_preparation(self):
      return True

    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc_val, exc_tb):
      return False

    def grade_assignment(self, assignment, *args, **kwargs):
      calls["grade"] += 1
      for submission in assignment.submissions:
        submission.feedback = Feedback(percentage_score=100.0, comments="ok")

    def cleanup(self):
      calls["cleanup"] += 1

  @contextlib.contextmanager
  def fake_lock():
    yield

  monkeypatch.setattr(grade_assignments, "parse_args", lambda: args)
  monkeypatch.setattr(grade_assignments, "ensure_single_instance", fake_lock)
  monkeypatch.setattr(grade_assignments, "CanvasInterface", DummyCanvasInterface)
  monkeypatch.setattr(grade_assignments.GraderRegistry, "create",
                      lambda *a, **k: DummyGrader())
  monkeypatch.setattr(grade_assignments.AssignmentRegistry, "create",
                      lambda *a, **k: DummyAssignment())
  monkeypatch.setattr(
    grade_assignments.DockerClient, "cleanup",
    lambda: calls.__setitem__("docker_cleanup", calls["docker_cleanup"] + 1))

  exit_code = grade_assignments.main()

  assert exit_code == 0
  assert calls["canvas_init"]["privacy_mode"] == "blind"
  assert calls["canvas_init"]["reveal_identity"] is False
  assert calls["prepare"] == 1
  assert calls["grade"] == 1
  assert calls["finalize"] == 1
  assert calls["push_flag"] is True
  assert calls["cleanup"] == 1
  assert calls["docker_cleanup"] == 1


def test_main_returns_nonzero_when_push_failures_present(monkeypatch):
  args = SimpleNamespace(
    yaml="config.yaml",
    env=None,
    limit=None,
    do_regrade=False,
    max_workers=1,
    test=False,
    report=None,
    error_slack_channel=None,
    debug=False,
    show_stage_timings=False,
    reveal_identity=False,
    idempotency_key=None,
    idempotency_state_dir=None,
    dump_config=False,
    dry_run=False,
    list_graders=False,
  )

  cleaned = {"called": False}

  @contextlib.contextmanager
  def fake_lock():
    yield

  results = [{
    "success": True,
    "assignment_id": 42,
    "assignment_name": "PA1",
    "course_name": "CST334",
    "finalize_summary": {
      "push_failed": 1,
      "push_failed_students": ["Student 1"],
    },
  }]

  monkeypatch.setattr(grade_assignments, "parse_args", lambda: args)
  monkeypatch.setattr(grade_assignments, "ensure_single_instance", fake_lock)
  monkeypatch.setattr(grade_assignments, "load_and_validate_config",
                      lambda _: RunConfig())
  monkeypatch.setattr(grade_assignments, "collect_assignments_to_grade",
                      lambda _config, _args: [])
  monkeypatch.setattr(grade_assignments, "execute_grading",
                      lambda _assignments, _args: results)
  monkeypatch.setattr(grade_assignments, "print_results_summary",
                      lambda _results: None)
  monkeypatch.setattr(grade_assignments, "write_run_report",
                      lambda _results, _args: None)
  monkeypatch.setattr(grade_assignments, "send_slack_run_summary",
                      lambda _results, _args, _config: None)
  monkeypatch.setattr(
    grade_assignments.DockerClient, "cleanup",
    lambda: cleaned.__setitem__("called", True))

  exit_code = grade_assignments.main()

  assert exit_code == 1
  assert cleaned["called"] is True


def test_send_slack_run_summary_notifies_on_push_failures(monkeypatch):
  monkeypatch.setenv("SLACK_BOT_TOKEN", "token")
  sent = {}

  class DummyResponse:
    def json(self):
      return {"ok": True}

  def fake_post(url, headers=None, json=None, timeout=None):
    sent["url"] = url
    sent["json"] = json
    return DummyResponse()

  monkeypatch.setattr(grade_assignments.requests, "post", fake_post)

  args = SimpleNamespace(error_slack_channel=None, yaml="config.yaml")
  config = RunConfig(reporting={"slack_channel": "C123", "notify_on": "failures"})
  results = [{
    "success": True,
    "course_name": "CST334",
    "assignment_name": "PA1",
    "finalize_summary": {
      "push_failed": 1,
      "push_failed_students": ["Student 10"]
    }
  }]

  grade_assignments.send_slack_run_summary(results, args, config)

  assert sent["url"].endswith("/chat.postMessage")
  assert "Per-student push failures:" in sent["json"]["text"]


def test_write_run_report_includes_stage_and_push_summaries(tmp_path):
  report_path = tmp_path / "run_report.json"
  results = [{
    "success": True,
    "assignment_name": "PA1",
    "assignment_id": 42,
    "course_name": "CST334",
    "finalize_summary": {
      "push_failed": 1,
      "push_failed_students": ["Student 10"],
      "push_attempted": 2,
      "push_succeeded": 1,
      "push_skipped": 0,
    },
    "stage_contract": {
      "prepare": {
        "submission_count": 2,
        "duration_ms": 10
      },
      "grade": {
        "submission_count": 2,
        "graded_count": 2,
        "duration_ms": 20
      },
      "publish": {
        "duration_ms": 5,
        "finalize_summary": {
          "push_attempted": 2,
          "push_succeeded": 1,
          "push_failed": 1,
          "push_skipped": 0,
        }
      },
    },
  }]
  args = SimpleNamespace(report=str(report_path), yaml="config.yaml")

  grade_assignments.write_run_report(results, args)

  payload = json.loads(report_path.read_text(encoding="utf-8"))
  assert payload["summary"]["push_failures_total"] == 1
  assert "PA1" in payload["summary"]["push_failures"][0]
  assert payload["summary"]["stage_contracts"]["prepare"]["count"] == 1
  assert payload["summary"]["stage_contracts"]["prepare"]["total_duration_ms"] == 10
  assert payload["summary"]["stage_contracts"]["publish"]["total_push_attempted"] == 2
