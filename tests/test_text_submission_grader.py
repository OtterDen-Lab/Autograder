from Autograder.graders.text_submission_grader import TextSubmissionGrader
from lms_interface.classes import Student, TextSubmission


def _submission(name: str, user_id: int) -> TextSubmission:
  return TextSubmission(student=Student(name=name, user_id=user_id, _inner=None))


def test_apply_grades_to_submissions_maps_results_by_student_id():
  grader = TextSubmissionGrader()
  submissions = [_submission("Student A", 1), _submission("Student B", 2)]

  individual_results = [{
    "student_id": 1,
    "engagement_score": 3,
    "length_score": 2,
    "relevance_score": 2,
    "explanation_quality_score": 1,
    "total_grade": 8,
    "accurate_word_count": 300,
    "topics_needing_review": [],
    "feedback": "Good effort."
  }]

  grader._apply_grades_to_submissions(submissions, individual_results)

  assert submissions[0].feedback is not None
  assert submissions[0].feedback.percentage_score == 80.0

  assert submissions[1].feedback is not None
  assert submissions[1].feedback.percentage_score == 0.0
  assert "could not analyze" in submissions[1].feedback.comments.lower()


def test_truncate_submission_text_applies_word_limit_first():
  grader = TextSubmissionGrader()
  text = " ".join(["word"] * 1100)

  truncated, was_truncated = grader._truncate_submission_text(
    text, max_words=1000, max_chars=50000)

  assert was_truncated is True
  assert len(truncated.split()) == 1000
