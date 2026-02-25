"""
Docker utilities for grading systems.

Provides common Docker operations like client management, container lifecycle,
file operations, and command execution in a reusable way.
"""
import io
import json
import os
import pathlib
import shlex
import tarfile
import time
import threading
import uuid
from typing import List, Tuple, Optional, Union
from collections import defaultdict

import Autograder.exceptions

import logging

log = logging.getLogger(__name__)

# Global image usage counter - tracks how many containers are using each image
_image_usage_counter = defaultdict(int)
_image_usage_lock = threading.Lock()

# Lazy import docker to avoid import errors when docker is not available
docker = None


def _parse_bool_env(name: str, default: bool) -> bool:
  raw = os.getenv(name)
  if raw is None:
    return default
  return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, default: int | None) -> int | None:
  raw = os.getenv(name)
  if raw is None or raw.strip() == "":
    return default
  try:
    value = int(raw)
  except ValueError:
    log.warning(f"Invalid integer for {name}={raw!r}; using default {default!r}")
    return default
  if value <= 0:
    return None
  return value


def _parse_str_env(name: str, default: str | None) -> str | None:
  raw = os.getenv(name)
  if raw is None:
    return default
  value = raw.strip()
  if value == "":
    return None
  return value


def _default_seccomp_profile_path() -> str | None:
  packaged_profile = (pathlib.Path(__file__).resolve().parent / "seccomp" /
                      "autograder-seccomp.json")
  if packaged_profile.exists():
    return str(packaged_profile)
  return None


def _normalize_seccomp_profile(seccomp_profile: str | None) -> str | None:
  """
  Normalize seccomp config for Docker API usage.

  Docker CLI accepts seccomp file paths, but the Docker API expects either
  "unconfined" or JSON profile content.
  """
  if seccomp_profile is None:
    return None

  profile_value = seccomp_profile.strip()
  if profile_value == "":
    return None
  if profile_value == "unconfined":
    return "unconfined"

  profile_path = pathlib.Path(profile_value)
  if profile_path.exists():
    try:
      profile_json = json.loads(profile_path.read_text(encoding="utf-8"))
      return json.dumps(profile_json, separators=(",", ":"))
    except (OSError, json.JSONDecodeError) as e:
      log.warning(
        f"Unable to read Docker seccomp profile at {profile_value}: {e}")
      return None

  try:
    profile_json = json.loads(profile_value)
    return json.dumps(profile_json, separators=(",", ":"))
  except json.JSONDecodeError:
    log.warning(
      f"Invalid Docker seccomp profile value {profile_value!r}; expected 'unconfined', JSON content, or a path to a JSON file."
    )
    return None


DEFAULT_DOCKER_MEMORY_LIMIT = _parse_str_env("AUTOGRADER_DOCKER_MEMORY_LIMIT",
                                              "1g")
DEFAULT_DOCKER_NANO_CPUS = _parse_int_env("AUTOGRADER_DOCKER_NANO_CPUS",
                                           2_000_000_000)
DEFAULT_DOCKER_PIDS_LIMIT = _parse_int_env("AUTOGRADER_DOCKER_PIDS_LIMIT",
                                           256)
DEFAULT_DOCKER_READ_ONLY_ROOT_FS = _parse_bool_env(
  "AUTOGRADER_DOCKER_READ_ONLY_ROOT_FS", False)
DEFAULT_DOCKER_SECCOMP_PROFILE = _parse_str_env(
  "AUTOGRADER_DOCKER_SECCOMP_PROFILE", _default_seccomp_profile_path())
DEFAULT_DOCKER_TMPFS = {
  "/tmp": "rw,noexec,nosuid,size=64m",
  "/var/tmp": "rw,noexec,nosuid,size=64m",
}


def _import_docker() -> None:
  global docker
  if docker is None:
    import docker as docker_module
    docker = docker_module


class DockerClient:
  """
  Manages Docker client connection and provides common operations.
  
  Thread-safe Docker client wrapper with connection management
  and error handling.
  """

  _containers = set()
  _images = set()

  def __init__(self):
    self.client = None
    self._setup_client()

  def _setup_client(self) -> None:
    """Set up Docker client with error handling."""
    _import_docker()

    try:
      self.client = docker.from_env()
      # Test connection
      self.client.ping()
      log.debug("Docker client connected successfully")
    except docker.errors.DockerException as e:
      log.error(f"Docker isn't running: {e}")
      raise Autograder.exceptions.DockerError(
        f"Docker daemon not available: {e}") from e
    except docker.errors.APIError as e:
      log.error(f"Docker API error: {e}")
      raise Autograder.exceptions.DockerError(f"Docker API error: {e}") from e
    except Exception as e:
      log.error(f"Unexpected error connecting to Docker: {e}")
      raise Autograder.exceptions.ConfigurationError(
        f"Failed to initialize Docker client: {e}") from e

  def build_image(self, dockerfile_content: str,
                  tag: str) -> 'docker.models.images.Image':
    """
    Build a Docker image from dockerfile content.
    
    Args:
        dockerfile_content: Dockerfile as a string
        tag: Tag for the built image
        
    Returns:
        Built Docker image
    """
    log.info(f"Building docker image: \"{tag}\"")

    # Rebuild docker image every time.
    try:
      image, logs = self.client.images.build(fileobj=io.BytesIO(
        dockerfile_content.encode()),
                                             pull=True,
                                             nocache=True,
                                             tag=tag,
                                             rm=True,
                                             forcerm=True)

      log.debug(f"Successfully built docker image {image.tags}")
      log.debug(f"Adding image: {image}")
      self._images.add(image)
      return image
    except docker.errors.BuildError as e:
      log.error(f"Docker build failed for tag {tag}: {e}")
      raise Autograder.exceptions.ImageBuildError(
        f"Failed to build image {tag}: {e}") from e
    except docker.errors.APIError as e:
      log.error(f"Docker API error during build: {e}")
      raise Autograder.exceptions.DockerError(
        f"Docker API error building {tag}: {e}") from e

  @classmethod
  def cleanup(cls):
    log.debug("Running docker clean up")
    for container in list(cls._containers):
      log.debug(f"Removing container: {container}")
      try:
        container.stop(timeout=1)
        container.remove(force=True)
        cls._containers.discard(container)
      except docker.errors.APIError as e:
        status = getattr(e, "status_code", None)
        if status in (404, 409):
          log.debug(f"Container cleanup skipped ({status}): {e}")
          cls._containers.discard(container)
        else:
          log.warning("Stopping containers failed.")
          log.warning(e)
    for image in list(cls._images):
      log.debug(f"Removing image: {image}")
      cls.remove_image(image, force=True)

  @staticmethod
  def remove_image(image, force: bool = True) -> None:
    """Remove a Docker image with error handling."""
    try:
      image.remove(force=force)
      log.debug(
        f"Successfully removed image: {getattr(image, 'tags', 'unknown')}")
      DockerClient._images.discard(image)
    except AttributeError as e:
      log.warning(f"Image object missing remove method: {e}")
    except docker.errors.ImageNotFound:
      log.debug("Image already removed.")
      DockerClient._images.discard(image)
    except docker.errors.APIError as e:
      status = getattr(e, "status_code", None)
      if status == 404:
        log.debug(f"Image already removed: {e}")
        DockerClient._images.discard(image)
      else:
        log.warning(f"Docker API error removing image: {e}")
    except Exception as e:
      log.warning(f"Unexpected error removing image: {e}")

  def run_image(self, *args, **kwargs):
    container = self.client.containers.run(*args, **kwargs)
    self._containers.add(container)
    return container

  def build_image_from_context(
      self,
      context_path: str,
      tag: str,
      use_cached=True) -> 'docker.models.images.Image':
    """
    Build a Docker image from a directory context (containing Dockerfile and files).

    Args:
        context_path: Path to directory containing Dockerfile and build context
        tag: Tag for the built image

    Returns:
        Built Docker image
    """
    log.info(f"Building docker image from context: {tag}")

    # Check if image already exists to avoid rebuilding
    if False:  # todo: does it ever make sense to cache?  We'd want to check all inputs and that seems errorprone
      try:
        existing_image = self.client.images.get(tag)
        log.debug(f"Found existing image {tag}, reusing")
        return existing_image
      except docker.errors.ImageNotFound:
        # Image doesn't exist, need to build it
        pass

    try:
      image, logs = self.client.images.build(path=context_path,
                                             pull=True,
                                             nocache=True,
                                             tag=tag,
                                             rm=True,
                                             forcerm=True)

      log.debug(f"Successfully built docker image {image.tags}")
      self.__class__._images.add(image)
      return image
    except docker.errors.BuildError as e:
      build_detail = self._extract_build_error_detail(getattr(e, "build_log", None))
      detail_suffix = f" | detail: {build_detail}" if build_detail else ""
      log.error(f"Docker build failed for tag {tag}: {e}{detail_suffix}")
      raise Autograder.exceptions.ImageBuildError(
        f"Failed to build image {tag}: {e}{detail_suffix}") from e
    except docker.errors.APIError as e:
      log.error(f"Docker API error during build: {e}")
      raise Autograder.exceptions.DockerError(
        f"Docker API error building {tag}: {e}") from e

  @staticmethod
  def _extract_build_error_detail(build_log) -> str:
    """Extract a compact, human-useful error from docker build logs."""
    if build_log is None:
      return ""
    if isinstance(build_log, (str, bytes, bytearray)):
      return ""
    if not isinstance(build_log, list):
      try:
        build_log = list(build_log)
      except TypeError:
        return ""

    details = []
    for entry in build_log:
      if not isinstance(entry, dict):
        continue
      for key in ("error", "stream"):
        value = entry.get(key)
        if not isinstance(value, str):
          continue
        text = value.strip()
        if not text:
          continue
        lower = text.lower()
        if "running in" in lower and "/bin/sh -c" in lower:
          continue
        details.append(text)
      error_detail = entry.get("errorDetail")
      if isinstance(error_detail, dict):
        message = error_detail.get("message")
        if isinstance(message, str) and message.strip():
          details.append(message.strip())

    if not details:
      return ""
    unique = []
    for item in details:
      if item not in unique:
        unique.append(item)
    return " | ".join(unique[-3:])


class DockerContainer:
  """
  Manages the lifecycle of a single Docker container.
  
  Provides context manager support and common container operations
  like file copying and command execution.
  """

  def __init__(self,
               client: DockerClient,
               image: Union[str, 'docker.models.images.Image'],
               name_prefix: str = "grader",
               memory_limit: str | None = DEFAULT_DOCKER_MEMORY_LIMIT,
               nano_cpus: int | None = DEFAULT_DOCKER_NANO_CPUS,
               pids_limit: int | None = DEFAULT_DOCKER_PIDS_LIMIT,
               read_only_root_fs: bool = DEFAULT_DOCKER_READ_ONLY_ROOT_FS,
               seccomp_profile: str | None = DEFAULT_DOCKER_SECCOMP_PROFILE):
    self.client = client
    self.image = image
    self.container = None
    self.name_prefix = name_prefix
    self.memory_limit = memory_limit
    self.nano_cpus = nano_cpus
    self.pids_limit = pids_limit
    self.read_only_root_fs = read_only_root_fs
    self.seccomp_profile = seccomp_profile

    # Generate unique container name for thread safety
    thread_id = threading.current_thread().ident
    timestamp = int(time.time() * 1000000)
    self.container_name = f"{name_prefix}_{uuid.uuid4().hex[:8]}_{thread_id}_{timestamp}"

  def start(self) -> None:
    """Start the container."""
    security_opt = ["no-new-privileges:true"]
    normalized_seccomp = _normalize_seccomp_profile(self.seccomp_profile)
    if normalized_seccomp:
      security_opt.append(f"seccomp={normalized_seccomp}")
    elif self.seccomp_profile:
      log.warning(
        "Docker seccomp profile is invalid; using daemon default seccomp policy."
      )
    else:
      log.warning(
        "No Docker seccomp profile configured; set AUTOGRADER_DOCKER_SECCOMP_PROFILE to enforce one."
      )

    run_kwargs = {
      "image": self.image,
      "detach": True,
      "tty": True,
      "remove": True,
      "name": self.container_name,
      "security_opt": security_opt,
      # Keep the container alive for subsequent exec_run/put_archive calls.
      # Some base images default to commands that exit immediately.
      "command": ["sh", "-lc", "trap : TERM INT; while :; do sleep 3600; done"],
    }
    if self.memory_limit:
      run_kwargs["mem_limit"] = self.memory_limit
      run_kwargs["memswap_limit"] = self.memory_limit
    if self.nano_cpus is not None:
      run_kwargs["nano_cpus"] = self.nano_cpus
    if self.pids_limit is not None:
      run_kwargs["pids_limit"] = self.pids_limit
    if self.read_only_root_fs:
      run_kwargs["read_only"] = True
      run_kwargs["tmpfs"] = dict(DEFAULT_DOCKER_TMPFS)

    try:
      self.container = self.client.run_image(**run_kwargs)
      log.debug(f"Started container: {self.container_name}")
    except docker.errors.ContainerError as e:
      log.error(f"Container failed to start: {e}")
      if self.container is not None:
        try:
          self.container.remove(force=True)
        except Exception as cleanup_error:
          log.warning(
            f"Failed to cleanup container after start error: {cleanup_error}")
      raise Autograder.exceptions.ContainerError(
        f"Failed to start container {self.container_name}: {e}") from e
    except docker.errors.ImageNotFound as e:
      log.error(f"Image not found: {self.image}")
      if self.container is not None:
        try:
          self.container.remove(force=True)
        except Exception as cleanup_error:
          log.warning(
            f"Failed to cleanup container after image error: {cleanup_error}")
      raise Autograder.exceptions.DockerError(
        f"Image not found: {self.image}") from e
    except docker.errors.APIError as e:
      log.error(f"Docker API error starting container: {e}")
      if self.container is not None:
        try:
          self.container.remove(force=True)
        except Exception as cleanup_error:
          log.warning(
            f"Failed to cleanup container after API error: {cleanup_error}")
      raise Autograder.exceptions.DockerError(f"Docker API error: {e}") from e

  def stop(self, timeout: int = 1) -> None:
    """Stop and remove the container."""
    if self.container:
      try:
        self.container.stop(timeout=timeout)
        log.debug(f"Stopped container: {self.container_name}")
        try:
          self.container.remove(force=True)
          log.debug(f"Removed container: {self.container_name}")
          DockerClient._containers.discard(self.container)
        except docker.errors.NotFound:
          log.debug(f"Container {self.container_name} already removed")
          DockerClient._containers.discard(self.container)
        except docker.errors.APIError as e:
          status = getattr(e, "status_code", None)
          if status in (404, 409):
            log.debug(
              f"Container remove skipped ({status}) for {self.container_name}: {e}"
            )
            DockerClient._containers.discard(self.container)
          else:
            log.warning(
              f"Docker API error removing container {self.container_name}: {e}")
        except Exception as e:
          log.warning(
            f"Unexpected error removing container {self.container_name}: {e}")
      except docker.errors.NotFound:
        log.debug(f"Container {self.container_name} already removed")
      except docker.errors.APIError as e:
        status = getattr(e, "status_code", None)
        if status in (404, 409):
          log.debug(
            f"Container stop skipped ({status}) for {self.container_name}: {e}")
        else:
          log.warning(
            f"Docker API error stopping container {self.container_name}: {e}")
      except Exception as e:
        log.warning(
          f"Unexpected error stopping container {self.container_name}: {e}")
      finally:
        self.container = None

  def commit(self,
             repository: str,
             tag: str = "latest") -> 'docker.models.images.Image':
    """
    Create an image from the current container state.
    
    Args:
        repository: Repository name for the new image
        tag: Tag for the new image
        
    Returns:
        New Docker image
    """
    if not self.container:
      raise Autograder.exceptions.ContainerError(
        "Cannot commit - no running container")

    image = self.container.commit(repository=repository, tag=tag)
    try:
      self.client._images.add(image)
    except Exception as e:
      log.debug(f"Failed to track committed image for cleanup: {e}")
    return image

  def copy_files(self, files_to_copy: List[Tuple[io.IOBase, str]]) -> None:
    """
    Copy files to the container.

    Args:
        files_to_copy: List of (file_object, target_path) tuples where target_path
                       can be either a directory path or a full file path (dir/filename)
    """
    if not self.container:
      raise Autograder.exceptions.ContainerError(
        "Cannot copy files - no running container")

    for src_file, target_path in files_to_copy:
      self._copy_single_file(src_file, target_path)

  def _copy_single_file(self, src_file: io.IOBase, target_path: str) -> None:
    """
    Copy a single file to the container.

    Args:
        src_file: File object to copy
        target_path: Full path including filename (e.g., /repo/dir/newname.txt)
                     The directory portion will be the extraction target,
                     and the filename will be used in the tarball.
    """
    import os

    # Split target_path into directory and filename
    target_dir = os.path.dirname(target_path)
    target_filename = os.path.basename(target_path)

    # If target_path ends with /, treat it as a directory and use original filename
    if target_path.endswith('/'):
      target_dir = target_path.rstrip('/')
      target_filename = os.path.basename(src_file.name if hasattr(src_file, 'name') else 'file')

    # put_archive requires an existing destination directory.
    # Create it first so file_paths can target new nested locations.
    if not target_dir:
      target_dir = "/"

    mkdir_rc, mkdir_output = self.container.exec_run(cmd=[
      "mkdir", "-p", target_dir
    ],
                                                     demux=False,
                                                     tty=False)
    if mkdir_rc != 0:
      mkdir_stderr = mkdir_output.decode(
        errors="replace") if isinstance(mkdir_output, (bytes,
                                                       bytearray)) else str(
                                                         mkdir_output)
      raise Autograder.exceptions.ContainerError(
        f"Failed to create directory in container: {target_dir}. {mkdir_stderr}"
      )

    # Create a TarInfo object with the target filename
    tar_info = tarfile.TarInfo(name=target_filename)

    # Get file size
    src_file.seek(0, io.SEEK_END)
    tar_info.size = src_file.tell()
    src_file.seek(0)  # Reset to beginning

    # Set modification time
    tar_info.mtime = int(time.time())

    # Prepare the tarball
    tarstream = io.BytesIO()
    with tarfile.open(fileobj=tarstream, mode="w") as tarhandle:
      tarhandle.addfile(tar_info, src_file)
    tarstream.seek(0)

    # Push to container - extract to the directory
    self.container.put_archive(target_dir, tarstream)

  def execute_command(
      self,
      command: str,
      workdir: Optional[str] = None,
      timeout_seconds: int = 60) -> Tuple[int, bytes, bytes]:
    """
    Execute a command in the container.

    Args:
        command: Command to execute (shell command string, must be trusted/instructor-controlled)
        workdir: Working directory for the command
        timeout_seconds: Command timeout in seconds (default 60)

    Returns:
        Tuple of (return_code, stdout, stderr)

    Note:
        The command is executed via `timeout <n> bash -lc "<command>"`. Commands should come
        from trusted configuration (e.g., grading scripts), not from student input.
    """
    if not self.container:
      raise Autograder.exceptions.ContainerError(
        "Cannot execute command - no running container")

    log.debug(f"Executing command: {command}")

    extra_args = {}
    if workdir is not None:
      extra_args["workdir"] = workdir

    # Use list form for exec_run to avoid outer shell parsing.
    # Run timeout against a bash subprocess so compound shell commands
    # (e.g., if/then blocks) execute correctly under timeout.
    # Note: command must be trusted (from config) - not suitable for untrusted input.
    wrapped_command = ["timeout", str(timeout_seconds), "bash", "-lc", command]

    rc, (stdout, stderr) = (0, (b"", b""))
    try:
      rc, (stdout,
           stderr) = self.container.exec_run(cmd=wrapped_command,
                                             demux=True,
                                             tty=True,
                                             **extra_args)
    except docker.errors.APIError as e:
      raise Autograder.exceptions.ContainerError(
        f"Failed to execute command in container: {e}") from e

    log.debug(f"Command '{command}' returned {rc}")
    log.debug(f"stdout: {stdout}")
    log.debug(f"stderr: {stderr}")

    return rc, stdout or b'', stderr or b''

  def read_file(self, file_path: str) -> Optional[str]:
    """
    Read a file from the container.
    
    Args:
        file_path: Path to file in container
        
    Returns:
        File contents as string, or None if file not found
    """
    if not self.container:
      raise Autograder.exceptions.ContainerError(
        "Cannot read file: no running container. Start the container before reading files."
      )

    try:
      bits, stats = self.container.get_archive(file_path)
    except docker.errors.APIError as e:
      log.error(f"Failed to read file {file_path}: {e}")
      return None

    # Read file from docker
    f = io.BytesIO()
    for chunk in bits:
      f.write(chunk)
    f.seek(0)

    # Extract file from tarball
    with tarfile.open(fileobj=f, mode="r") as tarhandle:
      # Get the first file in the archive
      members = tarhandle.getmembers()
      if not members:
        return None

      file_member = members[0]
      extracted_file = tarhandle.extractfile(file_member)
      if extracted_file:
        extracted_file.seek(0)
        return extracted_file.read().decode()

    return None

  def __enter__(self):
    """Context manager entry."""
    self.start()
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    """Context manager exit with cleanup."""
    self.stop()
    if exc_type is not None:
      log.error(f"Exception in container context: {exc_val}")
    return False


class DockerContainerManager:
  """
  Manages multiple Docker containers for complex grading scenarios.
  
  Useful for graders that need multiple containers (e.g., step-by-step grading
  with golden and student containers).
  """

  def __init__(self, client: DockerClient):
    self.client = client
    self.containers = {}

  def create_container(self,
                       name: str,
                       image: Union[str, 'docker.models.images.Image'],
                       start_immediately: bool = False) -> DockerContainer:
    """
    Create a new container with the given name.
    
    Args:
        name: Logical name for the container
        image: Docker image to use
        start_immediately: Whether to start the container immediately
        
    Returns:
        DockerContainer instance
    """
    container = DockerContainer(self.client, image, name_prefix=name)
    self.containers[name] = container

    if start_immediately:
      container.start()

    return container

  def get_container(self, name: str) -> DockerContainer:
    """Get a container by name."""
    if name not in self.containers:
      raise Autograder.exceptions.ContainerError(
        f"Container '{name}' not found in manager. "
        "Create it first with create_container(...).")
    return self.containers[name]

  def stop_all(self) -> None:
    """Stop and cleanup all containers."""
    for container in self.containers.values():
      container.stop()
    self.containers.clear()

  def __enter__(self):
    """Context manager entry."""
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    """Context manager exit with cleanup."""
    self.stop_all()
