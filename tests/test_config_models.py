from types import SimpleNamespace

import pytest

from Autograder import grade_assignments
from Autograder.config_models import parse_run_config


def test_parse_run_config_requires_assignment_types():
  with pytest.raises(ValueError):
    parse_run_config({"courses": []})


def test_parse_run_config_rejects_course_without_assignment_groups():
  with pytest.raises(ValueError):
    parse_run_config({
      "assignment_types": {
        "programming": {
          "kind": "ProgrammingAssignment",
          "grader": "template-grader"
        }
      },
      "courses": [{"id": 1, "assignments": [{"id": 2}]}]
    })


def test_collect_assignments_to_grade_builds_typed_requests(monkeypatch):
  class DummyCourse:
    name = "CST"

  class DummyCanvasInterface:
    def __init__(self, *args, **kwargs):
      pass

    def get_course(self, _):
      return DummyCourse()

  monkeypatch.setattr(grade_assignments, "CanvasInterface", DummyCanvasInterface)

  run_config = parse_run_config({
    "prod": False,
    "push": True,
    "assignment_types": {
      "programming": {
        "kind": "ProgrammingAssignment",
        "grader": "template-grader",
        "settings": {
          "record_retention": False,
          "base_image_name": "base"
        }
      }
    },
    "courses": [{
      "id": 10,
      "name": "CST334",
      "slack_channel": "C123",
      "record_retention": True,
      "assignment_groups": [{
        "type": "programming",
        "settings": {
          "base_image_name": "group-base"
        },
        "assignments": [{
          "id": 99,
          "repo_path": "PA1",
          "record_retention": False,
          "records_dir": "/tmp/records"
        }]
      }]
    }]
  })

  args = SimpleNamespace(env=None)
  requests = grade_assignments.collect_assignments_to_grade(run_config, args)

  assert len(requests) == 1
  request = requests[0]
  assert request.assignment_id == 99
  assert request.assignment_kind == "ProgrammingAssignment"
  assert request.grader_name == "template-grader"
  assert request.repo_path == "PA1"
  assert request.push_grades is True
  assert request.settings["base_image_name"] == "group-base"
  assert request.settings["record_retention"] is False
  assert request.settings["records_dir"] == "/tmp/records"
