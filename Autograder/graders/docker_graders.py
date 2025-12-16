"""
Docker-based grader implementations.

Contains configurable Docker graders that can run arbitrary grading scripts
in containerized environments.
"""
import os
import pathlib
import shutil
import tempfile
import shutil
import os
import subprocess
import uuid

import yaml
from collections import defaultdict
from typing import Tuple, Optional, List

from Autograder.registry import GraderRegistry
from Autograder.lms_interface.classes import Feedback
from Autograder.docker_utils import DockerClient, DockerContainer, DockerError, DockerContainerManager
import Autograder.exceptions
from Autograder.grader import FileBasedGrader

import logging

log = logging.getLogger(__name__)


class Grader__docker(FileBasedGrader):
  """
  Base class for Docker-based graders.

  Provides common Docker functionality like container management,
  file copying, and command execution using docker_utils.
  """

  def __init__(self, image=None, *args, **kwargs):
    super().__init__(*args, **kwargs)

    # Set up docker client
    try:
      self.docker_client = DockerClient()
    except DockerError as e:
      log.error(f"Failed to initialize Docker client: {e}")
      raise Autograder.exceptions.ConfigurationError(
        f"Docker client initialization failed: {e}") from e

    # Default to using ubuntu image
    self.base_name_name = image if image is not None else "ubuntu"
    self.image = None  # Only set this when we actually run grading to reduce how often we build
    self.container: Optional[DockerContainer] = None

  # Helper functions below here
  def build_docker_image(self, dockerfile_str: str):
    """
    Build a Docker image from dockerfile content.

    Args:
        dockerfile_str: Dockerfile as a single string

    Returns:
        Built Docker image
    """
    tag = f"grading:{self.__class__.__name__.lower()}"
    return self.docker_client.build_image(dockerfile_str, tag)

  def start_container(self, image=None) -> None:
    """Start a Docker container."""
    image_to_use = image if image is not None else self.image
    self.container = DockerContainer(self.docker_client,
                                     image_to_use,
                                     name_prefix="grader")
    self.container.start()

  def stop_container(self) -> None:
    """Stop the Docker container."""
    if self.container:
      self.container.stop()
      self.container = None

  def add_files_to_docker(self, files_to_copy: List[Tuple] = None) -> None:
    """
    Copy files to the Docker container.

    Args:
        files_to_copy: List of (file_object, target_directory) tuples
    """
    if files_to_copy and self.container:
      self.container.copy_files(files_to_copy)

  def execute_command_in_container(self,
                                   command="",
                                   container=None,
                                   workdir=None) -> Tuple[int, bytes, bytes]:
    """
    Execute a command in the Docker container.

    Args:
        command: Command to execute
        container: Container to use (defaults to self.container)
        workdir: Working directory for command

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    target_container = container if container is not None else self.container
    if not target_container:
      raise RuntimeError("No container available for command execution")

    return target_container.execute_command(command, workdir)

  def read_file_from_container(self, path_to_file: str) -> Optional[str]:
    """
    Read a file from the Docker container.

    Args:
        path_to_file: Path to file in container

    Returns:
        File contents as string, or None if not found
    """
    if not self.container:
      return None

    return self.container.read_file(path_to_file)

  def _get_slack_config(self) -> dict:
    """
    Get Slack configuration with support for override hierarchy.

    Configuration is checked in order (most specific to least specific):
    1. Assignment-level config (self.slack_webhook, self.slack_channel, self.report_errors)
    2. Course-level config via environment variables (SLACK_WEBHOOK_{COURSE}, etc.)
    3. Global config via environment variables (SLACK_BOT_TOKEN, SLACK_WEBHOOK, etc.)

    Returns:
        dict with keys: webhook, token, channel, enabled
    """
    import os

    # Assignment-level overrides
    webhook = getattr(self, 'slack_webhook', None)
    token = getattr(self, 'slack_token', None)
    channel = getattr(self, 'slack_channel', None)
    enabled = getattr(self, 'report_errors', True)  # Default to enabled

    # Course-level environment variables (if course_name is set)
    course_name = getattr(self, 'course_name',
                          '').upper().replace(' ', '_').replace('-', '_')
    if course_name:
      webhook = webhook or os.getenv(f'SLACK_WEBHOOK_{course_name}')
      token = token or os.getenv(f'SLACK_BOT_TOKEN_{course_name}')
      channel = channel or os.getenv(f'SLACK_CHANNEL_{course_name}')

      # Check for course-specific opt-out
      course_enabled = os.getenv(f'REPORT_ERRORS_{course_name}')
      if course_enabled is not None:
        enabled = course_enabled.lower() not in ('false', '0', 'no')

    # Global environment variables
    webhook = webhook or os.getenv('SLACK_WEBHOOK')
    token = token or os.getenv('SLACK_BOT_TOKEN')
    channel = channel or os.getenv('SLACK_CHANNEL')

    # Global opt-out
    global_enabled = os.getenv('REPORT_ERRORS')
    if global_enabled is not None:
      enabled = enabled and global_enabled.lower() not in ('false', '0', 'no')

    return {
      'webhook': webhook,
      'token': token,
      'channel': channel,
      'enabled': enabled
    }

  def _zip_student_files(self, submission) -> Optional[bytes]:
    """
    Create a zip archive of student submission files.

    Args:
        submission: Submission object with files attribute

    Returns:
        Zip file as bytes, or None if no files
    """
    import io
    import zipfile

    if not submission or not hasattr(submission,
                                     'files') or not submission.files:
      return None

    try:
      zip_buffer = io.BytesIO()
      with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_obj in submission.files:
          # Get file name
          filename = getattr(file_obj, 'name', 'unnamed_file')

          # Read file content
          file_obj.seek(0)
          content = file_obj.read()

          # Add to zip
          zip_file.writestr(filename, content)

          # Reset file pointer
          file_obj.seek(0)

      zip_buffer.seek(0)
      return zip_buffer.getvalue()
    except Exception as e:
      log.warning(f"Failed to zip student files: {e}")
      return None

  def _send_error_to_slack(self,
                           error_msg: str,
                           execution_results: Tuple,
                           submission=None) -> None:
    """
    Send error report to Slack with file attachments.

    Args:
        error_msg: Description of the error
        execution_results: Tuple of (rc, stdout, stderr) from execution
        submission: The submission object being graded (if available)
    """
    import io
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    slack_config = self._get_slack_config()

    if not slack_config['enabled']:
      log.debug("Error reporting is disabled")
      return

    # Need either webhook or (token + channel)
    webhook = slack_config['webhook']
    token = slack_config['token']
    channel = slack_config['channel']

    if not webhook and not (token and channel):
      log.debug(
        f"Slack not configured for error reporting (webhook={webhook}, token={bool(token)}, channel={channel})"
      )
      return

    rc, stdout, stderr = execution_results

    # Get student info
    student_name = "Unknown"
    if submission and hasattr(submission, 'student') and submission.student:
      student_name = submission.student.name

    # Build initial message
    assignment_name = getattr(self, 'assignment_name', 'Unknown Assignment')
    course_name = getattr(self, 'course_name', 'Unknown Course')

    message = (f":warning: *Grading Error Detected*\n"
               f"*Course:* {course_name}\n"
               f"*Assignment:* {assignment_name}\n"
               f"*Student:* {student_name}\n"
               f"*Error:* {error_msg}\n"
               f"*Return Code:* {rc}\n\n"
               f"Relevant files are attached below.")

    try:
      # Handle webhook case (simple message only, no files)
      if webhook:
        import requests
        response = requests.post(webhook, json={"text": message}, timeout=10)
        if response.status_code == 200:
          log.info("Slack error notification sent via webhook (no files)")
        else:
          log.warning(f"Slack webhook failed: {response.status_code}")
        return

      # Use Slack SDK for bot token
      client = WebClient(token=token)

      # Prepare files to upload
      files_to_upload = []

      # 1. Error details as text file
      error_details = (f"Error: {error_msg}\n"
                       f"Return Code: {rc}\n"
                       f"Student: {student_name}\n"
                       f"Assignment: {assignment_name}\n"
                       f"Course: {course_name}\n")
      files_to_upload.append(('error_details.txt', error_details.encode()))

      # 2. stdout
      if stdout:
        files_to_upload.append(
          ('stdout.txt',
           stdout if isinstance(stdout, bytes) else stdout.encode()))

      # 3. stderr
      if stderr:
        files_to_upload.append(
          ('stderr.txt',
           stderr if isinstance(stderr, bytes) else stderr.encode()))

      # 4. feedback.yaml (if exists)
      feedback_content = self.read_file_from_container("/tmp/feedback.yaml")
      if feedback_content:
        files_to_upload.append(('feedback.yaml', feedback_content.encode()))

      # 5. Student code as zip
      student_zip = self._zip_student_files(submission)
      if student_zip:
        files_to_upload.append(('student_code.zip', student_zip))

      # Upload all files together with the message (not threaded)
      # Note: Slack's files_upload_v2 doesn't support multiple files in one call,
      # so we upload them separately but they'll all appear as attachments
      for filename, content in files_to_upload:
        try:
          client.files_upload_v2(
            channel=channel,
            file=io.BytesIO(content),
            filename=filename,
            title=filename,
            initial_comment=message if filename == files_to_upload[0][0] else
            None  # Only show message with first file
          )
        except SlackApiError as e:
          log.warning(f"Failed to upload {filename}: {e.response['error']}")
        except Exception as e:
          log.warning(f"Failed to upload {filename}: {e}")

      log.info("Slack error report sent successfully with attachments")

    except SlackApiError as e:
      log.warning(f"Slack API error: {e.response['error']}")
    except Exception as e:
      log.warning(f"Failed to send Slack error report: {e}")

  def _report_grading_error(self,
                            error_msg: str,
                            execution_results: Tuple,
                            submission=None) -> None:
    """
    Hook for reporting grading errors (e.g., via Slack, email, etc.).

    Default implementation logs the error and sends to Slack if configured.
    Subclasses can override to add custom behavior or provide additional context.

    Args:
        error_msg: Description of the error
        execution_results: Tuple of (rc, stdout, stderr) from execution
        submission: The submission object being graded (if available)
    """
    log.error(f"Grading error hook called: {error_msg}")
    if submission:
      log.error(
        f"  Submission: {submission.student.name if hasattr(submission, 'student') else 'Unknown'}"
      )
    rc, stdout, stderr = execution_results
    log.error(f"  Return code: {rc}")
    log.error(f"  Stdout (first 500 chars): {stdout[:500] if stdout else ''}")
    log.error(f"  Stderr (first 500 chars): {stderr[:500] if stderr else ''}")

    # Send to Slack if configured
    self._send_error_to_slack(error_msg, execution_results, submission)

  def _get_image(self, *args, **kwargs):
    return "ubuntu"

  def __enter__(self):
    """Context manager entry - start container."""
    if self.image is None:
      log.debug("Building docker image")
      self.image = self._get_image()
    log.debug(f"Starting docker image {self.image} context")
    self.start_container()
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    """Context manager exit - stop container."""
    log.debug(f"Exiting docker image context")
    self.stop_container()
    if exc_type is not None:
      log.error(f"An exception occurred: {exc_val}")
    return False

  def grade_submission(self,
                       submission,
                       files_to_copy=None,
                       *args,
                       **kwargs) -> Feedback:
    """
    Overrides method to add files to docker and then relies on children to implement two other required files
    :param files_to_copy:
    :param args:
    :param kwargs:
    :return:
    """
    with self:
      if files_to_copy is not None:
        self.add_files_to_docker(files_to_copy)
      # input(f"Waiting on container {self.container}")
      return super().grade_submission(submission, *args, **kwargs)


@GraderRegistry.register("template-grader")
class Grader__template_grader(Grader__docker):
  """
  Template-based grader that automatically sets up a course template repository
  and runs scripts/grader.py with minimal configuration required.
  
  Automatically handles:
  - Default Python 3.11 environment
  - Cloning template repository (local or remote)
  - Installing uv and running uv sync
  - Running grader.py with assignment name
  """

  def __init__(
      self,
      assignment_name,
      course_name: str = "UnknownCourse",
      base_image_name: str = "python:3.11-slim",  # assume this is based on linux
      source_repo:
    str = "https://github.com/CSUMB-SCD-instructors/course-template",
      student_code_path: str = "",
      extra_installs=None,  # todo: these will be tough, do later
      extra_dockerfile_lines=None,
      file_paths=None,
      *args,
      **kwargs):

    if extra_installs is None:
      extra_installs = []
    if extra_dockerfile_lines is None:
      extra_dockerfile_lines = []
    if file_paths is None:
      file_paths = {}

    self.course_name = course_name
    self.assignment_name = assignment_name
    self.base_image_name = base_image_name
    self.source_repo = source_repo
    self.student_code_path = student_code_path
    self.extra_installs = extra_installs
    self.extra_dockerfile_lines = extra_dockerfile_lines
    self.file_paths = file_paths

    # Potential includes
    self.golden_repo = kwargs.get("golden_repo", None)
    self.files_from_golden = kwargs.get("files_from_golden", [])

    super().__init__(*args, **kwargs)

    # todo: these two can likely be removed if we go full template.
    self.working_dir = "/repo"
    self.grading_script = f"/repo/.venv/bin/python /repo/scripts/grader.py --PA {self.assignment_name}"

    return

  @staticmethod
  def _get_repo(repo_path: str, dest="repo", depth=None, deploy_key_path=None):

    dest = pathlib.Path(dest).expanduser().resolve()
    if dest.exists():
      raise FileExistsError(f"{dest} already exists")

    # If it's local, copy it from local
    if pathlib.Path(repo_path).expanduser().exists():
      shutil.copytree(pathlib.Path(repo_path).expanduser(), dest)
      return

    else:  # Get it from the remote location
      env = os.environ.copy()

      # If you need to use an SSH deploy key just for this command:
      # (works for git@host:org/repo.git or ssh://host/...)
      if deploy_key_path:
        ssh_cmd = f"ssh -i {deploy_key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        env["GIT_SSH_COMMAND"] = ssh_cmd

      cmd = ["git", "clone", repo_path, str(dest)]
      if depth:
        cmd[2:2] = ["--depth", str(depth)
                    ]  # insert after "clone" (optional shallow clone)

      subprocess.run(cmd, check=True, env=env)

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
        - path: subdirectory within assignment folder
        - name: target filename
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

            # Build target path
            subpath = target_config.get('path', '')
            target_name = target_config.get('name', file_identifier)

            target_directory = os.path.join(
              f"/repo/programming-assignments/{self.assignment_name}", subpath)

            # We need to provide a target file path, not just directory
            # The Docker copy will handle creating directories
            target_file_path = os.path.join(target_directory, target_name)

            files_to_copy.append((file_obj, target_file_path))
            matched_this_file = True
            log.debug(f"  Will copy to: {target_file_path}")
            break  # Only match first pattern

        except re.error as e:
          log.error(f"Invalid regex pattern '{pattern}': {e}")
          return [], f"Invalid regex pattern '{pattern}': {e}"

      if not matched_this_file:
        log.debug(f"  No pattern matched (will be skipped)")

    log.info(f"Matched {len(files_to_copy)} files using file_paths patterns")
    return files_to_copy, None

  def _get_image(self, **kwargs):
    # What we want to do is to create a docker image that has the repository in it and installs all the required dependencies.
    # In this case that means we need to get the repo from either locally or remotely and then run `uv sync` in the right directory

    with tempfile.TemporaryDirectory() as temp_build_dir:
      log.info(f"temp_build_dir: {temp_build_dir}")

      # Get the main repo
      self._get_repo(self.source_repo,
                     os.path.join(temp_build_dir, "repo"),
                     depth=1)

      # If we have a golden repo, let's use it to set the extra files
      if self.golden_repo:
        # Download the golden repo, and we'll delete it later
        self._get_repo(self.golden_repo, os.path.join(temp_build_dir,
                                                      "golden"))

        logging.debug(temp_build_dir)

        for f in self.files_from_golden:
          log.debug(f"Copying over golden file: {f}")
          shutil.copy(
            os.path.join(temp_build_dir, "golden", "programming-assignments",
                         self.assignment_name, f),
            os.path.join(temp_build_dir, "repo", "programming-assignments",
                         self.assignment_name, f),
          )

        # Remove the golden for now
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
        "COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/",
        "WORKDIR /repo",
        "RUN rm -rf .venv",
        "RUN rm -rf uv.lock .venv",
        # "RUN uv lock",
        "RUN uv sync",
        # "RUN chown -fR dockeruser /repo",
        # "USER dockeruser",
      ])

      # Next, we want to save our dockerfile
      with open(os.path.join(temp_build_dir, "Dockerfile"),
                "w") as dockerfile_fid:
        dockerfile_fid.write('\n'.join(dockerfile_lines) + "\n")
        
      # input(f"Waiting at {temp_build_dir}")

      image = self.docker_client.build_image_from_context(
        context_path=temp_build_dir,
        tag=
        f"template-grader:{self.course_name}-{self.assignment_name}-{uuid.uuid4().hex}",
        use_cached=True)
    return image

  def grade_submission(self, submission, *args, **kwargs) -> Feedback:
    # Determine which file organization strategy to use
    if self.file_paths:
      # Use new regex-based file matching
      log.info("Using file_paths regex matching for file organization")
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
      # Use legacy student_code_path behavior (backward compatibility)
      log.info("Using legacy student_code_path for file organization")
      submission_files = []
      for f in submission.files:
        # Copy all files to the working directory
        submission_files.append(
          (f,
           os.path.join(
             f"/repo/programming-assignments/{self.assignment_name}",
             self.student_code_path)))
      log.debug(f"submission.files: {submission.files}")
      log.debug(f"submission_files: {submission_files}")

    # Grade using parent class method
    return super().grade_submission(submission,
                                    files_to_copy=submission_files,
                                    *args,
                                    **kwargs)

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    rc, stdout, stderr = execution_results

    # Try to read the feedback.yaml file (note: different from results.yaml in parent)
    # Expected format:
    #   grade: <percentage 0-100+>  # Percentage score where 100 = full credit
    #   comments: <feedback text>
    #   logs: <optional execution logs>
    feedback_content = self.read_file_from_container("/tmp/feedback.yaml")
    log.debug(f"feedback_content: {feedback_content}")

    if feedback_content:
      try:
        feedback_data = yaml.safe_load(feedback_content)
        if isinstance(feedback_data, dict):
          # Grade should be a percentage (0-100+)
          grade = float(feedback_data.get('grade', 0.0))
          comments = feedback_data.get('comments', 'No comments provided')
          logs = feedback_data.get('logs', '')

          full_feedback = comments
          if logs and logs.strip():
            full_feedback += f"\n\n--- Execution Logs ---\n{logs}"

          return Feedback(percentage_score=grade, comments=full_feedback)
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

  def execute_grading(self, *args, **kwargs) -> Tuple[int, str, str]:
    # Execute the grading script
    rc, stdout, stderr = self.execute_command_in_container(
      command=self.grading_script, workdir=self.working_dir)
    return rc, stdout.decode(), stderr.decode()


@GraderRegistry.register("Step-by-step")
class Grader_stepbystep(Grader__docker):
  """
  Step-by-step grader that compares student commands against golden commands.

  Executes commands in parallel containers and compares outputs,
  with rollback functionality when outputs don't match.
  """

  def __init__(self, rubric_file, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.rubric = self.parse_rubric(rubric_file)
    self.container_manager = DockerContainerManager(self.docker_client)

  def parse_rubric(self, rubric_file):
    with open(rubric_file) as fid:
      rubric = yaml.safe_load(fid)
    if not isinstance(rubric["steps"], list):
      rubric["steps"] = rubric["steps"].split('\n')
    return rubric

  def parse_student_file(self, student_file):
    with open(student_file) as fid:
      return [l.strip() for l in fid.readlines()]

  def rollback(self):
    """Rollback student container to match golden container state."""
    # Stop student container
    student = self.container_manager.get_container("student")
    student.stop()

    # Create image from golden container
    golden = self.container_manager.get_container("golden")
    rollback_image = golden.commit(repository="rollback", tag="latest")

    # Create new student container from rollback image
    self.container_manager.create_container("student",
                                            rollback_image,
                                            start_immediately=True)

  def start(self, image):
    """Start both golden and student containers."""
    self.container_manager.create_container("golden",
                                            image,
                                            start_immediately=True)
    self.container_manager.create_container("student",
                                            image,
                                            start_immediately=True)

  def stop_container(self):
    """Stop all containers."""
    self.container_manager.stop_all()

  def execute_grading(self,
                      golden_lines=[],
                      student_lines=[],
                      rollback=True,
                      *args,
                      **kwargs):
    golden_results = defaultdict(list)
    student_results = defaultdict(list)

    def add_results(results_dict, rc, stdout, stderr):
      results_dict["rc"].append(rc)
      results_dict["stdout"].append(stdout)
      results_dict["stderr"].append(stderr)

    for i, (golden, student) in enumerate(zip(golden_lines, student_lines)):
      log.debug(f"commands: '{golden}' <-> '{student}'")

      golden_container = self.container_manager.get_container("golden")
      student_container = self.container_manager.get_container("student")

      rc_g, stdout_g, stderr_g = golden_container.execute_command(golden)
      rc_s, stdout_s, stderr_s = student_container.execute_command(student)

      add_results(golden_results, rc_g, stdout_g, stderr_g)
      add_results(student_results, rc_s, stdout_s, stderr_s)

      if (not self.outputs_match(stdout_g, stdout_s, stderr_g, stderr_s, rc_g,
                                 rc_s)) and rollback:
        # Bring the student container up to date with our container
        self.rollback()

    return golden_results, student_results

  @staticmethod
  def outputs_match(stdout_g, stdout_s, stderr_g, stderr_s, rc_g,
                    rc_s) -> bool:
    if stdout_g != stdout_s:
      return False
    if stderr_g != stderr_s:
      return False
    if rc_g != rc_s:
      return False
    return True

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    log.debug(f"execution_results: {execution_results}")
    golden_results, student_results = execution_results
    num_lines = len(golden_results["stdout"])
    num_matches = 0
    for i in range(num_lines):
      if not self.outputs_match(
          golden_results["stdout"][i], student_results["stdout"][i],
          golden_results["stderr"][i], student_results["stderr"][i],
          golden_results["rc"][i], student_results["rc"][i]):
        continue
      num_matches += 1

    return Feedback(
      percentage_score=(100.0 * num_matches / len(golden_results["stdout"])),
      comments=f"Matched {num_matches} out of {len(golden_results['stdout'])}")

  def grade_assignment(self, input_files: List[str], *args,
                       **kwargs) -> Feedback:

    golden_lines = self.rubric["steps"]
    student_lines = self.parse_student_file(input_files[0])

    # Start containers
    self.start(self.image)

    try:
      results = self.execute_grading(golden_lines=golden_lines,
                                     student_lines=student_lines,
                                     *args,
                                     **kwargs)
      feedback = self.score_grading(results, *args, **kwargs)
    finally:
      # Clean up containers
      self.stop_container()

    log.debug(f"final results: {feedback}")
    return feedback
