from __future__ import annotations

from dataclasses import dataclass, field
import posixpath
from typing import Any, Dict, List, Optional

DEFAULT_TEMPLATE_BASE_IMAGE = "python:3.11-slim"
DEFAULT_TEMPLATE_SOURCE_REPO = (
  "https://github.com/CSUMB-SCD-instructors/course-template")
DEFAULT_CONTAINER_REPO_PATH = "/repo/programming-assignments"
VALID_TIERS = {"small", "medium", "large"}


def _config_error(message: str) -> ValueError:
  return ValueError(f"Config error: {message}")


def _require_mapping(value: Any, label: str) -> Dict[str, Any]:
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
  if normalized not in VALID_TIERS:
    raise _config_error(f"{label} must be one of: small, medium, large")
  return normalized


def _normalize_container_repo_path(value: Any, label: str) -> str:
  if value is None:
    return DEFAULT_CONTAINER_REPO_PATH
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


@dataclass
class FilePathTargetConfig:
  path: str = ""
  name: Optional[str] = None

  def __post_init__(self) -> None:
    if not isinstance(self.path, str):
      raise _config_error("file_paths.path must be a string")
    if self.name is not None and not isinstance(self.name, str):
      raise _config_error("file_paths.name must be a string")

  @classmethod
  def from_raw(cls, value: Any, label: str) -> "FilePathTargetConfig":
    raw = _require_mapping(value, label)
    unknown = sorted(k for k in raw.keys() if k not in {"path", "name"})
    if unknown:
      raise _config_error(f"{label} has unsupported key(s): {', '.join(unknown)}")

    path = raw.get("path", "")
    name = raw.get("name")
    if not isinstance(path, str):
      raise _config_error(f"{label}.path must be a string")
    if name is not None and not isinstance(name, str):
      raise _config_error(f"{label}.name must be a string")
    return cls(path=path, name=name)


@dataclass
class AdditionalRepoConfig:
  source_repo: str
  container_path: str
  depth: Optional[int] = 1

  def __post_init__(self) -> None:
    if not isinstance(self.source_repo, str) or not self.source_repo.strip():
      raise _config_error("additional_repos.source_repo is required")
    self.container_path = _normalize_container_repo_path(
      self.container_path, "additional_repos.container_path")
    if self.container_path == "/repo":
      raise _config_error(
        "additional_repos.container_path cannot be /repo (reserved for source_repo)")
    if self.depth is not None:
      if not isinstance(self.depth, int):
        raise _config_error("additional_repos.depth must be an integer")
      if self.depth <= 0:
        raise _config_error("additional_repos.depth must be >= 1 when provided")

  @classmethod
  def from_raw(cls, value: Any, label: str) -> "AdditionalRepoConfig":
    raw = _require_mapping(value, label)
    unknown = sorted(
      k for k in raw.keys() if k not in {"source_repo", "container_path", "depth"})
    if unknown:
      raise _config_error(f"{label} has unsupported key(s): {', '.join(unknown)}")

    source_repo = _require_optional_str(raw.get("source_repo"), f"{label}.source_repo")
    if source_repo is None or not source_repo.strip():
      raise _config_error(f"{label}.source_repo is required")

    container_path = _normalize_container_repo_path(
      raw.get("container_path"), f"{label}.container_path")
    if container_path == "/repo":
      raise _config_error(
        f"{label}.container_path cannot be /repo (reserved for source_repo)")

    depth = _require_optional_int(raw.get("depth", 1), f"{label}.depth")
    if depth is not None and depth <= 0:
      raise _config_error(f"{label}.depth must be >= 1 when provided")

    return cls(source_repo=source_repo, container_path=container_path, depth=depth)


@dataclass
class TemplateGraderSettings:
  base_image_name: str = DEFAULT_TEMPLATE_BASE_IMAGE
  source_repo: str = DEFAULT_TEMPLATE_SOURCE_REPO
  additional_repos: List[AdditionalRepoConfig] = field(default_factory=list)
  container_repo_path: str = DEFAULT_CONTAINER_REPO_PATH
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

  def __post_init__(self) -> None:
    if not isinstance(self.base_image_name, str):
      raise _config_error("template-grader.base_image_name must be a string")
    if not isinstance(self.source_repo, str):
      raise _config_error("template-grader.source_repo must be a string")
    if not isinstance(self.student_code_path, str):
      raise _config_error("template-grader.student_code_path must be a string")
    self.container_repo_path = _normalize_container_repo_path(
      self.container_repo_path, "template-grader.container_repo_path")
    if not isinstance(self.additional_repos, list):
      raise _config_error("template-grader.additional_repos must be a list")
    if not isinstance(self.file_paths, dict):
      raise _config_error("template-grader.file_paths must be a mapping")

    additional_paths = sorted(repo.container_path for repo in self.additional_repos)
    for i, path_i in enumerate(additional_paths):
      for path_j in additional_paths[i + 1:]:
        if path_j.startswith(f"{path_i}/") or path_i.startswith(f"{path_j}/"):
          raise _config_error(
            "template-grader.additional_repos contain overlapping "
            f"container_path values: '{path_i}' and '{path_j}'")

  @classmethod
  def from_raw(cls, value: Any, context_label: str) -> "TemplateGraderSettings":
    raw = _require_mapping(value, context_label)

    if "extra_install_lines" in raw:
      raise _config_error(
        f"{context_label}.extra_install_lines is not supported; "
        "use extra_dockerfile_lines")

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
        f"{context_label} contains unsupported template-grader setting(s): "
        f"{', '.join(unknown)}")

    file_paths_raw = raw.get("file_paths", {})
    if not isinstance(file_paths_raw, dict):
      raise _config_error(f"{context_label}.file_paths must be a mapping")
    file_paths: Dict[str, FilePathTargetConfig] = {}
    for pattern, target in file_paths_raw.items():
      if not isinstance(pattern, str):
        raise _config_error(f"{context_label}.file_paths keys must be strings")
      file_paths[pattern] = FilePathTargetConfig.from_raw(
        target, f"{context_label}.file_paths['{pattern}']")

    additional_repos_raw = raw.get("additional_repos", [])
    if not isinstance(additional_repos_raw, list):
      raise _config_error(f"{context_label}.additional_repos must be a list")
    additional_repos: List[AdditionalRepoConfig] = []
    for i, repo_entry in enumerate(additional_repos_raw):
      additional_repos.append(
        AdditionalRepoConfig.from_raw(
          repo_entry, f"{context_label}.additional_repos[{i}]"))

    settings = cls(
      base_image_name=str(raw.get("base_image_name", DEFAULT_TEMPLATE_BASE_IMAGE)),
      source_repo=str(raw.get("source_repo", DEFAULT_TEMPLATE_SOURCE_REPO)),
      additional_repos=additional_repos,
      container_repo_path=_normalize_container_repo_path(
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
    return settings

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

  def __post_init__(self) -> None:
    self.phase1_tier = _require_tier(self.phase1_tier,
                                     "TextSubmissionGrader.phase1_tier")
    self.phase2_tier = _require_tier(self.phase2_tier,
                                     "TextSubmissionGrader.phase2_tier")
    self.phase25_tier = _require_tier(self.phase25_tier,
                                      "TextSubmissionGrader.phase25_tier")

  @classmethod
  def from_raw(cls, value: Any, context_label: str) -> "TextSubmissionGraderSettings":
    raw = _require_mapping(value, context_label)

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
        f"{context_label} contains unsupported TextSubmissionGrader setting(s): "
        f"{', '.join(unknown)}")

    return cls(
      grade_after_lock_date=_require_bool(
        raw.get("grade_after_lock_date", False),
        f"{context_label}.grade_after_lock_date"),
      prefer_anthropic=_require_bool(
        raw.get("prefer_anthropic", False),
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

