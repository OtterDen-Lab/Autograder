from __future__ import annotations

from dataclasses import dataclass, field
import posixpath
from typing import Any, Dict, List, Optional

_FALLBACK_ACTIVE_ASSIGNMENT_KINDS = {"ProgrammingAssignment", "TextAssignment"}
_FALLBACK_ACTIVE_GRADERS_BY_KIND = {
  "ProgrammingAssignment": {"template-grader"},
  "TextAssignment": {"TextSubmissionGrader"},
}


def _copy_grader_map(raw: Dict[str, set[str]]) -> Dict[str, set[str]]:
  return {kind: set(graders) for kind, graders in raw.items()}


def get_active_grader_compatibility() -> tuple[set[str], Dict[str, set[str]]]:
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
  except Exception:
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


@dataclass
class FilePathTargetConfig:
  path: str = ""
  name: Optional[str] = None


@dataclass
class AdditionalRepoConfig:
  source_repo: str
  container_path: str
  depth: Optional[int] = 1


@dataclass
class TemplateGraderSettings:
  base_image_name: str = "python:3.11-slim"
  source_repo: str = "https://github.com/CSUMB-SCD-instructors/course-template"
  additional_repos: List[AdditionalRepoConfig] = field(default_factory=list)
  container_repo_path: str = "/repo/programming-assignments"
  student_code_path: str = ""
  extra_installs: List[str] = field(default_factory=list)
  extra_dockerfile_lines: List[str] = field(default_factory=list)
  file_paths: Dict[str, FilePathTargetConfig] = field(default_factory=dict)
  golden_repo: Optional[str] = None
  files_from_golden: List[str] = field(default_factory=list)
  record_retention: bool = False
  records_dir: Optional[str] = None
  report_errors: bool = True
  slack_webhook: Optional[str] = None
  slack_token: Optional[str] = None
  slack_channel: Optional[str] = None
  num_repeats: Optional[int] = None

  def to_kwargs(self) -> Dict[str, Any]:
    file_paths: Dict[str, Dict[str, str]] = {}
    for pattern, target in self.file_paths.items():
      entry = {"path": target.path}
      if target.name is not None:
        entry["name"] = target.name
      file_paths[pattern] = entry

    additional_repos: List[Dict[str, Any]] = []
    for repo in self.additional_repos:
      additional_repos.append({
        "source_repo": repo.source_repo,
        "container_path": repo.container_path,
        "depth": repo.depth,
      })

    return {
      "base_image_name": self.base_image_name,
      "source_repo": self.source_repo,
      "additional_repos": additional_repos,
      "container_repo_path": self.container_repo_path,
      "student_code_path": self.student_code_path,
      "extra_installs": self.extra_installs,
      "extra_dockerfile_lines": self.extra_dockerfile_lines,
      "file_paths": file_paths,
      "golden_repo": self.golden_repo,
      "files_from_golden": self.files_from_golden,
      "record_retention": self.record_retention,
      "records_dir": self.records_dir,
      "report_errors": self.report_errors,
      "slack_webhook": self.slack_webhook,
      "slack_token": self.slack_token,
      "slack_channel": self.slack_channel,
      "num_repeats": self.num_repeats,
    }


@dataclass
class TextSubmissionGraderSettings:
  grade_after_lock_date: bool = False
  prefer_anthropic: bool = False
  phase1_tier: str = "small"
  phase2_tier: str = "small"
  phase25_tier: str = "small"
  records_dir: Optional[str] = None
  record_retention: bool = False
  report_errors: bool = True
  slack_webhook: Optional[str] = None
  slack_token: Optional[str] = None
  slack_channel: Optional[str] = None

  def to_kwargs(self) -> Dict[str, Any]:
    return {
      "grade_after_lock_date": self.grade_after_lock_date,
      "prefer_anthropic": self.prefer_anthropic,
      "phase1_tier": self.phase1_tier,
      "phase2_tier": self.phase2_tier,
      "phase25_tier": self.phase25_tier,
      "records_dir": self.records_dir,
      "record_retention": self.record_retention,
      "report_errors": self.report_errors,
      "slack_webhook": self.slack_webhook,
      "slack_token": self.slack_token,
      "slack_channel": self.slack_channel,
    }


def _config_error(message: str) -> ValueError:
  return ValueError(f"Config error: {message}")


def _require_dict(value: Any, label: str) -> Dict[str, Any]:
  if not isinstance(value, dict):
    raise _config_error(f"{label} must be a mapping")
  return value


def _require_optional_str(value: Any, label: str) -> Optional[str]:
  if value is None:
    return None
  if not isinstance(value, str):
    raise _config_error(f"{label} must be a string")
  return value


def _require_bool(value: Any, label: str) -> bool:
  if not isinstance(value, bool):
    raise _config_error(f"{label} must be a boolean")
  return value


def _require_optional_int(value: Any, label: str) -> Optional[int]:
  if value is None:
    return None
  if not isinstance(value, int):
    raise _config_error(f"{label} must be an integer")
  return value


def _require_str_list(value: Any, label: str) -> List[str]:
  if value is None:
    return []
  if isinstance(value, str):
    return [value]
  if not isinstance(value, list):
    raise _config_error(f"{label} must be a list of strings")
  for i, item in enumerate(value):
    if not isinstance(item, str):
      raise _config_error(f"{label}[{i}] must be a string")
  return value


def _require_tier(value: Any, label: str) -> str:
  if not isinstance(value, str):
    raise _config_error(f"{label} must be one of: small, medium, large")
  normalized = value.strip().lower()
  if normalized not in {"small", "medium", "large"}:
    raise _config_error(f"{label} must be one of: small, medium, large")
  return normalized


def _require_container_repo_path(value: Any, label: str) -> str:
  if value is None:
    return "/repo/programming-assignments"
  if not isinstance(value, str):
    raise _config_error(f"{label} must be a string")
  raw = value.strip()
  if not raw:
    raise _config_error(f"{label} cannot be empty")

  normalized = posixpath.normpath(raw)
  if not normalized.startswith("/"):
    raise _config_error(f"{label} must be an absolute path under /repo")
  if normalized != "/repo" and not normalized.startswith("/repo/"):
    raise _config_error(f"{label} must be within /repo")
  return normalized


def _normalize_template_grader_settings(
    settings: Dict[str, Any], context_label: str) -> Dict[str, Any]:
  raw = dict(settings)
  if "extra_install_lines" in raw:
    raise _config_error(
      f"{context_label}.extra_install_lines is not supported; use extra_dockerfile_lines"
    )

  allowed = {
    "base_image_name",
    "source_repo",
    "additional_repos",
    "container_repo_path",
    "student_code_path",
    "extra_installs",
    "extra_dockerfile_lines",
    "file_paths",
    "golden_repo",
    "files_from_golden",
    "record_retention",
    "records_dir",
    "report_errors",
    "slack_webhook",
    "slack_token",
    "slack_channel",
    "num_repeats",
  }
  unknown = sorted(k for k in raw.keys() if k not in allowed)
  if unknown:
    raise _config_error(
      f"{context_label} contains unsupported template-grader setting(s): {', '.join(unknown)}"
    )

  file_paths_raw = raw.get("file_paths", {})
  if not isinstance(file_paths_raw, dict):
    raise _config_error(f"{context_label}.file_paths must be a mapping")
  file_paths: Dict[str, FilePathTargetConfig] = {}
  for pattern, target in file_paths_raw.items():
    if not isinstance(pattern, str):
      raise _config_error(f"{context_label}.file_paths keys must be strings")
    if not isinstance(target, dict):
      raise _config_error(
        f"{context_label}.file_paths['{pattern}'] must be a mapping")
    target_unknown = sorted(k for k in target.keys() if k not in {"path", "name"})
    if target_unknown:
      raise _config_error(
        f"{context_label}.file_paths['{pattern}'] has unsupported key(s): {', '.join(target_unknown)}"
      )
    path = target.get("path", "")
    name = target.get("name")
    if not isinstance(path, str):
      raise _config_error(
        f"{context_label}.file_paths['{pattern}'].path must be a string")
    if name is not None and not isinstance(name, str):
      raise _config_error(
        f"{context_label}.file_paths['{pattern}'].name must be a string")
    file_paths[pattern] = FilePathTargetConfig(path=path, name=name)

  additional_repos_raw = raw.get("additional_repos", [])
  if not isinstance(additional_repos_raw, list):
    raise _config_error(f"{context_label}.additional_repos must be a list")
  additional_repos: List[AdditionalRepoConfig] = []
  for i, repo_entry in enumerate(additional_repos_raw):
    label = f"{context_label}.additional_repos[{i}]"
    if not isinstance(repo_entry, dict):
      raise _config_error(f"{label} must be a mapping")
    repo_unknown = sorted(
      k for k in repo_entry.keys() if k not in {"source_repo", "container_path", "depth"})
    if repo_unknown:
      raise _config_error(
        f"{label} has unsupported key(s): {', '.join(repo_unknown)}")

    source_repo = _require_optional_str(repo_entry.get("source_repo"),
                                        f"{label}.source_repo")
    if source_repo is None or not source_repo.strip():
      raise _config_error(f"{label}.source_repo is required")

    container_path = _require_container_repo_path(
      repo_entry.get("container_path"), f"{label}.container_path")
    if container_path == "/repo":
      raise _config_error(
        f"{label}.container_path cannot be /repo (reserved for source_repo)")

    depth = _require_optional_int(repo_entry.get("depth", 1),
                                  f"{label}.depth")
    if depth is not None and depth <= 0:
      raise _config_error(f"{label}.depth must be >= 1 when provided")

    additional_repos.append(
      AdditionalRepoConfig(source_repo=source_repo,
                           container_path=container_path,
                           depth=depth))

  additional_paths = sorted(r.container_path for r in additional_repos)
  for i, path_i in enumerate(additional_paths):
    for path_j in additional_paths[i + 1:]:
      if path_j.startswith(f"{path_i}/") or path_i.startswith(f"{path_j}/"):
        raise _config_error(
          f"{context_label}.additional_repos contain overlapping container_path values: '{path_i}' and '{path_j}'"
        )

  settings_obj = TemplateGraderSettings(
    base_image_name=str(raw.get("base_image_name", "python:3.11-slim")),
    source_repo=str(
      raw.get("source_repo",
              "https://github.com/CSUMB-SCD-instructors/course-template")),
    additional_repos=additional_repos,
    container_repo_path=_require_container_repo_path(
      raw.get("container_repo_path"), f"{context_label}.container_repo_path"),
    student_code_path=str(raw.get("student_code_path", "")),
    extra_installs=_require_str_list(raw.get("extra_installs"),
                                     f"{context_label}.extra_installs"),
    extra_dockerfile_lines=_require_str_list(
      raw.get("extra_dockerfile_lines"),
      f"{context_label}.extra_dockerfile_lines"),
    file_paths=file_paths,
    golden_repo=_require_optional_str(raw.get("golden_repo"),
                                      f"{context_label}.golden_repo"),
    files_from_golden=_require_str_list(raw.get("files_from_golden"),
                                        f"{context_label}.files_from_golden"),
    record_retention=_require_bool(raw.get("record_retention", False),
                                   f"{context_label}.record_retention"),
    records_dir=_require_optional_str(raw.get("records_dir"),
                                      f"{context_label}.records_dir"),
    report_errors=_require_bool(raw.get("report_errors", True),
                                f"{context_label}.report_errors"),
    slack_webhook=_require_optional_str(raw.get("slack_webhook"),
                                        f"{context_label}.slack_webhook"),
    slack_token=_require_optional_str(raw.get("slack_token"),
                                      f"{context_label}.slack_token"),
    slack_channel=_require_optional_str(raw.get("slack_channel"),
                                        f"{context_label}.slack_channel"),
    num_repeats=_require_optional_int(raw.get("num_repeats"),
                                      f"{context_label}.num_repeats"),
  )
  return settings_obj.to_kwargs()


def _normalize_text_submission_grader_settings(
    settings: Dict[str, Any], context_label: str) -> Dict[str, Any]:
  raw = dict(settings)
  allowed = {
    "grade_after_lock_date",
    "prefer_anthropic",
    "phase1_tier",
    "phase2_tier",
    "phase25_tier",
    "records_dir",
    "record_retention",
    "report_errors",
    "slack_webhook",
    "slack_token",
    "slack_channel",
  }
  unknown = sorted(k for k in raw.keys() if k not in allowed)
  if unknown:
    raise _config_error(
      f"{context_label} contains unsupported TextSubmissionGrader setting(s): {', '.join(unknown)}"
    )

  settings_obj = TextSubmissionGraderSettings(
    grade_after_lock_date=_require_bool(raw.get("grade_after_lock_date", False),
                                        f"{context_label}.grade_after_lock_date"),
    prefer_anthropic=_require_bool(raw.get("prefer_anthropic", False),
                                   f"{context_label}.prefer_anthropic"),
    phase1_tier=_require_tier(raw.get("phase1_tier", "small"),
                              f"{context_label}.phase1_tier"),
    phase2_tier=_require_tier(raw.get("phase2_tier", "small"),
                              f"{context_label}.phase2_tier"),
    phase25_tier=_require_tier(raw.get("phase25_tier", "small"),
                               f"{context_label}.phase25_tier"),
    records_dir=_require_optional_str(raw.get("records_dir"),
                                      f"{context_label}.records_dir"),
    record_retention=_require_bool(raw.get("record_retention", False),
                                   f"{context_label}.record_retention"),
    report_errors=_require_bool(raw.get("report_errors", True),
                                f"{context_label}.report_errors"),
    slack_webhook=_require_optional_str(raw.get("slack_webhook"),
                                        f"{context_label}.slack_webhook"),
    slack_token=_require_optional_str(raw.get("slack_token"),
                                      f"{context_label}.slack_token"),
    slack_channel=_require_optional_str(raw.get("slack_channel"),
                                        f"{context_label}.slack_channel"),
  )
  return settings_obj.to_kwargs()


def normalize_grader_settings(grader_name: str,
                              settings: Dict[str, Any],
                              context_label: str) -> Dict[str, Any]:
  if grader_name == "template-grader":
    return _normalize_template_grader_settings(settings, context_label)
  if grader_name == "TextSubmissionGrader":
    return _normalize_text_submission_grader_settings(settings, context_label)

  raise _config_error(f"Unsupported grader for settings validation: {grader_name}")


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
  active_assignment_kinds, active_graders_by_kind = (
    get_active_grader_compatibility())
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
