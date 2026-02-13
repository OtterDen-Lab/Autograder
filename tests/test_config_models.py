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
  captured_init = {}

  class DummyCourse:
    name = "CST"

  class DummyCanvasInterface:
    def __init__(self, *args, **kwargs):
      captured_init.update(kwargs)

    def get_course(self, _):
      return DummyCourse()

  monkeypatch.setattr(grade_assignments, "CanvasInterface", DummyCanvasInterface)

  run_config = parse_run_config({
    "prod": False,
    "push": True,
    "privacy_mode": "blind",
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
  assert request.privacy_mode == "blind"
  assert request.reveal_identity is False
  assert request.settings["base_image_name"] == "group-base"
  assert request.settings["record_retention"] is False
  assert request.settings["records_dir"] == "/tmp/records"
  assert captured_init["privacy_mode"] == "blind"
  assert captured_init["reveal_identity"] is False


def test_parse_run_config_accepts_privacy_mode_and_reveal_identity():
  run_config = parse_run_config({
    "privacy_mode": "blind",
    "reveal_identity": True,
    "idempotency_key": "run-42",
    "idempotency_state_dir": "/tmp/autograder-state",
    "assignment_types": {
      "text": {
        "kind": "TextAssignment",
        "grader": "TextSubmissionGrader"
      }
    },
    "courses": [{
      "id": 10,
      "assignment_groups": [{
        "type": "text",
        "assignments": [{
          "id": 99
        }]
      }]
    }]
  })

  assert run_config.privacy_mode == "blind"
  assert run_config.reveal_identity is True
  assert run_config.idempotency_key == "run-42"
  assert run_config.idempotency_state_dir == "/tmp/autograder-state"


def test_parse_run_config_rejects_invalid_privacy_mode():
  with pytest.raises(ValueError):
    parse_run_config({
      "privacy_mode": "full",
      "assignment_types": {
        "programming": {
          "kind": "ProgrammingAssignment",
          "grader": "template-grader"
        }
      },
      "courses": [{
        "id": 1,
        "assignment_groups": [{
          "type": "programming",
          "assignments": [{
            "id": 2
          }]
        }]
      }]
    })


def test_parse_run_config_rejects_unsupported_assignment_kind():
  with pytest.raises(ValueError) as exc:
    parse_run_config({
      "assignment_types": {
        "legacy": {
          "kind": "LegacyAssignment",
          "grader": "LegacyGrader"
        }
      },
      "courses": [{
        "id": 1,
        "assignment_groups": [{
          "type": "legacy",
          "assignments": [{
            "id": 2
          }]
        }]
      }]
    })
  assert "not supported" in str(exc.value)


def test_parse_run_config_rejects_missing_grader():
  with pytest.raises(ValueError) as exc:
    parse_run_config({
      "assignment_types": {
        "programming": {
          "kind": "ProgrammingAssignment"
        }
      },
      "courses": [{
        "id": 1,
        "assignment_groups": [{
          "type": "programming",
          "assignments": [{
            "id": 2
          }]
        }]
      }]
    })
  assert "missing required key 'grader'" in str(exc.value)


def test_parse_run_config_rejects_grader_not_allowed_for_kind():
  with pytest.raises(ValueError) as exc:
    parse_run_config({
      "assignment_types": {
        "programming": {
          "kind": "ProgrammingAssignment",
          "grader": "TextSubmissionGrader"
        }
      },
      "courses": [{
        "id": 1,
        "assignment_groups": [{
          "type": "programming",
          "assignments": [{
            "id": 2
          }]
        }]
      }]
    })
  assert "not supported for kind" in str(exc.value)


def test_collect_assignments_normalizes_template_alias_settings(monkeypatch):
  class DummyCourse:
    name = "CST"

  class DummyCanvasInterface:
    def __init__(self, *args, **kwargs):
      pass

    def get_course(self, _):
      return DummyCourse()

  monkeypatch.setattr(grade_assignments, "CanvasInterface", DummyCanvasInterface)

  run_config = parse_run_config({
    "assignment_types": {
      "programming": {
        "kind": "ProgrammingAssignment",
        "grader": "template-grader",
      }
    },
    "courses": [{
      "id": 10,
      "assignment_groups": [{
        "type": "programming",
        "settings": {
          "source_repo": "https://example.com/repo.git",
          "extra_install_lines": ["RUN apt install clang"],
        },
        "assignments": [{
          "id": 99,
          "repo_path": "PA1",
        }]
      }]
    }]
  })

  args = SimpleNamespace(env=None)
  requests = grade_assignments.collect_assignments_to_grade(run_config, args)
  settings = requests[0].settings
  assert settings["extra_dockerfile_lines"] == ["RUN apt install clang"]
  assert "extra_install_lines" not in settings


def test_collect_assignments_rejects_unknown_template_setting(monkeypatch):
  class DummyCourse:
    name = "CST"

  class DummyCanvasInterface:
    def __init__(self, *args, **kwargs):
      pass

    def get_course(self, _):
      return DummyCourse()

  monkeypatch.setattr(grade_assignments, "CanvasInterface", DummyCanvasInterface)

  run_config = parse_run_config({
    "assignment_types": {
      "programming": {
        "kind": "ProgrammingAssignment",
        "grader": "template-grader",
      }
    },
    "courses": [{
      "id": 10,
      "assignment_groups": [{
        "type": "programming",
        "settings": {
          "unknown_knob": True
        },
        "assignments": [{
          "id": 99
        }]
      }]
    }]
  })

  with pytest.raises(ValueError) as exc:
    grade_assignments.collect_assignments_to_grade(run_config,
                                                   SimpleNamespace(env=None))
  assert "unsupported template-grader setting" in str(exc.value)


def test_collect_assignments_rejects_invalid_text_tier(monkeypatch):
  class DummyCourse:
    name = "CST"

  class DummyCanvasInterface:
    def __init__(self, *args, **kwargs):
      pass

    def get_course(self, _):
      return DummyCourse()

  monkeypatch.setattr(grade_assignments, "CanvasInterface", DummyCanvasInterface)

  run_config = parse_run_config({
    "assignment_types": {
      "text": {
        "kind": "TextAssignment",
        "grader": "TextSubmissionGrader",
      }
    },
    "courses": [{
      "id": 10,
      "assignment_groups": [{
        "type": "text",
        "settings": {
          "phase2_tier": "xl"
        },
        "assignments": [{
          "id": 99
        }]
      }]
    }]
  })

  with pytest.raises(ValueError) as exc:
    grade_assignments.collect_assignments_to_grade(run_config,
                                                   SimpleNamespace(env=None))
  assert "phase2_tier" in str(exc.value)
