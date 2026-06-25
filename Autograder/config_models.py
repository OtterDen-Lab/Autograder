from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from Autograder.grader_settings import (
  AdditionalRepoConfig,
  ExternalToolGraderSettings,
  FilePathTargetConfig,
  TemplateGraderSettings,
  TextSubmissionGraderSettings,
)

_FALLBACK_ACTIVE_ASSIGNMENT_KINDS = {
  "ExternalToolAssignment",
  "ProgrammingAssignment",
  "TextAssignment"
}
_FALLBACK_ACTIVE_GRADERS_BY_KIND = {
  "ExternalToolAssignment": {"panopto-watch-grader"},
  "ProgrammingAssignment": {"template-grader"},
  "TextAssignment": {"TextSubmissionGrader", "WeeklyStudyNotesGrader"},
}


def _copy_grader_map(raw: Dict[str, set[str]]) -> Dict[str, set[str]]:
  return {kind: set(graders) for kind, graders in raw.items()}


def get_active_grader_compatibility(
    strict: bool = False) -> tuple[set[str], Dict[str, set[str]]]:
  """
  Discover assignment-kind/grader compatibility from registered graders.

  Falls back to conservative static defaults if discovery fails or no grader
  compatibility metadata is available.
  """
  discovered_by_kind: Dict[str, set[str]] = {}
  try:
    from Autograder.registry import GraderRegistry

    if not GraderRegistry._scanned:
      GraderRegistry.load_premade_modules()

    for registered_name, grader_cls in GraderRegistry._registry.items():
      compatible_kinds = getattr(grader_cls, "COMPATIBLE_KINDS", None)
      if compatible_kinds is None:
        continue

      if isinstance(compatible_kinds, str):
        kinds = [compatible_kinds]
      else:
        kinds = list(compatible_kinds)

      display_name = getattr(grader_cls, "_registry_name", registered_name)
      for kind in kinds:
        if not isinstance(kind, str):
          continue
        normalized_kind = kind.strip()
        if not normalized_kind:
          continue
        discovered_by_kind.setdefault(normalized_kind, set()).add(display_name)
  except Exception as e:
    if strict:
      raise ValueError(
        f"Failed to discover grader compatibility from registry: {e}") from e
    discovered_by_kind = {}

  if discovered_by_kind:
    return set(discovered_by_kind.keys()), discovered_by_kind
  return (set(_FALLBACK_ACTIVE_ASSIGNMENT_KINDS),
          _copy_grader_map(_FALLBACK_ACTIVE_GRADERS_BY_KIND))


ACTIVE_ASSIGNMENT_KINDS, ACTIVE_GRADERS_BY_KIND = (
  get_active_grader_compatibility())


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
  privacy_mode: str = "id_only"
  reveal_identity: bool = False
  idempotency_key: Optional[str] = None
  idempotency_state_dir: str = "~/.autograder/idempotency"
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
  reveal_identity: bool = False
  privacy_mode: str = "id_only"
  idempotency_key: Optional[str] = None
  idempotency_state_dir: str = "~/.autograder/idempotency"


def _config_error(message: str) -> ValueError:
  return ValueError(f"Config error: {message}")


def _require_dict(value: Any, label: str) -> Dict[str, Any]:
  if not isinstance(value, dict):
    raise _config_error(f"{label} must be a mapping")
  return value


def _normalize_template_grader_settings(
    settings: Dict[str, Any], context_label: str) -> Dict[str, Any]:
  return TemplateGraderSettings.from_raw(settings, context_label).to_kwargs()


def _normalize_text_submission_grader_settings(
    settings: Dict[str, Any], context_label: str) -> Dict[str, Any]:
  return TextSubmissionGraderSettings.from_raw(settings, context_label).to_kwargs()


def _normalize_external_tool_grader_settings(
    settings: Dict[str, Any], context_label: str) -> Dict[str, Any]:
  return ExternalToolGraderSettings.from_raw(settings, context_label).to_kwargs()


def _normalize_known_grader_settings_without_registry(
    grader_name: str, settings: Dict[str, Any],
    context_label: str) -> Optional[Dict[str, Any]]:
  if grader_name == "template-grader":
    return _normalize_template_grader_settings(settings, context_label)
  if grader_name in {"TextSubmissionGrader", "WeeklyStudyNotesGrader"}:
    return _normalize_text_submission_grader_settings(settings, context_label)
  if grader_name == "panopto-watch-grader":
    return _normalize_external_tool_grader_settings(settings, context_label)
  return None


def normalize_grader_settings(grader_name: str,
                              settings: Dict[str, Any],
                              context_label: str) -> Dict[str, Any]:
  """
  Validate and normalize grader settings by dispatching to the grader class.

  The grader class is looked up via the GraderRegistry, and its
  normalize_settings() classmethod is called to perform validation.
  This allows graders to own their own settings validation logic.
  """
  from Autograder.registry import GraderRegistry

  try:
    grader_cls = GraderRegistry.get_class(grader_name)
  except ValueError:
    raise _config_error(
      f"Unknown grader for settings validation: {grader_name}")
  except Exception as exc:
    normalized = _normalize_known_grader_settings_without_registry(
      grader_name, settings, context_label)
    if normalized is not None:
      return normalized
    raise _config_error(
      "Failed to load grader registry while validating "
      f"'{grader_name}': {exc.__class__.__name__}: {exc}"
    ) from exc

  # Check if the grader class has a normalize_settings method
  if hasattr(grader_cls, 'normalize_settings'):
    return grader_cls.normalize_settings(settings, context_label)

  # Fall back to returning settings unchanged if no normalization defined
  return dict(settings)


def _extract_settings(source: Dict[str, Any], reserved_keys: set[str]) -> Dict[str, Any]:
  return {k: v for k, v in source.items() if k not in reserved_keys}


def _parse_assignment(raw_assignment: Any) -> AssignmentConfig:
  if isinstance(raw_assignment, (int, str)):
    return AssignmentConfig(id=int(raw_assignment))

  assignment = _require_dict(raw_assignment, "assignment")
  if 'id' not in assignment:
    raise _config_error(f"assignment is missing required key 'id': {assignment}")

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
    raise _config_error(f"assignment_group is missing required key 'type': {group}")

  group_settings = _extract_settings(group,
                                     {'name', 'type', 'assignments', 'settings'})
  group_settings.update(group.get('settings', {}))

  raw_assignments = group.get('assignments', [])
  if not isinstance(raw_assignments, list):
    raise _config_error(
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
    raise _config_error(f"course is missing required key 'id': {course}")

  raw_groups = course.get('assignment_groups')
  if raw_groups is None:
    raise _config_error(
      "course config must define assignment_groups")
  if not isinstance(raw_groups, list):
    raise _config_error("course.assignment_groups must be a list")

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
  try:
    try:
      active_assignment_kinds, active_graders_by_kind = (
        get_active_grader_compatibility(strict=True))
    except TypeError:
      # Backward-compatible path for monkeypatched tests/helpers that don't
      # accept the strict kwarg.
      active_assignment_kinds, active_graders_by_kind = (
        get_active_grader_compatibility())
  except ValueError as e:
    raise _config_error(str(e)) from e
  assignment_types = _require_dict(raw_types, "assignment_types")
  parsed: Dict[str, AssignmentTypeConfig] = {}

  for name, raw_type in assignment_types.items():
    type_config = _require_dict(raw_type, f"assignment_types.{name}")
    kind = type_config.get('kind')
    if not kind:
      raise _config_error(
        f"assignment_types.{name} is missing required key 'kind'")
    if kind not in active_assignment_kinds:
      supported = ", ".join(sorted(active_assignment_kinds))
      raise _config_error(
        f"assignment_types.{name}.kind '{kind}' is not supported in this build. "
        f"Supported kinds: {supported}")

    grader = type_config.get('grader')
    if not grader:
      raise _config_error(
        f"assignment_types.{name} is missing required key 'grader'")

    allowed_graders = active_graders_by_kind.get(kind, set())
    if grader not in allowed_graders:
      allowed = ", ".join(sorted(allowed_graders)) or "(none)"
      raise _config_error(
        f"assignment_types.{name}.grader '{grader}' is not supported for kind '{kind}'. "
        f"Allowed graders: {allowed}")

    parsed[name] = AssignmentTypeConfig(
      name=name,
      kind=kind,
      grader=grader,
      settings=dict(type_config.get('settings', {})),
    )

  return parsed


def parse_run_config(raw_config: Any) -> RunConfig:
  config = _require_dict(raw_config, "config")

  if 'assignment_types' not in config:
    raise _config_error("assignment_types is required")

  raw_courses = config.get('courses', [])
  if not isinstance(raw_courses, list):
    raise _config_error("courses must be a list")

  assignment_types = _parse_assignment_types(config['assignment_types'])
  courses = [_parse_course(c) for c in raw_courses]
  privacy_mode = config.get('privacy_mode', 'id_only')
  if privacy_mode not in {"none", "id_only", "blind"}:
    raise _config_error(
      "privacy_mode must be one of: none, id_only, blind")

  idempotency_key = config.get('idempotency_key')
  if idempotency_key is not None and not isinstance(idempotency_key, str):
    raise _config_error("idempotency_key must be a string when provided")
  idempotency_state_dir = config.get('idempotency_state_dir',
                                     "~/.autograder/idempotency")
  if not isinstance(idempotency_state_dir, str):
    raise _config_error("idempotency_state_dir must be a string")

  for course in courses:
    for group in course.assignment_groups:
      if group.type_name not in assignment_types:
        raise _config_error(
          f"unknown assignment group type '{group.type_name}' in course {course.id}")

      seen_assignment_ids = set()
      duplicate_assignment_ids = set()
      for assignment in group.assignments:
        if assignment.disabled:
          continue
        if assignment.id in seen_assignment_ids:
          duplicate_assignment_ids.add(assignment.id)
        seen_assignment_ids.add(assignment.id)

      if duplicate_assignment_ids:
        duplicates = ", ".join(
          str(assignment_id)
          for assignment_id in sorted(duplicate_assignment_ids))
        group_label = group.name or group.type_name
        raise _config_error(
          f"duplicate assignment id(s) in course {course.id}, "
          f"group '{group_label}' ({group.type_name}): {duplicates}")

  return RunConfig(
    prod=bool(config.get('prod', False)),
    push=bool(config.get('push', False)),
    privacy_mode=privacy_mode,
    reveal_identity=bool(config.get('reveal_identity', False)),
    idempotency_key=idempotency_key,
    idempotency_state_dir=idempotency_state_dir,
    reporting=dict(config.get('reporting', {})),
    error_slack_channel=config.get('error_slack_channel'),
    assignment_types=assignment_types,
    courses=courses,
  )
