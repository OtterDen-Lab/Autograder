from types import SimpleNamespace

from Autograder.grader import Grader
from lms_interface.classes import Feedback, Submission, Student


class _BaseTestGrader(Grader):
  def can_grade_submission(self, submission: Submission) -> bool:
    return True


class RecordingGrader(_BaseTestGrader):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.last_execute_submission = None
    self.last_score_submission = None

  def execute_grading(self, *args, **kwargs):
    self.last_execute_submission = kwargs.get("submission")
    return {"ok": True}

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    self.last_score_submission = kwargs.get("submission")
    return Feedback(percentage_score=100.0, comments="ok")


class SometimesExplodingGrader(_BaseTestGrader):
  def execute_grading(self, *args, **kwargs):
    submission = kwargs.get("submission")
    if submission.student.user_id == 1:
      raise RuntimeError("boom")
    return {"ok": True}

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    return Feedback(percentage_score=100.0, comments="ok")


def _make_assignment(submissions):
  return SimpleNamespace(
    submissions=submissions,
    lms_assignment=SimpleNamespace(canvas_course=SimpleNamespace(name="Course")),
  )


def _make_submission(name: str, user_id: int) -> Submission:
  return Submission(student=Student(name=name, user_id=user_id, _inner=None))


def test_grade_submission_passes_submission_to_execute_and_score():
  grader = RecordingGrader(assignment_name="PA1")
  submission = _make_submission("Student A", 1)

  feedback = grader.grade_submission(submission)

  assert isinstance(feedback, Feedback)
  assert grader.last_execute_submission is submission
  assert grader.last_score_submission is submission


def test_grade_assignment_isolates_submission_failures():
  grader = SometimesExplodingGrader(assignment_name="PA1")
  failing = _make_submission("Student A", 1)
  passing = _make_submission("Student B", 2)

  grader.grade_assignment(_make_assignment([failing, passing]))

  assert failing.feedback is not None
  assert failing.feedback.percentage_score == 0.0
  assert "internal error" in failing.feedback.comments.lower()

  assert passing.feedback is not None
  assert passing.feedback.percentage_score == 100.0
