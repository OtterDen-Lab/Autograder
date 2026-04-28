import io
import os
from types import SimpleNamespace

import pytest

from Autograder.graders.docker_graders import Grader__docker, Grader__template_grader
from lms_interface.classes import Feedback


def _make_file(name: str, content: bytes = b"data") -> io.BytesIO:
  buffer = io.BytesIO(content)
  buffer.name = name
  return buffer


def test_template_grader_student_code_path_keeps_original_filenames(
    monkeypatch):
  captured = {}

  def fake_parent_grade_submission(self, submission, files_to_copy=None, *args,
                                   **kwargs):
    captured["files_to_copy"] = files_to_copy
    return Feedback(percentage_score=100.0, comments="ok")

  monkeypatch.setattr(Grader__docker, "grade_submission",
                      fake_parent_grade_submission)

  grader = object.__new__(Grader__template_grader)
  grader.file_paths = {}
  grader.student_code_path = "src"
  grader.assignment_name = "PA1"
  grader.container_repo_path = "/repo/programming-assignments"

  submission = SimpleNamespace(
    files=[_make_file("main.c"),
           _make_file("util.h")])

  feedback = grader.grade_submission(submission)

  assert isinstance(feedback, Feedback)
  assert feedback.percentage_score == 100.0
  assert len(captured["files_to_copy"]) == 2

  target_paths = [entry[1] for entry in captured["files_to_copy"]]
  assert os.path.join("/repo/programming-assignments/PA1", "src",
                      "main.c") in target_paths
  assert os.path.join("/repo/programming-assignments/PA1", "src",
                      "util.h") in target_paths


def test_template_grader_student_code_path_honors_container_repo_path_override(
    monkeypatch):
  captured = {}

  def fake_parent_grade_submission(self, submission, files_to_copy=None, *args,
                                   **kwargs):
    captured["files_to_copy"] = files_to_copy
    return Feedback(percentage_score=100.0, comments="ok")

  monkeypatch.setattr(Grader__docker, "grade_submission",
                      fake_parent_grade_submission)

  grader = object.__new__(Grader__template_grader)
  grader.file_paths = {}
  grader.student_code_path = "src"
  grader.assignment_name = "PA1"
  grader.container_repo_path = "/repo/course-templates"

  submission = SimpleNamespace(files=[_make_file("main.c")])
  feedback = grader.grade_submission(submission)

  assert isinstance(feedback, Feedback)
  target_paths = [entry[1] for entry in captured["files_to_copy"]]
  assert os.path.join("/repo/course-templates/PA1", "src",
                      "main.c") in target_paths


def test_template_grader_student_code_path_supports_single_file_target(
    monkeypatch):
  captured = {}

  def fake_parent_grade_submission(self, submission, files_to_copy=None, *args,
                                   **kwargs):
    captured["files_to_copy"] = files_to_copy
    return Feedback(percentage_score=100.0, comments="ok")

  monkeypatch.setattr(Grader__docker, "grade_submission",
                      fake_parent_grade_submission)

  grader = object.__new__(Grader__template_grader)
  grader.file_paths = {}
  grader.student_code_path = "src/student_code.c"
  grader.assignment_name = "PA3"
  grader.container_repo_path = "/repo/programming-assignments"

  submission = SimpleNamespace(files=[_make_file("upload.c")])
  feedback = grader.grade_submission(submission)

  assert isinstance(feedback, Feedback)
  target_paths = [entry[1] for entry in captured["files_to_copy"]]
  assert target_paths == [
    os.path.join("/repo/programming-assignments/PA3", "src", "student_code.c")
  ]


def test_template_grader_student_code_path_file_target_rejects_multi_file_submission(
    monkeypatch):
  def fake_parent_grade_submission(self, submission, files_to_copy=None, *args,
                                   **kwargs):
    raise AssertionError("Parent grade_submission should not be called")

  monkeypatch.setattr(Grader__docker, "grade_submission",
                      fake_parent_grade_submission)

  grader = object.__new__(Grader__template_grader)
  grader.file_paths = {}
  grader.student_code_path = "src/student_code.c"
  grader.assignment_name = "PA3"
  grader.container_repo_path = "/repo/programming-assignments"

  submission = SimpleNamespace(files=[_make_file("a.c"), _make_file("b.c")])
  feedback = grader.grade_submission(submission)

  assert isinstance(feedback, Feedback)
  assert feedback.percentage_score == 0.0
  assert "Configuration error" in feedback.comments
  assert "appears to be a file target" in feedback.comments


def test_template_grader_validates_container_repo_path():
  assert (Grader__template_grader._normalize_container_repo_path(
    "/repo/programming-assignments/") == "/repo/programming-assignments")

  with pytest.raises(ValueError):
    Grader__template_grader._normalize_container_repo_path(
      "repo/programming-assignments")

  with pytest.raises(ValueError):
    Grader__template_grader._normalize_container_repo_path("/opt/assignments")


def test_template_grader_resolve_local_path_prefers_longest_mount(tmp_path):
  resolved = Grader__template_grader._resolve_local_path_for_container_path(
    str(tmp_path),
    "/repo/tools/programming-assignments/PA1",
    [{
      "container_path": "/repo",
      "context_dir": "repo",
    }, {
      "container_path": "/repo/tools",
      "context_dir": "repo_extra_0",
    }])

  assert resolved == os.path.join(str(tmp_path), "repo_extra_0",
                                  "programming-assignments", "PA1")


def test_template_grader_normalizes_additional_repos():
  grader = object.__new__(Grader__template_grader)
  normalized = grader._normalize_additional_repos([{
    "source_repo": "https://example.com/tools.git",
    "container_path": "/repo/tools/",
  }])

  assert normalized == [{
    "source_repo": "https://example.com/tools.git",
    "container_path": "/repo/tools",
    "depth": 1,
  }]

  with pytest.raises(ValueError):
    grader._normalize_additional_repos([{
      "source_repo": "https://example.com/a.git",
      "container_path": "/repo/tools"
    }, {
      "source_repo": "https://example.com/b.git",
      "container_path": "/repo/tools/sub"
    }])


def test_template_grader_file_paths_supports_path_as_full_target_file():
  grader = object.__new__(Grader__template_grader)
  grader.assignment_name = "PA1"
  grader.container_repo_path = "/repo/programming-assignments"
  grader.file_paths = {
    r".*_1.*\.java": {
      "path": "part1/hw3_1.java",
    }
  }

  submission = SimpleNamespace(files=[_make_file("student_1_submission.java")])
  files_to_copy, error = grader._match_files_to_paths(submission)

  assert error is None
  assert len(files_to_copy) == 1
  assert files_to_copy[0][1] == os.path.join("/repo/programming-assignments/PA1",
                                             "part1", "hw3_1.java")


def test_template_grader_file_paths_with_empty_path_and_no_name_errors():
  grader = object.__new__(Grader__template_grader)
  grader.assignment_name = "PA1"
  grader.container_repo_path = "/repo/programming-assignments"
  grader.file_paths = {
    r".*\.java": {
      "path": "",
    }
  }

  submission = SimpleNamespace(files=[_make_file("student_1_submission.java")])
  files_to_copy, error = grader._match_files_to_paths(submission)

  assert files_to_copy == []
  assert error is not None
  assert "empty path and no name" in error


def test_template_grader_context_image_tag_sanitizes_names(monkeypatch):
  monkeypatch.setattr("Autograder.graders.docker_graders.uuid.uuid4",
                      lambda: SimpleNamespace(hex="abc123"))

  grader = object.__new__(Grader__template_grader)
  grader.course_name = "CST363-02_2262"
  grader.assignment_name = "SQL HW 1"

  assert grader._build_context_image_tag(
  ) == "template-grader:cst363-02_2262-sql-hw-1-abc123"


def test_template_grader_default_script_includes_grading_args():
  command = Grader__template_grader._build_default_grading_script(
    "PA1", ["--files", "main.c", "utils lib.c"])
  assert "/repo/scripts/grader.py --PA PA1 --files main.c 'utils lib.c'" in command
  assert "/repo/.venv/bin/python" in command
  assert "python3 /repo/scripts/grader.py" in command
  assert "python /repo/scripts/grader.py" in command


def test_template_grader_uv_bootstrap_command_handles_missing_pyproject():
  command = Grader__template_grader._uv_bootstrap_command()
  assert "if [ -f pyproject.toml ]" in command
  assert "uv sync" in command
  assert "apt-get install -y --no-install-recommends" in command
  assert "build-essential" in command
  assert "libpq-dev" in command
  assert "skipping uv sync" in command


def test_template_grader_score_grading_attaches_optional_rubric(monkeypatch):
  grader = object.__new__(Grader__template_grader)
  grader.container = object()
  grader.read_file_from_container = lambda path: (
    "grade: 88\n"
    "comments: Great work.\n"
    "rubric:\n"
    "  criterion 1: 2\n"
    "  crit2:\n"
    "    points: 3\n"
  )
  grader._report_grading_error = lambda *args, **kwargs: pytest.fail(
    "Unexpected grading error")

  feedback = grader.score_grading((0, "", ""))

  assert feedback.percentage_score == 88.0
  assert feedback.comments == "Great work."
  assert getattr(feedback, "rubric_assessment") == {
    "criterion 1": 2,
    "crit2": {
      "points": 3,
    },
  }
