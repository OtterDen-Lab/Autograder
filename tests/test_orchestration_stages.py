from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Autograder.orchestration.stages import (
  run_grade_stage,
  run_prepare_stage,
  run_publish_stage,
)


class DummyPrepareAssignment:
  def __init__(self):
    self.calls = []
    self.submissions = [object()]

  def assignment_needs_preparation(self):
    return True

  def prepare(self, **kwargs):
    self.calls.append(kwargs)


class DummyGradeAssignment:
  def __init__(self):
    self.calls = []
    self.submissions = [SimpleNamespace(feedback=object())]

  def grade_assignment(self, *args, **kwargs):
    self.calls.append(kwargs)


def test_run_prepare_stage_forces_regrade_from_settings():
  assignment = DummyPrepareAssignment()
  args = SimpleNamespace(limit=None, student_id=None, test=False)
  settings = {"regrade": True}

  run_prepare_stage(SimpleNamespace(assignment_needs_preparation=lambda: True),
                    assignment, args, settings, do_regrade=False)

  assert assignment.calls[0]["do_regrade"] is True


@pytest.mark.parametrize('skip_cooldown', [False, True])
def test_prepare_cooldown_override_preserves_tracking(skip_cooldown):
  assignment = DummyPrepareAssignment()
  assignment.coordination_lease = Mock()
  assignment.grading_cooldown = 10800
  args = SimpleNamespace(limit=1, student_id=None, test=False,
                         skip_cooldown=skip_cooldown)
  lease = assignment.coordination_lease
  def filter_submissions(current, cooldown):
    current.submissions = []
  lease.filter_submissions.side_effect = filter_submissions

  result = run_prepare_stage(
    SimpleNamespace(assignment_needs_preparation=lambda: True),
    assignment, args, {}, do_regrade=True)

  assert assignment.calls[0]['do_regrade'] is True
  assert result.submission_count == (1 if skip_cooldown else 0)
  if skip_cooldown:
    lease.filter_submissions.assert_not_called()
  else:
    lease.filter_submissions.assert_called_once_with(assignment, 10800)
  assert assignment.coordination_lease is lease
  assert assignment.grading_cooldown == 10800


def test_run_grade_stage_forces_regrade_from_settings():
  assignment = DummyGradeAssignment()
  args = SimpleNamespace()
  settings = {"regrade": True}
  assignment_data = SimpleNamespace(reveal_identity=False,
                                    privacy_mode="id_only")
  grader = SimpleNamespace(grade_assignment=assignment.grade_assignment)

  run_grade_stage(grader, assignment, settings, assignment_data, args,
                  do_regrade=False)

  assert assignment.calls[0]["do_regrade"] is True


def test_run_publish_stage_forwards_clobber_feedback_setting():
  finalize_calls = []
  assignment = SimpleNamespace(finalize=lambda **kwargs: finalize_calls.append(kwargs))
  grader = SimpleNamespace(ready_to_finalize=True)
  assignment_data = SimpleNamespace(
    idempotency_key=None,
    idempotency_state_dir="~/.autograder/idempotency",
  )

  run_publish_stage(
    grader,
    assignment,
    SimpleNamespace(),
    push_grades=True,
    assignment_data=assignment_data,
    record_retention=False,
    settings={"clobber_feedback": True},
  )

  assert finalize_calls[0]["clobber_feedback"] is True
