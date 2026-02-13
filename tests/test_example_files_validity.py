from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from Autograder import grade_assignments


EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "example_files"
EXAMPLE_CONFIGS = sorted(EXAMPLES_DIR.glob("*.yaml"))


def test_example_configs_exist():
  assert EXAMPLE_CONFIGS, "No example YAML files found in example_files/"


def test_example_configs_validate_and_collect(monkeypatch):
  class DummyCourse:
    def __init__(self, course_id):
      self.id = course_id
      self.name = f"Course {course_id}"

  class DummyCanvasInterface:
    def __init__(self, *args, **kwargs):
      pass

    def get_course(self, course_id):
      return DummyCourse(course_id)

  monkeypatch.setattr(grade_assignments, "CanvasInterface", DummyCanvasInterface)

  args = SimpleNamespace(env=None,
                         reveal_identity=False,
                         idempotency_key=None,
                         idempotency_state_dir=None)

  for yaml_path in EXAMPLE_CONFIGS:
    with open(yaml_path) as fid:
      raw_config = yaml.safe_load(fid)

    run_config = grade_assignments.parse_run_config(raw_config)
    requests = grade_assignments.collect_assignments_to_grade(run_config, args)

    expected_assignments = 0
    for course in run_config.courses:
      for group in course.assignment_groups:
        seen_ids = set()
        duplicate_ids = set()
        for assignment in group.assignments:
          if assignment.disabled:
            continue
          if assignment.id in seen_ids:
            duplicate_ids.add(assignment.id)
          seen_ids.add(assignment.id)

        if duplicate_ids:
          duplicates = ", ".join(str(i) for i in sorted(duplicate_ids))
          pytest.fail(
            f"{yaml_path.name} has duplicate assignment id(s) in course {course.id}, "
            f"group {group.type_name}: {duplicates}")

        expected_assignments += len(
          [a for a in group.assignments if not a.disabled])

    assert len(requests) == expected_assignments, (
      f"{yaml_path.name} produced unexpected assignment count")
