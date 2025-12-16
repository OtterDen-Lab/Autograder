"""
Docker utilities for grading systems.

Provides common Docker operations like client management, container lifecycle,
file operations, and command execution in a reusable way.
"""
import io
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
    for container in cls._containers:
      log.debug(f"Removing container: {container}")
      try:
        container.stop(timeout=1)
        container.remove(force=True)
      except docker.errors.APIError as e:
        log.warning("Stopping containers failed.")
        log.warning(e)
    for image in cls._images:
      log.debug(f"Removing image: {image}")
      cls.remove_image(image, force=True)

  @staticmethod
  def remove_image(image, force: bool = True) -> None:
    """Remove a Docker image with error handling."""
    try:
      image.remove(force=force)
      log.debug(
        f"Successfully removed image: {getattr(image, 'tags', 'unknown')}")
    except AttributeError as e:
      log.warning(f"Image object missing remove method: {e}")
    except docker.errors.APIError as e:
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
      log.error(f"Docker build failed for tag {tag}: {e}")
      raise Autograder.exceptions.ImageBuildError(
        f"Failed to build image {tag}: {e}") from e
    except docker.errors.APIError as e:
      log.error(f"Docker API error during build: {e}")
      raise Autograder.exceptions.DockerError(
        f"Docker API error building {tag}: {e}") from e


class DockerContainer:
  """
  Manages the lifecycle of a single Docker container.
  
  Provides context manager support and common container operations
  like file copying and command execution.
  """

  def __init__(self,
               client: DockerClient,
               image: Union[str, 'docker.models.images.Image'],
               name_prefix: str = "grader"):
    self.client = client
    self.image = image
    self.container = None
    self.name_prefix = name_prefix

    # Generate unique container name for thread safety
    thread_id = threading.current_thread().ident
    timestamp = int(time.time() * 1000000)
    self.container_name = f"{name_prefix}_{uuid.uuid4().hex[:8]}_{thread_id}_{timestamp}"

  def start(self) -> None:
    """Start the container."""
    try:
      self.container = self.client.run_image(image=self.image,
                                             detach=True,
                                             tty=True,
                                             remove=True,
                                             name=self.container_name)
      log.debug(f"Started container: {self.container_name}")
    except docker.errors.ContainerError as e:
      log.error(f"Container failed to start: {e}")
      raise Autograder.exceptions.ContainerError(
        f"Failed to start container {self.container_name}: {e}") from e
    except docker.errors.ImageNotFound as e:
      log.error(f"Image not found: {self.image}")
      raise Autograder.exceptions.DockerError(
        f"Image not found: {self.image}") from e
    except docker.errors.APIError as e:
      log.error(f"Docker API error starting container: {e}")
      raise Autograder.exceptions.DockerError(f"Docker API error: {e}") from e

  def stop(self, timeout: int = 1) -> None:
    """Stop and remove the container."""
    if self.container:
      try:
        self.container.stop(timeout=timeout)
        log.debug(f"Stopped container: {self.container_name}")
      except docker.errors.NotFound:
        log.debug(f"Container {self.container_name} already removed")
      except docker.errors.APIError as e:
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

    return self.container.commit(repository=repository, tag=tag)

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
      workdir: Optional[str] = None) -> Tuple[int, bytes, bytes]:
    """
    Execute a command in the container.
    
    Args:
        command: Command to execute
        workdir: Working directory for the command
        
    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    if not self.container:
      raise Autograder.exceptions.ContainerError(
        "Cannot execute command - no running container")

    log.debug(f"Executing command: {command}")

    extra_args = {}
    if workdir is not None:
      extra_args["workdir"] = workdir

    rc, (stdout,
         stderr) = self.container.exec_run(cmd=f"bash -c \"timeout 60 {command}\"",
                                           demux=True,
                                           tty=True,
                                           **extra_args)

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
      raise DockerOperationError("Cannot read file - no running container")

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
      raise DockerOperationError(f"Container '{name}' not found")
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


# Exception classes for better error handling
class DockerError(Exception):
  """Base class for Docker-related errors."""
  pass


class DockerConnectionError(DockerError):
  """Raised when Docker connection fails."""
  pass


class DockerOperationError(DockerError):
  """Raised when a Docker operation fails."""
  pass
