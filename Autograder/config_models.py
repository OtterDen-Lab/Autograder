from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AssignmentTypeConfig:
  name: str
  kind: str
  grader: str
  settings: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AssignmentConfig:
  id: int
  repo_path: Optional[str] = None
  assignment_name: Optional[str] = None
  settings: Dict[str, Any] = field(default_factory=dict)
  disabled: bool = False


@dataclass
class AssignmentGroupConfig:
  type_name: str
  name: Optional[str] = None
  settings: Dict[str, Any] = field(default_factory=dict)
  assignments: List[AssignmentConfig] = field(default_factory=list)


@dataclass
class CourseConfig:
  id: int
  name: Optional[str] = None
  slack_channel: Optional[str] = None
  settings: Dict[str, Any] = field(default_factory=dict)
  assignment_groups: List[AssignmentGroupConfig] = field(default_factory=list)


@dataclass
class RunConfig:
  prod: bool = False
  push: bool = False
  reporting: Dict[str, Any] = field(default_factory=dict)
  error_slack_channel: Optional[str] = None
  assignment_types: Dict[str, AssignmentTypeConfig] = field(default_factory=dict)
  courses: List[CourseConfig] = field(default_factory=list)


@dataclass
class AssignmentRunRequest:
  course: Any
  course_name: Optional[str]
  assignment_id: int
  assignment_type: str
  assignment_kind: str
  grader_name: str
  settings: Dict[str, Any]
  repo_path: Optional[str]
  assignment_name: Optional[str]
  args: Any
  push_grades: bool
  slack_channel: Optional[str]


def _require_dict(value: Any, label: str) -> Dict[str, Any]:
  if not isinstance(value, dict):
    raise ValueError(f"{label} must be a mapping")
  return value


def _extract_settings(source: Dict[str, Any], reserved_keys: set[str]) -> Dict[str, Any]:
  return {k: v for k, v in source.items() if k not in reserved_keys}


def _parse_assignment(raw_assignment: Any) -> AssignmentConfig:
  if isinstance(raw_assignment, (int, str)):
    return AssignmentConfig(id=int(raw_assignment))

  assignment = _require_dict(raw_assignment, "assignment")
  if 'id' not in assignment:
    raise ValueError(f"assignment is missing required key 'id': {assignment}")

  assignment_settings = _extract_settings(
    assignment,
    {'id', 'repo_path', 'assignment_name', 'settings', 'disabled'})
  assignment_settings.update(assignment.get('settings', {}))

  return AssignmentConfig(
    id=int(assignment['id']),
    repo_path=assignment.get('repo_path'),
    assignment_name=assignment.get('assignment_name'),
    settings=assignment_settings,
    disabled=bool(assignment.get('disabled', False)),
  )


def _parse_assignment_group(raw_group: Any) -> AssignmentGroupConfig:
  group = _require_dict(raw_group, "assignment_group")
  group_type = group.get('type')
  if not group_type:
    raise ValueError(f"assignment_group is missing required key 'type': {group}")

  group_settings = _extract_settings(group,
                                     {'name', 'type', 'assignments', 'settings'})
  group_settings.update(group.get('settings', {}))

  raw_assignments = group.get('assignments', [])
  if not isinstance(raw_assignments, list):
    raise ValueError(
      f"assignment_group.assignments must be a list for type '{group_type}'")

  return AssignmentGroupConfig(
    type_name=group_type,
    name=group.get('name'),
    settings=group_settings,
    assignments=[_parse_assignment(a) for a in raw_assignments],
  )


def _parse_course(raw_course: Any) -> CourseConfig:
  course = _require_dict(raw_course, "course")
  if 'id' not in course:
    raise ValueError(f"course is missing required key 'id': {course}")

  raw_groups = course.get('assignment_groups')
  if raw_groups is None:
    raise ValueError(
      "course config must define assignment_groups")
  if not isinstance(raw_groups, list):
    raise ValueError("course.assignment_groups must be a list")

  course_settings = _extract_settings(
    course, {'id', 'name', 'slack_channel', 'assignment_groups'})

  return CourseConfig(
    id=int(course['id']),
    name=course.get('name'),
    slack_channel=course.get('slack_channel'),
    settings=course_settings,
    assignment_groups=[_parse_assignment_group(g) for g in raw_groups],
  )


def _parse_assignment_types(
  raw_types: Any) -> Dict[str, AssignmentTypeConfig]:
  assignment_types = _require_dict(raw_types, "assignment_types")
  parsed: Dict[str, AssignmentTypeConfig] = {}

  for name, raw_type in assignment_types.items():
    type_config = _require_dict(raw_type, f"assignment_types.{name}")
    kind = type_config.get('kind')
    if not kind:
      raise ValueError(
        f"assignment_types.{name} is missing required key 'kind'")

    parsed[name] = AssignmentTypeConfig(
      name=name,
      kind=kind,
      grader=type_config.get('grader', 'Dummy'),
      settings=dict(type_config.get('settings', {})),
    )

  return parsed


def parse_run_config(raw_config: Any) -> RunConfig:
  config = _require_dict(raw_config, "config")

  if 'assignment_types' not in config:
    raise ValueError("assignment_types is required")

  raw_courses = config.get('courses', [])
  if not isinstance(raw_courses, list):
    raise ValueError("courses must be a list")

  assignment_types = _parse_assignment_types(config['assignment_types'])
  courses = [_parse_course(c) for c in raw_courses]

  for course in courses:
    for group in course.assignment_groups:
      if group.type_name not in assignment_types:
        raise ValueError(
          f"unknown assignment group type '{group.type_name}' in course {course.id}")

  return RunConfig(
    prod=bool(config.get('prod', False)),
    push=bool(config.get('push', False)),
    reporting=dict(config.get('reporting', {})),
    error_slack_channel=config.get('error_slack_channel'),
    assignment_types=assignment_types,
    courses=courses,
  )
