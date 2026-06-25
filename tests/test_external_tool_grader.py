from Autograder.graders.external_tool_grader import PanoptoWatchGrader
from lms_interface.classes import Submission, Student


def _make_submission():
  return Submission(student=Student(name="Student A", user_id=1, _inner=None))


def test_panopto_watch_grader_returns_percent_feedback():
  grader = PanoptoWatchGrader(assignment_name="Video Watch")
  submission = _make_submission()
  submission.set_extra({
    "percent_watched": 87.5,
    "watch_record_found": True,
    "viewed_seconds": 1050.0,
    "duration_seconds": 1200.0,
  })

  feedback = grader.grade_submission(submission)

  assert feedback.percentage_score == 87.5
  assert "87.5%" in feedback.comments
  assert "17.5 / 20.0 minutes" in feedback.comments


def test_panopto_watch_grader_reports_missing_record():
  grader = PanoptoWatchGrader(assignment_name="Video Watch")
  submission = _make_submission()
  submission.set_extra({
    "percent_watched": 0.0,
    "missing_user_score": 0.0,
    "watch_record_found": False,
    "canvas_identifier": "student@example.edu",
  })

  feedback = grader.grade_submission(submission)

  assert feedback.percentage_score == 0.0
  assert "No Panopto watch record was found" in feedback.comments
