from types import SimpleNamespace

from Autograder.orchestration.stages import run_grade_stage, run_prepare_stage


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
