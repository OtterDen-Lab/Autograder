import os
import sys
from types import SimpleNamespace

import pytest

from Autograder import grade_assignments
from lms_interface.classes import Feedback, Submission, Student


def test_execute_grading_returns_empty_list_for_no_assignments():
  args = SimpleNamespace(max_workers=None)
  assert grade_assignments.execute_grading([], args) == []


def test_grade_single_assignment_blocks_quiz_flow():
  result = grade_assignments.grade_single_assignment({
    "course": None,
    "course_name": "CST",
    "yaml_assignment": {
      "id": 1
    },
    "merged_assignment": {
      "type": "quiz",
      "kind": "QuizAssignment",
      "grader": "QuizGrader"
    },
    "args": SimpleNamespace(
      do_regrade=False, merge_only=False, limit=None, test=False),
    "push_grades": False,
  })

  assert result["success"] is False
  assert "disabled" in result["error"].lower()


def test_grade_single_assignment_blocks_exam_kind():
  result = grade_assignments.grade_single_assignment({
    "course": None,
    "course_name": "CST",
    "yaml_assignment": {
      "id": 2
    },
    "merged_assignment": {
      "type": "assignment",
      "kind": "Exam",
      "grader": "Manual"
    },
    "args": SimpleNamespace(
      do_regrade=False, merge_only=False, limit=None, test=False),
    "push_grades": False,
  })

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

  result = grade_assignments.grade_single_assignment({
    "course": DummyCourse(),
    "course_name": "CST",
    "yaml_assignment": {
      "id": 42
    },
    "merged_assignment": {
      "type": "assignment",
      "kind": "ProgrammingAssignment",
      "grader": "template-grader",
      "settings": {
        "record_retention": True
      },
      "kwargs": {
        "record_retention": True
      },
    },
    "args": SimpleNamespace(
      do_regrade=False, merge_only=False, limit=None, test=False),
    "push_grades": False,
  })

  assert result["success"] is False
  assert "explicit records_dir" in result["error"]
