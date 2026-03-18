from Autograder.assignment import ProgrammingAssignment, TextAssignment
from lms_interface.classes import Student, Submission, TextSubmission


class DummyLmsAssignment:
  def __init__(self, submissions):
    self.name = "Test Assignment"
    self._submissions = list(submissions)
    self.calls = []

  def get_submissions(self, **kwargs):
    self.calls.append(kwargs)
    return list(self._submissions)


def _make_programming_submission(user_id, status):
  return Submission(
    student=Student(name=f"Student {user_id}", user_id=user_id, _inner=None),
    status=status)


def _make_text_submission(user_id, status, text):
  return TextSubmission(
    student=Student(name=f"Student {user_id}", user_id=user_id, _inner=None),
    status=status,
    submission_text=text)


def test_programming_assignment_prepare_filters_to_single_student_for_regrade():
  lms_assignment = DummyLmsAssignment([
    _make_programming_submission(101, Submission.Status.GRADED),
    _make_programming_submission(202, Submission.Status.GRADED),
  ])
  assignment = ProgrammingAssignment(lms_assignment=lms_assignment)

  assignment.prepare(do_regrade=True, limit=1, student_id=202)

  assert [submission.student.user_id for submission in assignment.submissions] == [202]
  assert lms_assignment.calls == [{"limit": None}]


def test_text_assignment_prepare_filters_to_single_student_and_submission_data():
  lms_assignment = DummyLmsAssignment([
    _make_text_submission(101, Submission.Status.UNGRADED, "first response"),
    _make_text_submission(202, Submission.Status.UNGRADED, "target response"),
  ])
  assignment = TextAssignment(lms_assignment=lms_assignment)

  assignment.prepare(student_id=202, test=False)

  assert [submission.student.user_id for submission in assignment.submissions] == [202]
  assert assignment.submission_data == [{
    "student_id": 202,
    "student_name": "Student 202",
    "text": "target response",
    "word_count": 2,
    "submission_obj": assignment.submissions[0],
  }]
  assert lms_assignment.calls == [{"limit": None, "test": False}]
