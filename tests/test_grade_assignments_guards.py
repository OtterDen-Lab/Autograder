import os
import sys
from types import SimpleNamespace

import pytest

from Autograder import grade_assignments
from Autograder.config_models import AssignmentRunRequest, RunConfig
from lms_interface.classes import Feedback, Submission, Student


def test_execute_grading_returns_empty_list_for_no_assignments():
  args = SimpleNamespace(max_workers=None)
  assert grade_assignments.execute_grading([], args) == []


def test_grade_single_assignment_blocks_quiz_flow():
  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=None,
      course_name="CST",
      assignment_id=1,
      assignment_type="quiz",
      assignment_kind="QuizAssignment",
      grader_name="QuizGrader",
      settings={},
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, merge_only=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is False
  assert "disabled" in result["error"].lower()


def test_grade_single_assignment_blocks_exam_kind():
  result = grade_assignments.grade_single_assignment(
    AssignmentRunRequest(
      course=None,
      course_name="CST",
      assignment_id=2,
      assignment_type="assignment",
      assignment_kind="Exam",
      grader_name="Dummy",
      settings={},
      repo_path=None,
      assignment_name=None,
      args=SimpleNamespace(
        do_regrade=False, merge_only=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is False
  assert "disabled" in result["error"].lower()


def test_parse_args_requires_yaml_when_not_using_test(monkeypatch):
  monkeypatch.setattr(sys, "argv", ["grade-assignments"])
  with pytest.raises(SystemExit) as exc:
    grade_assignments.parse_args()
  assert exc.value.code == 2


def test_parse_args_test_command_sets_expected_defaults(monkeypatch):
  monkeypatch.setattr(sys, "argv", ["grade-assignments", "TEST"])
  args = grade_assignments.parse_args()
  assert args.command == "TEST"
  assert args.do_regrade is True
  assert args.test is True
  assert args.max_workers == 1
  assert args.yaml.endswith(os.path.join("example_files", "learning-logs.yaml"))
  assert os.path.isfile(args.yaml)


def test_parse_args_accepts_explicit_yaml(monkeypatch, tmp_path):
  yaml_file = tmp_path / "config.yaml"
  yaml_file.write_text("courses: []\n", encoding="utf-8")

  monkeypatch.setattr(sys, "argv",
                      ["grade-assignments", "--yaml",
                       str(yaml_file)])
  args = grade_assignments.parse_args()
  assert args.yaml == str(yaml_file)


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
        do_regrade=False, merge_only=False, limit=None, test=False),
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
        do_regrade=False, merge_only=False, limit=None, test=False),
      push_grades=False,
      slack_channel=None,
    ))

  assert result["success"] is True


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
  args = SimpleNamespace(reveal_identity=False)
  config = RunConfig(reveal_identity=True)
  monkeypatch.setenv("AUTOGRADER_BREAK_GLASS", "1")
  assert grade_assignments.resolve_reveal_identity(args, config) is True


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
