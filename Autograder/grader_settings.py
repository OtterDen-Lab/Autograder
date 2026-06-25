from __future__ import annotations

from dataclasses import dataclass, field
import posixpath
from typing import Any, Dict, List, Optional

DEFAULT_TEMPLATE_BASE_IMAGE = "python:3.11-slim"
DEFAULT_TEMPLATE_SOURCE_REPO = (
  "https://github.com/CSUMB-SCD-instructors/course-template")
DEFAULT_CONTAINER_REPO_PATH = "/repo/programming-assignments"
VALID_TIERS = {"small", "medium", "large"}
VALID_EXTERNAL_USER_ATTRIBUTES = {
  "email",
  "login_id",
  "username",
  "sis_user_id",
  "name",
  "sortable_name",
}


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


def _require_non_negative_int(value: Any, label: str, default: int = 0) -> int:
  if value is None:
    return default
  if not isinstance(value, int):
    raise _config_error(f"{label} must be an integer")
  if value < 0:
    raise _config_error(f"{label} must be >= 0")
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


def _require_non_negative_float(value: Any,
                                label: str,
                                default: float = 0.0) -> float:
  if value is None:
    return default
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise _config_error(f"{label} must be a number")
  if value < 0:
    raise _config_error(f"{label} must be >= 0")
  return float(value)


def _require_float_in_range(value: Any,
                            label: str,
                            *,
                            minimum: float,
                            maximum: float,
                            default: float) -> float:
  normalized = _require_non_negative_float(value, label, default)
  if normalized < minimum or normalized > maximum:
    raise _config_error(f"{label} must be between {minimum} and {maximum}")
  return normalized


def _require_string_keyed_mapping(value: Any, label: str) -> Dict[str, str]:
  raw = _require_mapping(value, label)
  normalized: Dict[str, str] = {}
  for key, item in raw.items():
    if not isinstance(key, (str, int)):
      raise _config_error(f"{label} keys must be strings or integers")
    if not isinstance(item, str):
      raise _config_error(f"{label}[{key!r}] must be a string")
    normalized[str(key)] = item
  return normalized


def _require_choice(value: Any,
                    label: str,
                    *,
                    allowed: set[str],
                    default: str) -> str:
  if value is None:
    return default
  if not isinstance(value, str):
    raise _config_error(f"{label} must be one of: {', '.join(sorted(allowed))}")
  normalized = value.strip()
  if normalized not in allowed:
    raise _config_error(f"{label} must be one of: {', '.join(sorted(allowed))}")
  return normalized


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
  upload_error_artifacts: bool = False
  slack_webhook: Optional[str] = None
  slack_token: Optional[str] = None
  slack_channel: Optional[str] = None
  grading_script: Optional[str] = None
  grading_args: List[str] = field(default_factory=list)
  grading_workdir: Optional[str] = None
  num_repeats: Optional[int] = None
  rubric: Optional[Dict[str, Any]] = None

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
    if self.rubric is not None and not isinstance(self.rubric, dict):
      raise _config_error("template-grader.rubric must be a mapping")

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
      "upload_error_artifacts",
      "slack_webhook",
      "slack_token",
      "slack_channel",
      "grading_script",
      "grading_args",
      "grading_workdir",
      "num_repeats",
      "rubric",
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

    rubric_raw = raw.get("rubric")
    rubric = None
    if rubric_raw is not None:
      rubric = dict(_require_mapping(rubric_raw, f"{context_label}.rubric"))

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
      upload_error_artifacts=_require_bool(
        raw.get("upload_error_artifacts", False),
        f"{context_label}.upload_error_artifacts"),
      slack_webhook=_require_optional_str(raw.get("slack_webhook"),
                                          f"{context_label}.slack_webhook"),
      slack_token=_require_optional_str(raw.get("slack_token"),
                                        f"{context_label}.slack_token"),
      slack_channel=_require_optional_str(raw.get("slack_channel"),
                                          f"{context_label}.slack_channel"),
      grading_script=_require_optional_str(raw.get("grading_script"),
                                           f"{context_label}.grading_script"),
      grading_args=_require_str_list(raw.get("grading_args"),
                                     f"{context_label}.grading_args"),
      grading_workdir=_require_optional_str(raw.get("grading_workdir"),
                                            f"{context_label}.grading_workdir"),
      num_repeats=_require_optional_int(raw.get("num_repeats"),
                                        f"{context_label}.num_repeats"),
      rubric=rubric,
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
      "upload_error_artifacts": self.upload_error_artifacts,
      "slack_webhook": self.slack_webhook,
      "slack_token": self.slack_token,
      "slack_channel": self.slack_channel,
      "grading_script": self.grading_script,
      "grading_args": self.grading_args,
      "grading_workdir": self.grading_workdir,
      "num_repeats": self.num_repeats,
      "rubric": dict(self.rubric) if self.rubric is not None else None,
    }


DEFAULT_TEXT_PROMPT_KEYS = {
  "aggregate_analysis",
  "individual_grading",
  "question_consolidation",
}

DEFAULT_TEXT_RUBRIC_DESCRIPTIONS = {
  "engagement": "Effort to process and explain material",
  "length": "Meeting word count requirement",
  "relevance": "Coverage of class topics",
  "explanation_quality": "Depth of explanation",
}

DEFAULT_TEXT_RUBRIC_POINTS = {
  "engagement": 4,
  "length": 2,
  "relevance": 2,
  "explanation_quality": 2,
}

DEFAULT_TEXT_WORD_THRESHOLD = 250


@dataclass
class TextRubricCriterionSettings:
  points: int
  description: str

  @classmethod
  def from_raw(cls,
               value: Any,
               *,
               label: str,
               default_points: int,
               default_description: str) -> "TextRubricCriterionSettings":
    if value is None:
      return cls(points=default_points, description=default_description)

    raw = _require_mapping(value, label)
    unknown = sorted(k for k in raw.keys() if k not in {"points", "description"})
    if unknown:
      raise _config_error(f"{label} has unsupported key(s): {', '.join(unknown)}")

    points = _require_non_negative_int(raw.get("points"), f"{label}.points",
                                       default_points)
    description = _require_optional_str(raw.get("description"),
                                        f"{label}.description")
    if description is None:
      description = default_description
    return cls(points=points, description=description)

  def to_kwargs(self) -> Dict[str, Any]:
    return {
      "points": self.points,
      "description": self.description,
    }


@dataclass
class TextRubricSettings:
  engagement: TextRubricCriterionSettings = field(
    default_factory=lambda: TextRubricCriterionSettings(
      points=DEFAULT_TEXT_RUBRIC_POINTS["engagement"],
      description=DEFAULT_TEXT_RUBRIC_DESCRIPTIONS["engagement"]))
  length: TextRubricCriterionSettings = field(
    default_factory=lambda: TextRubricCriterionSettings(
      points=DEFAULT_TEXT_RUBRIC_POINTS["length"],
      description=DEFAULT_TEXT_RUBRIC_DESCRIPTIONS["length"]))
  relevance: TextRubricCriterionSettings = field(
    default_factory=lambda: TextRubricCriterionSettings(
      points=DEFAULT_TEXT_RUBRIC_POINTS["relevance"],
      description=DEFAULT_TEXT_RUBRIC_DESCRIPTIONS["relevance"]))
  explanation_quality: TextRubricCriterionSettings = field(
    default_factory=lambda: TextRubricCriterionSettings(
      points=DEFAULT_TEXT_RUBRIC_POINTS["explanation_quality"],
      description=DEFAULT_TEXT_RUBRIC_DESCRIPTIONS["explanation_quality"]))
  word_threshold: int = DEFAULT_TEXT_WORD_THRESHOLD

  @classmethod
  def from_raw(cls, value: Any, context_label: str) -> "TextRubricSettings":
    if value is None:
      return cls()
    raw = _require_mapping(value, context_label)
    allowed = {
      "engagement",
      "length",
      "relevance",
      "explanation_quality",
      "word_threshold",
    }
    unknown = sorted(k for k in raw.keys() if k not in allowed)
    if unknown:
      raise _config_error(
        f"{context_label} contains unsupported rubric setting(s): "
        f"{', '.join(unknown)}")

    word_threshold = _require_non_negative_int(raw.get("word_threshold"),
                                               f"{context_label}.word_threshold",
                                               DEFAULT_TEXT_WORD_THRESHOLD)
    if word_threshold == 0:
      raise _config_error(f"{context_label}.word_threshold must be >= 1")

    return cls(
      engagement=TextRubricCriterionSettings.from_raw(
        raw.get("engagement"),
        label=f"{context_label}.engagement",
        default_points=DEFAULT_TEXT_RUBRIC_POINTS["engagement"],
        default_description=DEFAULT_TEXT_RUBRIC_DESCRIPTIONS["engagement"]),
      length=TextRubricCriterionSettings.from_raw(
        raw.get("length"),
        label=f"{context_label}.length",
        default_points=DEFAULT_TEXT_RUBRIC_POINTS["length"],
        default_description=DEFAULT_TEXT_RUBRIC_DESCRIPTIONS["length"]),
      relevance=TextRubricCriterionSettings.from_raw(
        raw.get("relevance"),
        label=f"{context_label}.relevance",
        default_points=DEFAULT_TEXT_RUBRIC_POINTS["relevance"],
        default_description=DEFAULT_TEXT_RUBRIC_DESCRIPTIONS["relevance"]),
      explanation_quality=TextRubricCriterionSettings.from_raw(
        raw.get("explanation_quality"),
        label=f"{context_label}.explanation_quality",
        default_points=DEFAULT_TEXT_RUBRIC_POINTS["explanation_quality"],
        default_description=DEFAULT_TEXT_RUBRIC_DESCRIPTIONS[
          "explanation_quality"]),
      word_threshold=word_threshold,
    )

  def total_points(self) -> int:
    return (self.engagement.points + self.length.points + self.relevance.points +
            self.explanation_quality.points)

  def to_kwargs(self) -> Dict[str, Any]:
    return {
      "engagement": self.engagement.to_kwargs(),
      "length": self.length.to_kwargs(),
      "relevance": self.relevance.to_kwargs(),
      "explanation_quality": self.explanation_quality.to_kwargs(),
      "word_threshold": self.word_threshold,
      "total_points": self.total_points(),
    }


@dataclass
class TextSubmissionGraderSettings:
  grade_after_lock_date: bool = False
  prefer_anthropic: bool = False
  phase1_tier: str = "small"
  phase2_tier: str = "small"
  phase25_tier: str = "small"
  rate_limit_retries: int = 0
  records_dir: Optional[str] = None
  record_retention: bool = False
  report_errors: bool = True
  slack_webhook: Optional[str] = None
  slack_token: Optional[str] = None
  slack_channel: Optional[str] = None
  prompt_templates: Dict[str, str] = field(default_factory=dict)
  rubric: TextRubricSettings = field(default_factory=TextRubricSettings)

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
      "rate_limit_retries",
      "records_dir",
      "record_retention",
      "report_errors",
      "slack_webhook",
      "slack_token",
      "slack_channel",
      "prompts",
      "rubric",
    }
    unknown = sorted(k for k in raw.keys() if k not in allowed)
    if unknown:
      raise _config_error(
        f"{context_label} contains unsupported TextSubmissionGrader setting(s): "
        f"{', '.join(unknown)}")

    prompts_raw = raw.get("prompts", {})
    if prompts_raw is None:
      prompts_raw = {}
    prompts = _require_mapping(prompts_raw, f"{context_label}.prompts")
    unknown_prompts = sorted(
      key for key in prompts.keys() if key not in DEFAULT_TEXT_PROMPT_KEYS)
    if unknown_prompts:
      raise _config_error(
        f"{context_label}.prompts contains unsupported key(s): "
        f"{', '.join(unknown_prompts)}")
    normalized_prompts: Dict[str, str] = {}
    for key, value in prompts.items():
      if not isinstance(value, str):
        raise _config_error(f"{context_label}.prompts.{key} must be a string")
      rendered = value.strip()
      if not rendered:
        raise _config_error(
          f"{context_label}.prompts.{key} cannot be empty when provided")
      normalized_prompts[key] = rendered

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
      rate_limit_retries=_require_non_negative_int(
        raw.get("rate_limit_retries"),
        f"{context_label}.rate_limit_retries",
        0),
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
      prompt_templates=normalized_prompts,
      rubric=TextRubricSettings.from_raw(raw.get("rubric"),
                                         f"{context_label}.rubric"),
    )

  def to_kwargs(self) -> Dict[str, Any]:
    return {
      "grade_after_lock_date": self.grade_after_lock_date,
      "prefer_anthropic": self.prefer_anthropic,
      "phase1_tier": self.phase1_tier,
      "phase2_tier": self.phase2_tier,
      "phase25_tier": self.phase25_tier,
      "rate_limit_retries": self.rate_limit_retries,
      "records_dir": self.records_dir,
      "record_retention": self.record_retention,
      "report_errors": self.report_errors,
      "slack_webhook": self.slack_webhook,
      "slack_token": self.slack_token,
      "slack_channel": self.slack_channel,
      "prompt_templates": dict(self.prompt_templates),
      "rubric": self.rubric.to_kwargs(),
    }


@dataclass
class ExternalToolGraderSettings:
  provider: str = "panopto"
  panopto_url: str = ""
  panopto_base_url: Optional[str] = None
  panopto_session_id: Optional[str] = None
  panopto_access_token: Optional[str] = None
  panopto_access_token_env: Optional[str] = "PANOPTO_ACCESS_TOKEN"
  panopto_client_id: Optional[str] = None
  panopto_client_secret: Optional[str] = None
  panopto_client_id_env: Optional[str] = "PANOPTO_CLIENT_ID"
  panopto_client_secret_env: Optional[str] = "PANOPTO_CLIENT_SECRET"
  panopto_refresh_token: Optional[str] = None
  panopto_refresh_token_env: Optional[str] = "PANOPTO_REFRESH_TOKEN"
  panopto_refresh_token_path: Optional[str] = "~/.autograder/panopto_refresh_token.json"
  panopto_token_url: Optional[str] = None
  panopto_scope: str = "api"
  watch_data_path_template: str = "/Panopto/api/v1/sessions/{session_id}/viewers"
  canvas_user_attribute: str = "email"
  external_user_attribute: str = "email"
  student_identifier_overrides: Dict[str, str] = field(default_factory=dict)
  record_identifier_paths: List[str] = field(default_factory=list)
  record_percent_paths: List[str] = field(default_factory=list)
  record_viewed_seconds_paths: List[str] = field(default_factory=list)
  record_duration_seconds_paths: List[str] = field(default_factory=list)
  missing_user_score: float = 0.0
  request_timeout_seconds: float = 30.0
  report_errors: bool = True
  slack_webhook: Optional[str] = None
  slack_token: Optional[str] = None
  slack_channel: Optional[str] = None

  @classmethod
  def from_raw(cls, value: Any, context_label: str) -> "ExternalToolGraderSettings":
    raw = _require_mapping(value, context_label)

    allowed = {
      "provider",
      "panopto_url",
      "panopto_base_url",
      "panopto_session_id",
      "panopto_access_token",
      "panopto_access_token_env",
      "panopto_client_id",
      "panopto_client_secret",
      "panopto_client_id_env",
      "panopto_client_secret_env",
      "panopto_refresh_token",
      "panopto_refresh_token_env",
      "panopto_refresh_token_path",
      "panopto_token_url",
      "panopto_scope",
      "watch_data_path_template",
      "canvas_user_attribute",
      "external_user_attribute",
      "student_identifier_overrides",
      "record_identifier_paths",
      "record_percent_paths",
      "record_viewed_seconds_paths",
      "record_duration_seconds_paths",
      "missing_user_score",
      "request_timeout_seconds",
      "report_errors",
      "slack_webhook",
      "slack_token",
      "slack_channel",
    }
    unknown = sorted(k for k in raw.keys() if k not in allowed)
    if unknown:
      raise _config_error(
        f"{context_label} contains unsupported external tool setting(s): "
        f"{', '.join(unknown)}")

    panopto_url = _require_optional_str(raw.get("panopto_url"),
                                        f"{context_label}.panopto_url")
    if panopto_url is None or not panopto_url.strip():
      raise _config_error(f"{context_label}.panopto_url is required")

    default_watch_data_path_template = "/Panopto/api/v1/sessions/{session_id}/viewers"
    watch_data_path_template = _require_optional_str(
      raw.get("watch_data_path_template", default_watch_data_path_template),
      f"{context_label}.watch_data_path_template")
    if watch_data_path_template is None or "{session_id}" not in watch_data_path_template:
      raise _config_error(
        f"{context_label}.watch_data_path_template must contain '{{session_id}}'")

    return cls(
      provider=_require_choice(raw.get("provider"),
                               f"{context_label}.provider",
                               allowed={"panopto"},
                               default="panopto"),
      panopto_url=panopto_url.strip(),
      panopto_base_url=_require_optional_str(raw.get("panopto_base_url"),
                                             f"{context_label}.panopto_base_url"),
      panopto_session_id=_require_optional_str(raw.get("panopto_session_id"),
                                               f"{context_label}.panopto_session_id"),
      panopto_access_token=_require_optional_str(raw.get("panopto_access_token"),
                                                 f"{context_label}.panopto_access_token"),
      panopto_access_token_env=_require_optional_str(
        raw.get("panopto_access_token_env", "PANOPTO_ACCESS_TOKEN"),
        f"{context_label}.panopto_access_token_env"),
      panopto_client_id=_require_optional_str(raw.get("panopto_client_id"),
                                              f"{context_label}.panopto_client_id"),
      panopto_client_secret=_require_optional_str(
        raw.get("panopto_client_secret"),
        f"{context_label}.panopto_client_secret"),
      panopto_client_id_env=_require_optional_str(
        raw.get("panopto_client_id_env", "PANOPTO_CLIENT_ID"),
        f"{context_label}.panopto_client_id_env"),
      panopto_client_secret_env=_require_optional_str(
        raw.get("panopto_client_secret_env", "PANOPTO_CLIENT_SECRET"),
        f"{context_label}.panopto_client_secret_env"),
      panopto_refresh_token=_require_optional_str(
        raw.get("panopto_refresh_token"),
        f"{context_label}.panopto_refresh_token"),
      panopto_refresh_token_env=_require_optional_str(
        raw.get("panopto_refresh_token_env", "PANOPTO_REFRESH_TOKEN"),
        f"{context_label}.panopto_refresh_token_env"),
      panopto_refresh_token_path=_require_optional_str(
        raw.get("panopto_refresh_token_path",
                "~/.autograder/panopto_refresh_token.json"),
        f"{context_label}.panopto_refresh_token_path"),
      panopto_token_url=_require_optional_str(raw.get("panopto_token_url"),
                                              f"{context_label}.panopto_token_url"),
      panopto_scope=str(raw.get("panopto_scope", "api")).strip() or "api",
      watch_data_path_template=watch_data_path_template,
      canvas_user_attribute=_require_choice(
        raw.get("canvas_user_attribute"),
        f"{context_label}.canvas_user_attribute",
        allowed=VALID_EXTERNAL_USER_ATTRIBUTES,
        default="email"),
      external_user_attribute=_require_choice(
        raw.get("external_user_attribute"),
        f"{context_label}.external_user_attribute",
        allowed=VALID_EXTERNAL_USER_ATTRIBUTES,
        default="email"),
      student_identifier_overrides=_require_string_keyed_mapping(
        raw.get("student_identifier_overrides", {}),
        f"{context_label}.student_identifier_overrides"),
      record_identifier_paths=_require_str_list(
        raw.get("record_identifier_paths", [
          "User.Email",
          "UserEmail",
          "Email",
          "email",
          "User.Login",
          "UserLogin",
          "Viewer.Email",
          "ViewerEmail",
          "Viewer.Login",
        ]),
        f"{context_label}.record_identifier_paths"),
      record_percent_paths=_require_str_list(
        raw.get("record_percent_paths", [
          "PercentComplete",
          "PercentCompleted",
          "PercentWatched",
          "percentComplete",
          "percentWatched",
        ]),
        f"{context_label}.record_percent_paths"),
      record_viewed_seconds_paths=_require_str_list(
        raw.get("record_viewed_seconds_paths", [
          "SecondsViewed",
          "ViewerSeconds",
          "TimeViewedSeconds",
          "secondsViewed",
        ]),
        f"{context_label}.record_viewed_seconds_paths"),
      record_duration_seconds_paths=_require_str_list(
        raw.get("record_duration_seconds_paths", [
          "DurationSeconds",
          "SessionDurationSeconds",
          "TotalSeconds",
          "durationSeconds",
        ]),
        f"{context_label}.record_duration_seconds_paths"),
      missing_user_score=_require_float_in_range(
        raw.get("missing_user_score"),
        f"{context_label}.missing_user_score",
        minimum=0.0,
        maximum=100.0,
        default=0.0),
      request_timeout_seconds=_require_non_negative_float(
        raw.get("request_timeout_seconds"),
        f"{context_label}.request_timeout_seconds",
        30.0),
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
      "provider": self.provider,
      "panopto_url": self.panopto_url,
      "panopto_base_url": self.panopto_base_url,
      "panopto_session_id": self.panopto_session_id,
      "panopto_access_token": self.panopto_access_token,
      "panopto_access_token_env": self.panopto_access_token_env,
      "panopto_client_id": self.panopto_client_id,
      "panopto_client_secret": self.panopto_client_secret,
      "panopto_client_id_env": self.panopto_client_id_env,
      "panopto_client_secret_env": self.panopto_client_secret_env,
      "panopto_refresh_token": self.panopto_refresh_token,
      "panopto_refresh_token_env": self.panopto_refresh_token_env,
      "panopto_refresh_token_path": self.panopto_refresh_token_path,
      "panopto_token_url": self.panopto_token_url,
      "panopto_scope": self.panopto_scope,
      "watch_data_path_template": self.watch_data_path_template,
      "canvas_user_attribute": self.canvas_user_attribute,
      "external_user_attribute": self.external_user_attribute,
      "student_identifier_overrides": dict(self.student_identifier_overrides),
      "record_identifier_paths": list(self.record_identifier_paths),
      "record_percent_paths": list(self.record_percent_paths),
      "record_viewed_seconds_paths": list(self.record_viewed_seconds_paths),
      "record_duration_seconds_paths": list(self.record_duration_seconds_paths),
      "missing_user_score": self.missing_user_score,
      "request_timeout_seconds": self.request_timeout_seconds,
      "report_errors": self.report_errors,
      "slack_webhook": self.slack_webhook,
      "slack_token": self.slack_token,
      "slack_channel": self.slack_channel,
    }
