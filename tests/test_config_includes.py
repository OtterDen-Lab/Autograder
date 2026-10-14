import yaml
import pytest

from Autograder.grade_assignments import load_and_validate_config


def _write_yaml(path, content):
  path.write_text(yaml.safe_dump(content), encoding="utf-8")


def test_load_config_combines_course_fragments_and_overrides_mappings(tmp_path):
  _write_yaml(
    tmp_path / "shared.yaml", {
      "prod": False,
      "assignment_types": {
        "programming": {
          "kind": "ProgrammingAssignment",
          "grader": "template-grader",
          "settings": {"base_image_name": "python:3.12"},
        },
      },
      "courses": [{"id": 1, "name": "CST 334", "assignment_groups": []}],
    })
  _write_yaml(
    tmp_path / "cst334-online.yaml", {
      "courses": [{"id": 2, "name": "CST 334 Online", "assignment_groups": []}],
    })
  _write_yaml(
    tmp_path / "workhorse.yaml", {
      "includes": ["shared.yaml", "cst334-online.yaml"],
      "prod": True,
      "assignment_types": {"programming": {"settings": {"record_retention": True}}},
    })

  config = load_and_validate_config(str(tmp_path / "workhorse.yaml"))

  assert config.prod is True
  assert [course.id for course in config.courses] == [1, 2]
  assert config.assignment_types["programming"].settings == {
    "base_image_name": "python:3.12",
    "record_retention": True,
  }


def test_load_config_resolves_nested_includes_relative_to_declaring_file(tmp_path):
  fragments = tmp_path / "fragments"
  fragments.mkdir()
  _write_yaml(
    fragments / "types.yaml", {
      "assignment_types": {
        "text": {"kind": "TextAssignment", "grader": "TextSubmissionGrader"},
      },
    })
  _write_yaml(
    fragments / "course.yaml", {
      "includes": ["types.yaml"],
      "courses": [{"id": 1, "assignment_groups": []}],
    })
  _write_yaml(tmp_path / "workhorse.yaml", {"includes": ["fragments/course.yaml"]})

  config = load_and_validate_config(str(tmp_path / "workhorse.yaml"))

  assert list(config.assignment_types) == ["text"]
  assert [course.id for course in config.courses] == [1]


def test_load_config_rejects_cyclic_includes(tmp_path):
  _write_yaml(tmp_path / "one.yaml", {"includes": ["two.yaml"]})
  _write_yaml(tmp_path / "two.yaml", {"includes": ["one.yaml"]})

  with pytest.raises(SystemExit, match="cyclic config include"):
    load_and_validate_config(str(tmp_path / "one.yaml"))
