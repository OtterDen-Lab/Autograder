"""Template grading workflow with pluggable execution and image preparation."""
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
import logging
from typing import Any, Dict, List, Optional, Tuple

import yaml
from lms_interface.classes import Feedback
from Autograder import config_models
import Autograder.exceptions
from Autograder.grader import FileBasedGrader
from Autograder.grading_reports import GradingErrorReporter
from Autograder.registry import GraderRegistry
from Autograder.runners import ExecutionInput, ExecutionRequest, ExecutionResult

log = logging.getLogger(__name__)


@GraderRegistry.register("template-grader")
class TemplateGrader(GradingErrorReporter, FileBasedGrader):
  """
  Template-based grader that automatically sets up a course template repository
  and runs scripts/grader.py with minimal configuration required.

  Automatically handles:
  - Default Python 3.11 environment
  - Cloning template repository (local or remote)
  - Installing uv and running uv sync
  - Running grader.py with assignment name
  """
  COMPATIBLE_KINDS = {"ProgrammingAssignment"}

  @classmethod
  def normalize_settings(cls, settings: Dict[str, Any],
                         context_label: str) -> Dict[str, Any]:
    """Validate and normalize template grader settings."""
    return config_models._normalize_template_grader_settings(
      settings, context_label)

  def __init__(
      self,
      assignment_name,
      course_name: str = "UnknownCourse",
      base_image_name: str = "python:3.11-slim",  # assume this is based on linux
      source_repo:
    str = "https://github.com/CSUMB-SCD-instructors/course-template",
      branch: str = "main",
      additional_repos=None,
      container_repo_path: str = "/repo/programming-assignments",
      student_code_path: str = "",
      extra_installs=None,  # todo: these will be tough, do later
      extra_dockerfile_lines=None,
      file_paths=None,
      grading_script: str | None = None,
      grading_args=None,
      grading_workdir: str | None = None,
      upload_error_artifacts: bool = False,
      runner=None,
      image_builder=None,
      *args,
      **kwargs):

    if extra_installs is None:
      extra_installs = []
    if extra_dockerfile_lines is None:
      extra_dockerfile_lines = []
    if file_paths is None:
      file_paths = {}

    self.course_name = course_name
    self.course_id = kwargs.get("course_id")
    self.assignment_name = assignment_name
    self.base_image_name = base_image_name
    self.source_repo = source_repo
    self.branch = branch
    self.additional_repos = self._normalize_additional_repos(additional_repos)
    self.container_repo_path = self._normalize_container_repo_path(
      container_repo_path)
    self.student_code_path = student_code_path
    self.extra_installs = extra_installs
    self.extra_dockerfile_lines = extra_dockerfile_lines
    self.file_paths = file_paths
    self.grading_workdir = grading_workdir
    self.grading_script_override = grading_script
    self.grading_args = list(grading_args or [])
    # Potential includes
    self.golden_repo = kwargs.get("golden_repo", None)
    self.files_from_golden = kwargs.get("files_from_golden", [])

    super().__init__(*args, **kwargs)
    from Autograder.runners.docker import DockerRunner, DockerImageBuilder
    self.runner = runner if runner is not None else DockerRunner()
    self.image_builder = image_builder if image_builder is not None else DockerImageBuilder()
    self.image = None
    # Keep YAML resource options compatible while execution backends evolve.
    resource_keys = ("memory_limit", "nano_cpus", "pids_limit", "read_only_root_fs",
                     "seccomp_profile", "mounts", "gpu_count", "network_disabled", "shm_size")
    self.execution_options = {key: kwargs[key] for key in resource_keys
                              if kwargs.get(key) is not None}
    self.command_timeout_seconds = kwargs.get("command_timeout_seconds", 60)
    env_artifacts = os.getenv("AUTOGRADER_UPLOAD_ERROR_ARTIFACTS")
    artifacts = env_artifacts if env_artifacts is not None else upload_error_artifacts
    self.upload_error_artifacts = (artifacts.strip().lower() in {"1", "true", "yes", "on"}
                                   if isinstance(artifacts, str) else bool(artifacts))

    # todo: these two can likely be removed if we go full template.
    default_workdir = "/repo"
    self.working_dir = (
      self.grading_workdir.strip()
      if isinstance(self.grading_workdir, str) and self.grading_workdir.strip()
      else default_workdir)
    default_script = self._build_default_grading_script(
      assignment_name=self.assignment_name, grading_args=self.grading_args)
    self.grading_script = (
      self.grading_script_override.strip()
      if isinstance(self.grading_script_override, str)
      and self.grading_script_override.strip() else default_script)

    return

  @staticmethod
  def _build_default_grading_script(assignment_name: Any,
                                    grading_args: Optional[List[str]] = None
                                    ) -> str:
    pa_arg = shlex.quote(str(assignment_name))
    normalized_args: List[str] = []
    for arg in grading_args or []:
      normalized_args.append(shlex.quote(str(arg)))

    extra_args = ""
    if normalized_args:
      extra_args = f" {' '.join(normalized_args)}"

    grader_invocation = f"/repo/scripts/grader.py --PA {pa_arg}{extra_args}"
    return (
      "if [ -x /repo/.venv/bin/python ]; then "
      f"/repo/.venv/bin/python {grader_invocation}; "
      "elif command -v python3 >/dev/null 2>&1; then "
      f"python3 {grader_invocation}; "
      "else "
      f"python {grader_invocation}; "
      "fi"
    )

  @staticmethod
  def _normalize_container_repo_path(container_repo_path: str) -> str:
    if not isinstance(container_repo_path, str):
      raise ValueError(
        "container_repo_path must be a string (example: '/repo/programming-assignments')."
      )

    normalized = os.path.normpath(container_repo_path.strip())
    if not normalized.startswith("/"):
      raise ValueError(
        "container_repo_path must be an absolute path under /repo "
        "(example: '/repo/programming-assignments').")
    if normalized != "/repo" and not normalized.startswith("/repo/"):
      raise ValueError(
        f"container_repo_path must be within /repo, got '{container_repo_path}'.")
    return normalized

  def _normalize_additional_repos(self, additional_repos) -> List[dict]:
    if additional_repos is None:
      return []
    if not isinstance(additional_repos, list):
      raise ValueError(
        "additional_repos must be a list of mappings with keys: "
        "source_repo, container_path, optional depth.")

    normalized_repos = []
    for i, repo_config in enumerate(additional_repos):
      if not isinstance(repo_config, dict):
        raise ValueError(
          f"additional_repos[{i}] must be a mapping. "
          "Expected keys: source_repo, container_path, optional depth.")
      unknown_keys = sorted(
        k for k in repo_config.keys() if k not in {"source_repo", "container_path", "depth"})
      if unknown_keys:
        raise ValueError(
          f"additional_repos[{i}] has unsupported key(s): {', '.join(unknown_keys)}")

      source_repo = repo_config.get("source_repo")
      if not isinstance(source_repo, str) or not source_repo.strip():
        raise ValueError(
          f"additional_repos[{i}].source_repo is required and must be a non-empty string."
        )

      container_path = self._normalize_container_repo_path(
        repo_config.get("container_path"))
      if container_path == "/repo":
        raise ValueError(
          "additional_repos cannot target /repo (reserved for source_repo). "
          "Use a subpath such as /repo/tools.")

      depth = repo_config.get("depth", 1)
      if depth is not None and (not isinstance(depth, int) or depth <= 0):
        raise ValueError(
          f"additional_repos[{i}].depth must be an integer >= 1 when provided.")

      normalized_repos.append({
        "source_repo": source_repo,
        "container_path": container_path,
        "depth": depth,
      })

    sorted_paths = sorted(repo["container_path"] for repo in normalized_repos)
    for i, path_i in enumerate(sorted_paths):
      for path_j in sorted_paths[i + 1:]:
        if path_j.startswith(f"{path_i}/") or path_i.startswith(f"{path_j}/"):
          raise ValueError(
            "additional_repos contain overlapping container_path values: "
            f"'{path_i}' and '{path_j}'. Choose disjoint mount targets.")

    return normalized_repos

  def _repo_mounts(self) -> List[dict]:
    mounts = [{
      "container_path": "/repo",
      "context_dir": "repo",
    }]
    for i, repo in enumerate(self.additional_repos):
      mounts.append({
        "container_path": repo["container_path"],
        "context_dir": f"repo_extra_{i}",
      })
    return mounts

  def _container_assignment_root(self) -> str:
    return os.path.join(self.container_repo_path, self.assignment_name)

  @staticmethod
  def _sanitize_image_tag_component(value: Any) -> str:
    raw = str(value).strip().lower()
    normalized = re.sub(r"[^a-z0-9_.-]+", "-", raw)
    normalized = normalized.strip("._-")
    return normalized or "autograder"

  def _build_context_image_tag(self) -> str:
    course = self._sanitize_image_tag_component(self.course_name)
    assignment = self._sanitize_image_tag_component(self.assignment_name)
    return f"template-grader:{course}-{assignment}-{uuid.uuid4().hex}"

  @staticmethod
  def _uv_bootstrap_command() -> str:
    # Some course repos do not use uv projects; skip uv setup in that case.
    # If uv sync fails, retry once after installing common build deps so native
    # wheels (e.g., psycopg2) can compile on slim images.
    return (
      "if [ -f pyproject.toml ]; then "
      "uv sync || ("
      "if command -v apt-get >/dev/null 2>&1; then "
      "apt-get update && "
      "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
      "build-essential gcc libpq-dev && "
      "rm -rf /var/lib/apt/lists/*; "
      "elif command -v apk >/dev/null 2>&1; then "
      "apk add --no-cache build-base postgresql-dev; "
      "fi; "
      "uv sync"
      "); "
      "else "
      "echo 'No pyproject.toml found in /repo; skipping uv sync'; "
      "fi"
    )

  @staticmethod
  def _resolve_local_path_for_container_path(temp_build_dir: str,
                                             container_path: str,
                                             repo_mounts: List[dict]) -> str:
    normalized_target = os.path.normpath(container_path)
    for mount in sorted(repo_mounts,
                        key=lambda entry: len(entry["container_path"]),
                        reverse=True):
      mount_path = mount["container_path"]
      if (normalized_target == mount_path
          or normalized_target.startswith(f"{mount_path}/")):
        relative = os.path.relpath(normalized_target, mount_path)
        base = os.path.join(temp_build_dir, mount["context_dir"])
        return base if relative == "." else os.path.join(base, relative)
    raise ValueError(
      f"container path '{container_path}' is not mounted by any configured repository. "
      f"Configured mounts: {', '.join(sorted(m['container_path'] for m in repo_mounts))}"
    )

  @staticmethod
  def _is_remote_git_source(repo_path: str) -> bool:
    """Return whether *repo_path* is a Git remote rather than a local path."""
    repo_path = str(repo_path)
    return bool(re.match(
      r"^(?:(?:https?|ssh|git)://|[^/\s:@]+@[^/\s:]+:.+)", repo_path))

  @staticmethod
  def _get_repo(repo_path: str,
                dest="repo",
                depth=None,
                deploy_key_path=None,
                branch=None):

    dest = pathlib.Path(dest).expanduser().resolve()
    if dest.exists():
      raise FileExistsError(
        f"Destination path '{dest}' already exists while preparing repository "
        f"source '{repo_path}'. Remove the existing path or choose a different target."
      )

    # Only inspect filesystem paths.  Calling Path.exists() for an HTTPS URL
    # turns it into a relative path (for example, ``https:/github.com/...``),
    # which can fail with PermissionError before git has a chance to clone it.
    if not TemplateGrader._is_remote_git_source(repo_path):
      local_repo_path = pathlib.Path(repo_path).expanduser()
      if local_repo_path.exists():
        shutil.copytree(local_repo_path, dest)
        return

    # Get it from the remote location.
    env = os.environ.copy()

    # If you need to use an SSH deploy key just for this command:
    # (works for git@host:org/repo.git or ssh://host/...)
    if deploy_key_path:
      ssh_cmd = f"ssh -i {deploy_key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
      env["GIT_SSH_COMMAND"] = ssh_cmd

    cmd = ["git", "clone"]
    if branch:
      cmd.extend(["--branch", branch])
    if depth:
      cmd.extend(["--depth", str(depth)])
    cmd.extend([repo_path, str(dest)])

    run_kwargs = {"check": True, "env": env}
    if not log.isEnabledFor(logging.DEBUG):
      run_kwargs.update({
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "text": True,
      })

    try:
      subprocess.run(cmd, **run_kwargs)
    except subprocess.CalledProcessError as e:
      stderr = (getattr(e, "stderr", None) or "").strip()
      if stderr:
        raise RuntimeError(
          f"git clone failed for '{repo_path}': {stderr}. "
          "Check repository URL/access permissions and network connectivity.") from e
      raise RuntimeError(
        f"git clone failed for '{repo_path}'. "
        "Check repository URL/access permissions and network connectivity.") from e

  def _match_files_to_paths(self,
                            submission) -> Tuple[List[Tuple], Optional[str]]:
    """
    Match submission files to target paths based on regex patterns in file_paths config.

    Args:
        submission: Submission object with files attribute

    Returns:
        Tuple of (files_to_copy, error_message)
        - files_to_copy: List of (file_object, target_path) tuples
        - error_message: Error string if duplicate matches found, None otherwise

    Uses self.file_paths dict where keys are regex patterns and values are dicts with:
        - path: when name is provided, a subdirectory within assignment folder;
                when name is omitted, a full relative target file path.
        - name: optional target filename override
    """
    import re

    files_to_copy = []
    matched_patterns = {}  # Track which patterns have been matched

    # Iterate through submission files
    for file_obj in submission.files:
      # Get the file's name (could include path if uploaded in folder structure)
      file_identifier = getattr(file_obj, 'name', 'unnamed_file')

      log.debug(f"Processing file: {file_identifier}")

      # Try to match against each pattern
      matched_this_file = False
      for pattern, target_config in self.file_paths.items():
        try:
          if re.match(pattern, file_identifier):
            log.debug(f"  Matched pattern: {pattern}")

            # Check if this pattern was already matched by another file
            if pattern in matched_patterns:
              # Multiple files match the same pattern - return error
              previous_file = matched_patterns[pattern]
              error_msg = (
                f"Multiple files match pattern '{pattern}': "
                f"'{previous_file}' and '{file_identifier}'. "
                f"Please ensure file names are unique and match exactly one pattern."
              )
              log.error(error_msg)
              return [], error_msg

            # Record this match
            matched_patterns[pattern] = file_identifier

            # Build target path. Keep backward compatibility:
            # - with name: path is treated as destination directory
            # - without name: path is treated as full destination file path
            configured_path = target_config.get('path', '')
            configured_name = target_config.get('name')
            assignment_root = self._container_assignment_root()

            if configured_name is not None:
              target_directory = os.path.join(assignment_root, configured_path)
              target_file_path = os.path.join(target_directory, configured_name)
            else:
              if not configured_path:
                error_msg = (
                  f"Pattern '{pattern}' has an empty path and no name. "
                  "Provide name, or set path to a full relative target file path."
                )
                log.error(error_msg)
                return [], error_msg
              target_file_path = os.path.join(assignment_root, configured_path)

            files_to_copy.append((file_obj, target_file_path))
            matched_this_file = True
            log.debug(f"  Will copy to: {target_file_path}")
            break  # Only match first pattern

        except re.error as e:
          log.error(f"Invalid regex pattern '{pattern}': {e}")
          return [], f"Invalid regex pattern '{pattern}': {e}"

      if not matched_this_file:
        log.debug(f"  No pattern matched (will be skipped)")

    log.debug(f"Matched {len(files_to_copy)} files using file_paths patterns")
    return files_to_copy, None

  def _get_image(self, **kwargs):
    # What we want to do is to create a docker image that has the repository in it and installs all the required dependencies.
    # In this case that means we need to get the repo from either locally or remotely and then run `uv sync` in the right directory

    with tempfile.TemporaryDirectory() as temp_build_dir:
      log.debug(f"temp_build_dir: {temp_build_dir}")
      repo_mounts = self._repo_mounts()

      # Get the main repo
      self._get_repo(self.source_repo,
                     os.path.join(temp_build_dir, "repo"),
                     depth=1,
                     branch=self.branch)
      for mount, repo in zip(repo_mounts[1:], self.additional_repos):
        self._get_repo(repo["source_repo"],
                       os.path.join(temp_build_dir, mount["context_dir"]),
                       depth=repo.get("depth"))

      # Prepare the golden repository before resolving the immutable image tag.
      if self.golden_repo:
        self._get_repo(self.golden_repo, os.path.join(temp_build_dir, "golden"))

      def commit(path):
        return subprocess.check_output(
          ["git", "-C", path, "rev-parse", "--short=12", "HEAD"],
          text=True, stderr=subprocess.PIPE).strip()

      source_commit = commit(os.path.join(temp_build_dir, "repo"))
      golden_commit = (commit(os.path.join(temp_build_dir, "golden"))
                       if self.golden_repo else None)
      if self.course_id is None:
        raise Autograder.exceptions.ConfigurationError(
          "A course ID is required for registry image caching")
      course = self._sanitize_image_tag_component(self.course_name)
      course_id = self._sanitize_image_tag_component(self.course_id)
      tag = f"docker-registry.p.zxqw.dev/{course}-{course_id}:{source_commit}"
      if golden_commit:
        tag += f"-{golden_commit}"

      image = self.image_builder.find(tag)
      if image is not None:
        log.info("Using cached grading image %s", tag)
        return image

      # Copy the configured files for every assignment into the shared image.
      if self.golden_repo and self.files_from_golden:
        golden_mounts = [{"container_path": "/repo", "context_dir": "golden"}]
        golden_root = self._resolve_local_path_for_container_path(
          temp_build_dir, self.container_repo_path, golden_mounts)
        target_root = self._resolve_local_path_for_container_path(
          temp_build_dir, self.container_repo_path, repo_mounts)
        for assignment in os.listdir(golden_root):
          for filename in self.files_from_golden:
            source = os.path.join(golden_root, assignment, filename)
            if not os.path.isfile(source):
              continue
            target = os.path.join(target_root, assignment, filename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy(source, target)
      if self.golden_repo:
        shutil.rmtree(os.path.join(temp_build_dir, "golden"))

      # Set up dockerfile
      dockerfile_lines = [
        f"FROM {self.base_image_name}",
        "USER root",
      ]

      # Add any extra Dockerfile lines after FROM but before copying repo
      # This allows for apt installs, user setup, etc.
      if self.extra_dockerfile_lines:
        # Handle both string (single line) and list (multiple lines)
        if isinstance(self.extra_dockerfile_lines, str):
          dockerfile_lines.append(self.extra_dockerfile_lines)
        else:
          dockerfile_lines.extend(self.extra_dockerfile_lines)

      # Continue with the standard setup
      dockerfile_lines.extend([
        "",  # Empty line for readability
        "COPY repo /repo",
      ])
      for mount in repo_mounts[1:]:
        dockerfile_lines.append(
          f"COPY {mount['context_dir']} {mount['container_path']}")
      dockerfile_lines.extend([
        "COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/",
        "WORKDIR /repo",
        "RUN rm -rf .venv",
        "RUN rm -rf uv.lock .venv",
        # "RUN uv lock",
      ])

      # Optional extra install commands from config.
      for install_cmd in self.extra_installs:
        if not isinstance(install_cmd, str):
          continue
        command = install_cmd.strip()
        if not command:
          continue
        if command.upper().startswith("RUN "):
          dockerfile_lines.append(command)
        else:
          dockerfile_lines.append(f"RUN {command}")

      dockerfile_lines.extend([
        f"RUN {self._uv_bootstrap_command()}",
        # "RUN chown -fR dockeruser /repo",
        # "USER dockeruser",
      ])

      # Next, we want to save our dockerfile
      with open(os.path.join(temp_build_dir, "Dockerfile"),
                "w") as dockerfile_fid:
        dockerfile_fid.write('\n'.join(dockerfile_lines) + "\n")
      
      # input(f"Waiting at {temp_build_dir}")

      image = self.image_builder.build(context_path=temp_build_dir, tag=tag)
    return image

  def grade_submission(self, submission, *args, **kwargs) -> Feedback:
    # Determine which file organization strategy to use
    if self.file_paths:
      # Use new regex-based file matching
      log.debug("Using file_paths regex matching for file organization")
      submission_files, error_msg = self._match_files_to_paths(submission)

      if error_msg:
        # Return error feedback immediately
        log.error(f"File matching error: {error_msg}")
        return Feedback(percentage_score=0.0,
                        comments=f"File naming error: {error_msg}")

      log.debug(
        f"Matched files: {[(f[0].name if hasattr(f[0], 'name') else 'unknown', f[1]) for f in submission_files]}"
      )
    else:
      # Use student_code_path file organization when regex mapping is not configured.
      log.debug("Using student_code_path for file organization")
      assignment_root = self._container_assignment_root()
      student_code_path = self.student_code_path or ""
      normalized_student_code_path = student_code_path.strip()
      looks_like_file_target = (
        bool(normalized_student_code_path)
        and not normalized_student_code_path.endswith("/")
        and bool(
          os.path.splitext(
            os.path.basename(normalized_student_code_path))[1]))

      if looks_like_file_target:
        if len(submission.files) != 1:
          error_msg = (
            "student_code_path appears to be a file target "
            f"('{self.student_code_path}') but this submission has "
            f"{len(submission.files)} files. Use a directory-style "
            "student_code_path or configure file_paths.")
          log.error(error_msg)
          return Feedback(percentage_score=0.0,
                          comments=f"Configuration error: {error_msg}")
        submission_files = [(submission.files[0],
                             os.path.join(assignment_root,
                                          normalized_student_code_path))]
      else:
        submission_files = []
        for f in submission.files:
          # Copy each file into the target directory using its own filename.
          # This avoids accidental overwrite when multiple files are submitted.
          original_name = os.path.basename(
            getattr(f, "name", "submission_file"))
          submission_files.append(
            (f,
             os.path.join(assignment_root, normalized_student_code_path,
                          original_name)))
      log.debug(f"submission.files: {submission.files}")
      log.debug(f"submission_files: {submission_files}")

    # Execute the same template workflow using the configured runner.
    return self._run_submission(submission, files_to_copy=submission_files,
                                *args, **kwargs)

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    rc, stdout, stderr = execution_results

    # Interpret the feedback collected by the runner after execution.
    # Expected format:
    #   grade: <percentage 0-100+>  # Percentage score where 100 = full credit
    #   comments: <feedback text>
    #   logs: <optional execution logs>
    #   rubric: <optional Canvas rubric assessment payload>
    feedback_content = execution_results.files.get("/tmp/feedback.yaml")
    log.debug(f"feedback_content: {feedback_content}")

    if feedback_content:
      try:
        feedback_data = yaml.safe_load(feedback_content)
        if isinstance(feedback_data, dict):
          # Grade should be a percentage (0-100+)
          grade = float(feedback_data.get('grade', 0.0))
          comments = feedback_data.get('comments', 'No comments provided')
          logs = feedback_data.get('logs', '')
          rubric_assessment = feedback_data.get("rubric")

          full_feedback = comments
          if logs and logs.strip():
            full_feedback += f"\n\n--- Execution Logs ---\n{logs}"

          feedback = Feedback(percentage_score=grade, comments=full_feedback)
          if isinstance(rubric_assessment, dict):
            setattr(feedback, "rubric_assessment", rubric_assessment)
          elif rubric_assessment is not None:
            log.warning(
              "Ignoring feedback.yaml rubric because it is not a mapping")
          return feedback
      except Exception as e:
        error_msg = f"Failed to parse feedback YAML: {e}"
        log.error(error_msg)
        self._report_grading_error(error_msg, execution_results,
                                   kwargs.get('submission'))
        return Feedback(
          percentage_score=0.0,
          comments=
          "Error during grading. If this persists, please let your professor know."
        )

    # Fallback: feedback.yaml not found or empty
    error_msg = f"Feedback file not found or empty. RC: {rc}, stdout length: {len(stdout)}, stderr length: {len(stderr)}"
    log.error(error_msg)
    self._report_grading_error(error_msg, execution_results,
                               kwargs.get('submission'))
    return Feedback(
      percentage_score=0.0,
      comments=
      "Error during grading. If this persists, please let your professor know."
    )

  output_paths = ("/tmp/feedback.yaml",)

  def __enter__(self):
    if self.image is None:
      self.image = self._get_image()
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self.cleanup()
    return False

  def cleanup(self):
    if self.image is not None:
      try:
        self.image_builder.release(self.image)
      finally:
        self.image = None

  def _run_submission(self, submission, files_to_copy=None, *args, **kwargs):
    return super().grade_submission(submission, *args,
                                    files_to_copy=files_to_copy, **kwargs)

  def _execution_files(self, files_to_copy):
    return files_to_copy

  def _execution_command(self):
    return self.grading_script, self.working_dir

  def execute_grading(self, *args, **kwargs) -> ExecutionResult:
    if self.image is None:
      self.image = self._get_image()
    inputs = []
    for file_obj, destination in self._execution_files(kwargs.get("files_to_copy") or []):
      position = file_obj.tell()
      try:
        file_obj.seek(0)
        inputs.append(ExecutionInput(destination, file_obj.read()))
      finally:
        file_obj.seek(position)
    command, workdir = self._execution_command()
    request = ExecutionRequest(image=self.image, command=command, workdir=workdir,
                               inputs=tuple(inputs), output_paths=self.output_paths,
                               timeout_seconds=self.command_timeout_seconds,
                               options=dict(self.execution_options))
    return self.runner.run(request)
