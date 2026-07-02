from types import SimpleNamespace

from Autograder.assignment import (ExternalToolAssignment,
                                   ProgrammingAssignment, TextAssignment)
from lms_interface.classes import Student, Submission, TextSubmission


class DummyLmsAssignment:
  def __init__(self, submissions):
    self.name = "Test Assignment"
    self._submissions = list(submissions)
    self.calls = []

  def get_submissions(self, **kwargs):
    self.calls.append(kwargs)
    return list(self._submissions)


class DummyExternalLmsAssignment(DummyLmsAssignment):
  def __init__(self, submissions, students, points_possible=None):
    super().__init__(submissions)
    self._students = list(students)
    self.name = "Video Watch Assignment"
    self.points_possible = points_possible

  def get_students(self, include_names=True):
    return list(self._students)


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


def test_external_tool_assignment_prepare_builds_submissions_from_watch_data(
    monkeypatch):
  students = [
    Student(name="Student 101",
            user_id=101,
            _inner=SimpleNamespace(email="student101@example.edu")),
    Student(name="Student 202",
            user_id=202,
            _inner=SimpleNamespace(email="student202@example.edu")),
  ]
  existing = [
    Submission(student=students[0], status=Submission.Status.GRADED),
    Submission(student=students[1], status=Submission.Status.UNGRADED),
  ]
  lms_assignment = DummyExternalLmsAssignment(existing, students)

  class FakeWatchRecord:
    def __init__(self, user_key, percent_watched, viewed_seconds, duration_seconds):
      self.user_key = user_key
      self.percent_watched = percent_watched
      self.viewed_seconds = viewed_seconds
      self.duration_seconds = duration_seconds
      self.raw = {"User": {"Username": user_key}}

  class FakePanoptoClient:
    def __init__(self, **kwargs):
      pass

    def fetch_watch_records(self, **kwargs):
      return [
        FakeWatchRecord("unified\\student202@example.edu", 62.5, 750.0,
                        1200.0),
      ]

  monkeypatch.setattr("Autograder.assignment.PanoptoWatchClient",
                      FakePanoptoClient)

  assignment = ExternalToolAssignment(lms_assignment=lms_assignment)
  assignment.prepare(
    panopto_url="https://videos.example.edu/Panopto/Pages/Viewer.aspx?id=session-123",
    panopto_access_token="secret-token",
    record_identifier_paths=["User.Username"],
  )

  assert [submission.student.user_id for submission in assignment.submissions] == [202]
  assert assignment.submissions[0].extra_info["percent_watched"] == 62.5
  assert assignment.submissions[0].extra_info["watch_record_found"] is True


def test_external_tool_assignment_prepare_carries_current_canvas_score(
    monkeypatch):
  students = [
    Student(name="Student 101",
            user_id=101,
            _inner=SimpleNamespace(email="student101@example.edu")),
  ]
  existing = [
    SimpleNamespace(student=students[0],
                    status=Submission.Status.UNGRADED,
                    score=10.0),
  ]
  lms_assignment = DummyExternalLmsAssignment(existing, students)

  class FakeWatchRecord:
    def __init__(self, user_key, percent_watched, viewed_seconds, duration_seconds):
      self.user_key = user_key
      self.percent_watched = percent_watched
      self.viewed_seconds = viewed_seconds
      self.duration_seconds = duration_seconds
      self.raw = {"User": {"Username": user_key}}

  class FakePanoptoClient:
    def __init__(self, **kwargs):
      pass

    def fetch_watch_records(self, **kwargs):
      return [
        FakeWatchRecord("unified\\student101@example.edu", 100.0, 1200.0,
                        1200.0),
      ]

  monkeypatch.setattr("Autograder.assignment.PanoptoWatchClient",
                      FakePanoptoClient)

  assignment = ExternalToolAssignment(lms_assignment=lms_assignment)
  assignment.prepare(
    panopto_url="https://videos.example.edu/Panopto/Pages/Viewer.aspx?id=session-123",
    panopto_access_token="secret-token",
    record_identifier_paths=["User.Username"],
  )

  assert [submission.student.user_id for submission in assignment.submissions] == [101]
  assert assignment.submissions[0].extra_info["current_canvas_score"] == 10.0


def test_external_tool_assignment_prepare_skips_non_improvable_scores(
    monkeypatch):
  students = [
    Student(name="Student 101",
            user_id=101,
            _inner=SimpleNamespace(email="student101@example.edu")),
    Student(name="Student 202",
            user_id=202,
            _inner=SimpleNamespace(email="student202@example.edu")),
  ]
  existing = [
    SimpleNamespace(student=students[0],
                    status=Submission.Status.UNGRADED,
                    score=10.0),
    SimpleNamespace(student=students[1],
                    status=Submission.Status.UNGRADED,
                    score=8.0),
  ]
  lms_assignment = DummyExternalLmsAssignment(existing,
                                               students,
                                               points_possible=10.0)

  class FakeWatchRecord:
    def __init__(self, user_key, percent_watched, viewed_seconds, duration_seconds):
      self.user_key = user_key
      self.percent_watched = percent_watched
      self.viewed_seconds = viewed_seconds
      self.duration_seconds = duration_seconds
      self.raw = {"User": {"Username": user_key}}

  class FakePanoptoClient:
    def __init__(self, **kwargs):
      pass

    def fetch_watch_records(self, **kwargs):
      return [
        FakeWatchRecord("unified\\student101@example.edu", 100.0, 1200.0,
                        1200.0),
        FakeWatchRecord("unified\\student202@example.edu", 80.0, 960.0,
                        1200.0),
      ]

  monkeypatch.setattr("Autograder.assignment.PanoptoWatchClient",
                      FakePanoptoClient)

  assignment = ExternalToolAssignment(lms_assignment=lms_assignment)
  assignment.prepare(
    panopto_url="https://videos.example.edu/Panopto/Pages/Viewer.aspx?id=session-123",
    panopto_access_token="secret-token",
    record_identifier_paths=["User.Username"],
    skip_non_improvable=True,
  )

  assert [submission.student.user_id for submission in assignment.submissions] == [202]
  assert assignment.submissions[0].extra_info["percent_watched"] == 80.0


def test_external_tool_assignment_prepare_uses_raw_submission_history_scores(
    monkeypatch):
  students = [
    Student(name="Student 101",
            user_id=101,
            _inner=SimpleNamespace(email="student101@example.edu")),
    Student(name="Student 202",
            user_id=202,
            _inner=SimpleNamespace(email="student202@example.edu")),
  ]

  class RawSubmission:
    def __init__(self, user_id, score):
      self.user_id = user_id
      self.submission_history = [{
        "workflow_state": "submitted",
        "score": score,
        "body": None,
        "attachments": None,
      }]

  class RawAssignment:
    def get_submissions(self, **kwargs):
      return [
        RawSubmission(101, 10.0),
        RawSubmission(202, 8.0),
      ]

  lms_assignment = DummyExternalLmsAssignment([], students, points_possible=10.0)
  lms_assignment.assignment = RawAssignment()

  class FakeWatchRecord:
    def __init__(self, user_key, percent_watched, viewed_seconds, duration_seconds):
      self.user_key = user_key
      self.percent_watched = percent_watched
      self.viewed_seconds = viewed_seconds
      self.duration_seconds = duration_seconds
      self.raw = {"User": {"Username": user_key}}

  class FakePanoptoClient:
    def __init__(self, **kwargs):
      pass

    def fetch_watch_records(self, **kwargs):
      return [
        FakeWatchRecord("unified\\student101@example.edu", 100.0, 1200.0,
                        1200.0),
        FakeWatchRecord("unified\\student202@example.edu", 80.0, 960.0,
                        1200.0),
      ]

  monkeypatch.setattr("Autograder.assignment.PanoptoWatchClient",
                      FakePanoptoClient)

  assignment = ExternalToolAssignment(lms_assignment=lms_assignment)
  assignment.prepare(
    panopto_url="https://videos.example.edu/Panopto/Pages/Viewer.aspx?id=session-123",
    panopto_access_token="secret-token",
    record_identifier_paths=["User.Username"],
    skip_non_improvable=True,
  )

  assert [submission.student.user_id for submission in assignment.submissions] == [202]
  assert assignment.submissions[0].extra_info["current_canvas_score"] == 8.0


def test_external_tool_assignment_prepare_skips_students_without_watch_records(
    monkeypatch):
  student = Student(name="Student 101",
                    user_id=101,
                    _inner=SimpleNamespace(email="student101@example.edu"))
  lms_assignment = DummyExternalLmsAssignment([], [student])

  class FakePanoptoClient:
    def __init__(self, **kwargs):
      pass

    def fetch_watch_records(self, **kwargs):
      return []

  monkeypatch.setattr("Autograder.assignment.PanoptoWatchClient",
                      FakePanoptoClient)

  assignment = ExternalToolAssignment(lms_assignment=lms_assignment)
  assignment.prepare(
    panopto_url="https://videos.example.edu/Panopto/Pages/Viewer.aspx?id=session-123",
    panopto_access_token="secret-token",
    record_identifier_paths=["User.Username"],
    missing_user_score=15.0,
  )

  assert assignment.submissions == []
