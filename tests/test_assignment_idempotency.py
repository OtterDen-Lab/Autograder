from types import SimpleNamespace

from Autograder.assignment import Assignment
from lms_interface.classes import Feedback, Submission, Student


class DummyAssignment(Assignment):

  def prepare(self, *args, **kwargs):
    return None


class DummyLmsAssignment:
  name = "PA1"
  id = 111

  def __init__(self, fail_user_ids=None, exception_user_ids=None):
    self.canvas_course = SimpleNamespace(id=222)
    self.fail_user_ids = set(fail_user_ids or [])
    self.exception_user_ids = set(exception_user_ids or [])
    self.push_calls = []

  def push_feedback(self, *, user_id, **kwargs):
    self.push_calls.append(user_id)
    if user_id in self.exception_user_ids:
      raise RuntimeError(f"push exploded for {user_id}")
    return user_id not in self.fail_user_ids


def _make_submission(user_id: int) -> Submission:
  submission = Submission(student=Student(name=f"Student {user_id}",
                                          user_id=user_id,
                                          _inner=None),
                          status=Submission.Status.UNGRADED)
  submission.feedback = Feedback(percentage_score=95.0, comments="ok")
  return submission


def test_finalize_idempotency_skips_previously_pushed_users(tmp_path):
  lms_assignment = DummyLmsAssignment()
  assignment = DummyAssignment(lms_assignment=lms_assignment)
  assignment.submissions = [_make_submission(1), _make_submission(2)]

  assignment.finalize(push=True,
                      idempotency_key="run-A",
                      idempotency_state_dir=str(tmp_path))
  assignment.finalize(push=True,
                      idempotency_key="run-A",
                      idempotency_state_dir=str(tmp_path))

  assert lms_assignment.push_calls == [1, 2]


def test_finalize_idempotency_different_keys_push_again(tmp_path):
  lms_assignment = DummyLmsAssignment()
  assignment = DummyAssignment(lms_assignment=lms_assignment)
  assignment.submissions = [_make_submission(1)]

  assignment.finalize(push=True,
                      idempotency_key="run-A",
                      idempotency_state_dir=str(tmp_path))
  assignment.finalize(push=True,
                      idempotency_key="run-B",
                      idempotency_state_dir=str(tmp_path))

  assert lms_assignment.push_calls == [1, 1]


def test_finalize_idempotency_only_marks_successful_pushes(tmp_path):
  lms_assignment = DummyLmsAssignment(fail_user_ids={2})
  assignment = DummyAssignment(lms_assignment=lms_assignment)
  assignment.submissions = [_make_submission(1), _make_submission(2)]

  assignment.finalize(push=True,
                      idempotency_key="run-A",
                      idempotency_state_dir=str(tmp_path))

  # Retry with failures fixed; user 1 should be skipped, user 2 retried.
  lms_assignment.fail_user_ids.clear()
  assignment.finalize(push=True,
                      idempotency_key="run-A",
                      idempotency_state_dir=str(tmp_path))

  assert lms_assignment.push_calls == [1, 2, 2]


def test_finalize_push_exception_isolated_per_student(tmp_path):
  lms_assignment = DummyLmsAssignment(exception_user_ids={2})
  assignment = DummyAssignment(lms_assignment=lms_assignment)
  assignment.submissions = [_make_submission(1), _make_submission(2),
                            _make_submission(3)]

  assignment.finalize(push=True,
                      idempotency_key="run-A",
                      idempotency_state_dir=str(tmp_path))

  # Student 2 fails with an exception, but student 3 still gets pushed.
  assert lms_assignment.push_calls == [1, 2, 3]

  # Re-running the same key should skip successful users and retry only failed ones.
  assignment.finalize(push=True,
                      idempotency_key="run-A",
                      idempotency_state_dir=str(tmp_path))
  assert lms_assignment.push_calls == [1, 2, 3, 2]
