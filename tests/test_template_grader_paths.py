import io
import os
from types import SimpleNamespace

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
