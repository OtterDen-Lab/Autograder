from types import SimpleNamespace

import pytest

from Autograder import grade_assignments
from Autograder import config_models
from Autograder.config_models import parse_run_config
from Autograder.grader_settings import TemplateGraderSettings


def test_parse_run_config_requires_assignment_types():
  with pytest.raises(ValueError) as exc:
    parse_run_config({"courses": []})
  assert str(exc.value).startswith("Config error:")


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


def test_get_active_grader_compatibility_discovers_registry_metadata(monkeypatch):
  class DummyGrader:
    COMPATIBLE_KINDS = {"CustomAssignmentKind"}
    _registry_name = "custom-grader"

  monkeypatch.setattr(config_models, "_FALLBACK_ACTIVE_ASSIGNMENT_KINDS",
                      {"FallbackAssignment"})
  monkeypatch.setattr(config_models, "_FALLBACK_ACTIVE_GRADERS_BY_KIND",
                      {"FallbackAssignment": {"fallback-grader"}})

  from Autograder.registry import GraderRegistry
  monkeypatch.setattr(GraderRegistry, "_scanned", True)
  monkeypatch.setattr(GraderRegistry, "_registry",
                      {"custom-grader": DummyGrader})

  kinds, graders_by_kind = config_models.get_active_grader_compatibility()

  assert kinds == {"CustomAssignmentKind"}
  assert graders_by_kind == {
    "CustomAssignmentKind": {"custom-grader"}
  }


def test_get_active_grader_compatibility_uses_fallback_when_metadata_missing(
    monkeypatch):
  class UnknownGrader:
    _registry_name = "unknown-grader"

  fallback_kinds = {"ProgrammingAssignment", "TextAssignment"}
  fallback_graders = {
    "ProgrammingAssignment": {"template-grader"},
    "TextAssignment": {"TextSubmissionGrader"},
  }

  monkeypatch.setattr(config_models, "_FALLBACK_ACTIVE_ASSIGNMENT_KINDS",
                      fallback_kinds)
  monkeypatch.setattr(config_models, "_FALLBACK_ACTIVE_GRADERS_BY_KIND",
                      fallback_graders)

  from Autograder.registry import GraderRegistry
  monkeypatch.setattr(GraderRegistry, "_scanned", True)
  monkeypatch.setattr(GraderRegistry, "_registry",
                      {"unknown-grader": UnknownGrader})

  kinds, graders_by_kind = config_models.get_active_grader_compatibility()

  assert kinds == fallback_kinds
  assert graders_by_kind == fallback_graders


def test_parse_run_config_uses_discovered_grader_compatibility(monkeypatch):
  monkeypatch.setattr(config_models, "get_active_grader_compatibility",
                      lambda: ({
                        "CustomAssignmentKind"
                      }, {
                        "CustomAssignmentKind": {"custom-grader"}
                      }))

  run_config = parse_run_config({
    "assignment_types": {
      "custom": {
        "kind": "CustomAssignmentKind",
        "grader": "custom-grader"
      }
    },
    "courses": [{
      "id": 1,
      "assignment_groups": [{
        "type": "custom",
        "assignments": [{
          "id": 2
        }]
      }]
    }]
  })

  assert run_config.assignment_types["custom"].kind == "CustomAssignmentKind"
  assert run_config.assignment_types["custom"].grader == "custom-grader"


def test_normalize_grader_settings_falls_back_for_known_grader_when_registry_errors(
    monkeypatch):
  from Autograder.registry import GraderRegistry

  def raise_import_error(_):
    raise ModuleNotFoundError("simulated optional dependency missing")

  monkeypatch.setattr(GraderRegistry, "get_class", raise_import_error)

  normalized = config_models.normalize_grader_settings(
    "TextSubmissionGrader",
    {"phase2_tier": "medium"},
    "assignment_types.text",
  )

  assert normalized["phase2_tier"] == "medium"
  assert normalized["phase1_tier"] == "small"


def test_normalize_grader_settings_reports_registry_error_for_unknown_grader(
    monkeypatch):
  from Autograder.registry import GraderRegistry

  def raise_import_error(_):
    raise ModuleNotFoundError("simulated optional dependency missing")

  monkeypatch.setattr(GraderRegistry, "get_class", raise_import_error)

  with pytest.raises(ValueError) as exc:
    config_models.normalize_grader_settings(
      "UnknownGrader",
      {},
      "assignment_types.unknown",
    )

  message = str(exc.value)
  assert "Failed to load grader registry while validating 'UnknownGrader'" in message
  assert "ModuleNotFoundError" in message


def test_normalize_text_submission_settings_supports_prompt_and_rubric_blocks():
  normalized = config_models.normalize_grader_settings(
    "TextSubmissionGrader",
    {
      "phase1_tier": "medium",
      "rate_limit_retries": 2,
      "prompts": {
        "aggregate_analysis": "Analyze {num_submissions} submissions."
      },
      "rubric": {
        "engagement": {
          "points": 5,
          "description": "Engagement depth"
        },
        "word_threshold": 300
      }
    },
    "assignment_types.text",
  )

  assert normalized["phase1_tier"] == "medium"
  assert normalized["rate_limit_retries"] == 2
  assert normalized["prompt_templates"]["aggregate_analysis"].startswith(
    "Analyze")
  assert normalized["rubric"]["engagement"]["points"] == 5
  assert normalized["rubric"]["word_threshold"] == 300


def test_normalize_text_submission_settings_omits_rubric_when_unspecified():
  normalized = config_models.normalize_grader_settings(
    "TextSubmissionGrader",
    {
      "phase1_tier": "medium",
      "rate_limit_retries": 2,
    },
    "assignment_types.text",
  )

  assert normalized["phase1_tier"] == "medium"
  assert normalized["rate_limit_retries"] == 2
  assert "rubric" not in normalized


def test_template_grader_settings_preserve_rubric_for_lms_push():
  settings = TemplateGraderSettings.from_raw(
    {
      "rubric": {
        "criterion 1": 2,
        "crit2": {
          "points": 3,
          "comments": "Good work.",
        },
      }
    },
    "assignment_types.programming.settings",
  )

  assert settings.rubric == {
    "criterion 1": 2,
    "crit2": {
      "points": 3,
      "comments": "Good work.",
    },
  }

  normalized = settings.to_kwargs()
  assert normalized["rubric"] == {
    "criterion 1": 2,
    "crit2": {
      "points": 3,
      "comments": "Good work.",
    },
  }


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
          "base_image_name": "base",
          "additional_repos": [{
            "source_repo": "https://example.com/tests.git",
            "container_path": "/repo/tests",
          }],
          "container_repo_path": "/repo/programming-assignments"
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
  assert request.settings["additional_repos"] == [{
    "source_repo": "https://example.com/tests.git",
    "container_path": "/repo/tests",
    "depth": 1,
  }]
  assert request.settings["container_repo_path"] == "/repo/programming-assignments"
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


def test_parse_run_config_accepts_weekly_study_notes_grader():
  run_config = parse_run_config({
    "assignment_types": {
      "weekly_notes": {
        "kind": "TextAssignment",
        "grader": "WeeklyStudyNotesGrader"
      }
    },
    "courses": [{
      "id": 10,
      "assignment_groups": [{
        "type": "weekly_notes",
        "assignments": [{
          "id": 99
        }]
      }]
    }]
  })

  assert run_config.assignment_types["weekly_notes"].kind == "TextAssignment"
  assert run_config.assignment_types["weekly_notes"].grader == "WeeklyStudyNotesGrader"


def test_parse_run_config_accepts_external_tool_assignment_type():
  run_config = parse_run_config({
    "assignment_types": {
      "panopto_watch": {
        "kind": "ExternalToolAssignment",
        "grader": "panopto-watch-grader",
        "settings": {
          "panopto_base": "https://videos.example.edu/Panopto/",
          "panopto_client_id_env": "PANOPTO_CLIENT_ID",
          "panopto_client_secret_env": "PANOPTO_CLIENT_SECRET",
          "skip_non_improvable": True,
          "skip_stale_watch_buffer_multiplier": 0,
          "regrade": True,
        }
      }
    },
    "courses": [{
      "id": 10,
      "assignment_groups": [{
        "type": "panopto_watch",
        "assignments": [{
          "id": 99,
          "panopto_id": "session-123"
        }]
      }]
    }]
  })

  assignment_type = run_config.assignment_types["panopto_watch"]
  assert assignment_type.kind == "ExternalToolAssignment"
  assert assignment_type.grader == "panopto-watch-grader"
  assert assignment_type.settings["panopto_client_id_env"] == "PANOPTO_CLIENT_ID"
  assert assignment_type.settings[
    "panopto_client_secret_env"] == "PANOPTO_CLIENT_SECRET"
  assert assignment_type.settings["skip_non_improvable"] is True
  assert assignment_type.settings["skip_stale_watch_buffer_multiplier"] == 0
  assert assignment_type.settings["regrade"] is True
  assert run_config.courses[0].assignment_groups[0].assignments[0].settings[
    "panopto_id"] == "session-123"


def test_external_tool_settings_default_panopto_refresh_token_path():
  from Autograder.grader_settings import ExternalToolGraderSettings

  settings = ExternalToolGraderSettings.from_raw({
    "panopto_base": "https://videos.example.edu/Panopto/",
  }, "assignment_types.panopto_watch.settings")

  assert settings.panopto_refresh_token_path == "~/.tokens/Autograder.panopto.json"


def test_parse_run_config_accepts_assignment_type_schedule():
  run_config = parse_run_config({
    "assignment_types": {
      "programming": {
        "kind": "ProgrammingAssignment",
        "grader": "template-grader",
        "schedule": {
          "timezone": "UTC",
          "rrule": "FREQ=DAILY;BYHOUR=0,12;BYMINUTE=0;BYSECOND=0",
        }
      }
    },
    "courses": [{
      "id": 10,
      "assignment_groups": [{
        "type": "programming",
        "assignments": [{
          "id": 99
        }]
      }]
    }]
  })

  schedule = run_config.assignment_types["programming"].schedule
  assert schedule is not None
  assert schedule.timezone == "UTC"
  assert schedule.rrule == "FREQ=DAILY;BYHOUR=0,12;BYMINUTE=0;BYSECOND=0"


def test_parse_run_config_defaults_schedule_timezone_to_los_angeles():
  run_config = parse_run_config({
    "assignment_types": {
      "programming": {
        "kind": "ProgrammingAssignment",
        "grader": "template-grader",
        "schedule": {
          "rrule": "FREQ=DAILY;BYHOUR=0;BYMINUTE=0;BYSECOND=0",
        }
      }
    },
    "courses": [{
      "id": 10,
      "assignment_groups": [{
        "type": "programming",
        "assignments": [{
          "id": 99
        }]
      }]
    }]
  })

  schedule = run_config.assignment_types["programming"].schedule
  assert schedule is not None
  assert schedule.timezone == "America/Los_Angeles"


def test_normalize_external_tool_settings_validates_required_panopto_base():
  with pytest.raises(ValueError) as exc:
    config_models.normalize_grader_settings(
      "panopto-watch-grader",
      {},
      "assignment_types.panopto_watch",
    )

  assert "panopto_base is required" in str(exc.value)


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


def test_parse_run_config_rejects_duplicate_assignment_ids_in_group():
  with pytest.raises(ValueError) as exc:
    parse_run_config({
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
          }, {
            "id": 2
          }]
        }]
      }]
    })
  assert "duplicate assignment id(s)" in str(exc.value)


def test_parse_run_config_allows_duplicate_id_if_one_assignment_disabled():
  run_config = parse_run_config({
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
        }, {
          "id": 2,
          "disabled": True
        }]
      }]
    }]
  })

  assert run_config.courses[0].assignment_groups[0].assignments[1].disabled is True


def test_collect_assignments_rejects_template_alias_settings(monkeypatch):
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
  with pytest.raises(ValueError) as exc:
    grade_assignments.collect_assignments_to_grade(run_config, args)
  assert "extra_install_lines" in str(exc.value)
  assert "extra_dockerfile_lines" in str(exc.value)


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


def test_collect_assignments_rejects_invalid_container_repo_path(monkeypatch):
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
          "container_repo_path": "programming-assignments"
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
  assert "container_repo_path" in str(exc.value)


def test_collect_assignments_rejects_invalid_additional_repo_path(monkeypatch):
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
          "additional_repos": [{
            "source_repo": "https://example.com/tools.git",
            "container_path": "repo/tools"
          }]
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
  assert "additional_repos[0].container_path" in str(exc.value)


def test_collect_assignments_rejects_overlapping_additional_repos(monkeypatch):
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
          "additional_repos": [{
            "source_repo": "https://example.com/a.git",
            "container_path": "/repo/tools"
          }, {
            "source_repo": "https://example.com/b.git",
            "container_path": "/repo/tools/sub"
          }]
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
  assert "overlapping container_path" in str(exc.value)


def test_collect_assignments_accepts_template_entrypoint_and_error_artifact_settings(
    monkeypatch):
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
          "grading_script": "/repo/scripts/custom_grader.py --PA PA1",
          "grading_args": ["--files", "main.c", "util.h"],
          "grading_workdir": "/repo/custom",
          "upload_error_artifacts": True,
          "extra_installs": ["apt-get update && apt-get install -y jq"],
        },
        "assignments": [{
          "id": 99,
          "assignment_name": "PA1"
        }]
      }]
    }]
  })

  requests = grade_assignments.collect_assignments_to_grade(
    run_config, SimpleNamespace(env=None))
  assert len(requests) == 1
  settings = requests[0].settings
  assert settings["grading_script"] == "/repo/scripts/custom_grader.py --PA PA1"
  assert settings["grading_args"] == ["--files", "main.c", "util.h"]
  assert settings["grading_workdir"] == "/repo/custom"
  assert settings["upload_error_artifacts"] is True
  assert settings["extra_installs"] == ["apt-get update && apt-get install -y jq"]


def test_collect_assignments_rejects_invalid_grading_args(monkeypatch):
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
          "grading_args": ["--files", 1]
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
  assert "grading_args[1] must be a string" in str(exc.value)


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
