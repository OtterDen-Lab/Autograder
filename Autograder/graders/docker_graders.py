"""
Docker-based grader implementations.

Contains configurable Docker graders that can run arbitrary grading scripts
in containerized environments.
"""
import contextlib
import os

from typing import Tuple, Optional, List

from lms_interface.classes import Feedback
from Autograder.docker_utils import DockerClient, DockerContainer
import Autograder.exceptions
from Autograder.grader import FileBasedGrader
from Autograder.grading_reports import GradingErrorReporter

import logging

log = logging.getLogger(__name__)


def _parse_bool(value, default=False):
  if value is None:
    return default
  if isinstance(value, bool):
    return value
  if isinstance(value, str):
    return value.strip().lower() in {"1", "true", "yes", "on"}
  return bool(value)


class DockerGrader(GradingErrorReporter, FileBasedGrader):
  """
  Base class for Docker-based graders.

  Provides common Docker functionality like container management,
  file copying, and command execution using docker_utils.
  """

  def __init__(self,
               image=None,
               memory_limit=None,
               nano_cpus=None,
               pids_limit=None,
               read_only_root_fs=None,
               seccomp_profile=None,
               mounts=None,
               gpu_count=0,
               network_disabled=False,
               shm_size=None,
               command_timeout_seconds=60,
               *args,
               **kwargs):
    super().__init__(*args, **kwargs)

    env_upload_artifacts = os.getenv("AUTOGRADER_UPLOAD_ERROR_ARTIFACTS")
    upload_error_artifacts = kwargs.get("upload_error_artifacts", False)
    if env_upload_artifacts is not None:
      upload_error_artifacts = _parse_bool(env_upload_artifacts, False)
    self.upload_error_artifacts = _parse_bool(upload_error_artifacts, False)

    # Set up docker client
    try:
      self.docker_client = DockerClient()
    except Autograder.exceptions.DockerError as e:
      log.error(f"Failed to initialize Docker client: {e}")
      raise Autograder.exceptions.ConfigurationError(
        f"Docker client initialization failed: {e}") from e

    # Default to using ubuntu image
    self.base_name_name = image if image is not None else "ubuntu"
    self.image = None  # Only set this when we actually run grading to reduce how often we build
    self.container: Optional[DockerContainer] = None
    self.container_options = {
      key: value for key, value in {
        "memory_limit": memory_limit,
        "nano_cpus": nano_cpus,
        "pids_limit": pids_limit,
        "read_only_root_fs": read_only_root_fs,
        "seccomp_profile": seccomp_profile,
      }.items() if value is not None
    }
    self.mounts = mounts or []
    self.gpu_count = gpu_count
    self.network_disabled = network_disabled
    self.shm_size = shm_size
    self.command_timeout_seconds = command_timeout_seconds

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
                                     name_prefix="grader",
                                     mounts=self.mounts,
                                     gpu_count=self.gpu_count,
                                     network_disabled=self.network_disabled,
                                     shm_size=self.shm_size,
                                     **self.container_options)
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
      raise Autograder.exceptions.ContainerError(
        "No container available for command execution. "
        "Call start_container() before execute_command_in_container().")

    return target_container.execute_command(
      command, workdir, timeout_seconds=self.command_timeout_seconds)

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

  def _get_image(self, *args, **kwargs):
    return "ubuntu"

  def __enter__(self):
    """Context manager entry - ensure image is available."""
    if self.image is None:
      log.debug("Resolving grading image (registry cache lookup or build)")
      self.image = self._get_image()
    log.debug(f"Prepared docker image {self.image} context")
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    """Context manager exit - clean up docker resources."""
    log.debug("Exiting docker image context")
    self.cleanup()
    if exc_type is not None:
      log.error(f"An exception occurred: {exc_val}")
    return False

  @contextlib.contextmanager
  def _container_context(self):
    """Context manager for per-submission containers."""
    self.start_container()
    try:
      yield
    finally:
      self.stop_container()

  def cleanup(self) -> None:
    """Ensure docker resources are cleaned up on failure or early exit."""
    try:
      self.stop_container()
    except Exception as e:
      log.warning(f"Failed to stop container during cleanup: {e}")

    if self.image is not None and not isinstance(self.image, str):
      try:
        self.docker_client.remove_image(self.image, force=True)
      except Exception as e:
        log.warning(f"Failed to remove image during cleanup: {e}")
    self.image = None

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
    if self.image is None:
      log.debug("Building docker image")
      self.image = self._get_image()

    with self._container_context():
      if files_to_copy is not None:
        self.add_files_to_docker(files_to_copy)
      # input(f"Waiting on container {self.container}")
      return super().grade_submission(submission, *args, **kwargs)


# Preserve the historical module path and aliases for external callers.
from Autograder.graders.template_grader import TemplateGrader


# =============================================================================
# Backwards Compatibility Aliases
# =============================================================================
# These aliases preserve backwards compatibility for code that imports the
# old class names with double underscores.
Grader__docker = DockerGrader
Grader__template_grader = TemplateGrader
