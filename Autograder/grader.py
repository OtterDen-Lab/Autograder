#!env python
from __future__ import annotations

import abc
import time
import uuid

import io
import tarfile
import os
import threading

from typing import List, Tuple, Optional

from Autograder.assignment import Assignment
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback, Submission

# Import all grader implementations to ensure they're registered
try:
  import Autograder.graders
except ImportError:
  # Graders package may not be available in all environments
  pass

docker = None
def _import_docker():
  global docker
  if docker is None:
    import docker as docker_module
    docker = docker_module


import logging
log = logging.getLogger(__name__)


@GraderRegistry.register("Dummy")
class Grader(abc.ABC):
  """
  Base abstract class for all graders.
  
  Provides the framework for grading assignments by processing submissions
  and generating feedback.
  """
  def __init__(self, *args, **kwargs):
    super().__init__()
    self.ready_to_finalize = True

  def grade_assignment(self, assignment: Assignment, *args, **kwargs) -> None:
    """
    Takes an assignment and walks through its submissions and grades each.
    :param assignment: Takes in an assignment.Assignment object to grade
    :return:
    """
    for submission in assignment.submissions:
      if submission.files is None or len(submission.files) == 0:
        submission.feedback = Feedback(0.0, "Assignment submission files missing")
        continue
      if submission.status != Submission.Status.GRADED:
        log.info("Skipping submission due to already being graded")
      submission.feedback = self.grade_submission(submission, **kwargs)

  def grade_submission(self, submission: Submission, *args, **kwargs) -> Feedback:
    """
    Takes in a submission, grades it, and returns back a Feedback
    :param submission: A Submission object that may have files associated with it
    :param kwargs:
    :return: returns a Feedback object for the submission
    """
    execution_results = self.execute_grading(*args, **kwargs)
    return self.score_grading(execution_results, *args, **kwargs)
  
  @abc.abstractmethod
  def execute_grading(self, *args, **kwargs):
    """
    Implements the steps to actually execute the grading, such as running a make command.
    :param args:
    :param kwargs:
    :return:
    """
    pass
  
  @abc.abstractmethod
  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    """
    Scores the grading based on execution results, such as stdout or stderr, but can also perform other actions
    :param execution_results:
    :param args:
    :param kwargs:
    :return:
    """
    pass
  
  def assignment_needs_preparation(self):
    return True

  def prepare(self, *args, **kwargs):
    """
    Anything that is needed to take the assignment and prepare it for grading.
    For example, making a CSV file from the submissions for manual grading
    :param args:
    :param kwargs:
    :return:
    """
  
  def finalize(self, *args, **kwargs):
    """
    anything that is needed to connect the grades/feedback to the submissions after grading.
    For example, loading up the CSV and connecting grades to the submissions
    :param args:
    :param kwargs:
    :return:
    """
  
  def cleanup(self):
    pass


class Grader__docker(Grader, abc.ABC):
  """
  Base class for Docker-based graders.
  
  Provides common Docker functionality like container management,
  file copying, and command execution.
  """
  def __init__(self, image=None, *args, **kwargs):
    super().__init__(*args, **kwargs)
    
    # Import docker if needed
    _import_docker()
    
    # Set up docker client per instance for thread safety
    try:
      self.client = docker.from_env()
      # Try to perform an operation that requires Docker to be running
      self.client.ping()  # or client.containers.list()
      log.debug("Docker client connected successfully")
    except docker.errors.DockerException as e:
      log.error(f"Docker isn't running: {e}")
      # Handle the situation when Docker daemon isn't available
      exit(8)
    except docker.errors.APIError as e:
      log.error(f"Docker API error: {e}")
      # Handle other API-related errors
      exit(8)
    
    # Default to using ubuntu image
    self.image = image if image is not None else "ubuntu"
    self.container: Optional[docker.models.containers.Container] = None
    
    # Generate unique container name for thread safety
    self.container_name_prefix = f"grader_{uuid.uuid4().hex[:8]}"
  
  def cleanup(self):
    # Try to remove image, and if it hasn't been set up properly delete
    try:
      self.image.remove(force=True)
    except AttributeError:
      log.warning("Deleting image failed")
      
      
  # Helper functions below here
  def build_docker_image(self, dockerfile_str):
    """
    Given a dockerfile as a string, creates and returns this image
    :param dockerfile_str: dockerfile as a single string
    :return: a docker image
    """
    log.info("Building docker image for grading...")
    
    image, logs = self.client.images.build(
      fileobj=io.BytesIO(dockerfile_str.encode()),
      pull=True,
      nocache=True,
      tag=f"grading:{self.__class__.__name__.lower()}_{self.container_name_prefix}",
      rm=True,
      forcerm=True
    )
    
    log.debug(f"Successfully build docker image {image.tags}")
    return image
  
  def start_container(self, image : docker.models.images):
    # Create unique container name with timestamp to avoid conflicts when multiple containers per thread
    container_name = f"{self.container_name_prefix}_{threading.current_thread().ident}_{int(time.time() * 1000000)}"
    self.container = self.client.containers.run(
      image=image,
      detach=True,
      tty=True,
      remove=True,
      name=container_name
    )
    
  def stop_container(self):
    self.container.stop(timeout=1)
    self.container = None
  
  def add_files_to_docker(self, files_to_copy : List[Tuple[str,str]] = None):
    """
    
    :param files_to_copy: Format is [(src, target), ...]):
    :return:
    """
  
    def add_file_to_container(src_file, target_dir, container):
      # Create a TarInfo object
      tar_info = tarfile.TarInfo(name=src_file.name if hasattr(src_file, 'name') else 'file')
      
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
      
      # Push to container
      container.put_archive(f"{target_dir}", tarstream)
    
    for src_file, target_dir in files_to_copy:
      add_file_to_container(src_file, target_dir, self.container)
  
  def execute_command_in_container(self, command="", container=None, workdir=None) -> Tuple[int, str, str]:
    log.debug(f"executing: {command}")
    if container is None:
      container = self.container
    
    extra_args = {}
    if workdir is not None:
      extra_args["workdir"] = workdir
    
    rc, (stdout, stderr) = container.exec_run(
      cmd=f"bash -c \"{command}\"",
      demux=True,
      tty=True,
      **extra_args
    )
    log.debug(f"Command: \"{command}")
    log.debug(f"rc: {rc}")
    log.debug(f"stdout: {stdout}")
    log.debug(f"stderr: {stderr}")
    return rc, stdout, stderr
  
  def read_file_from_container(self, path_to_file) -> Optional[str]:
    
    try:
      # Try to find the file on the system
      bits, stats = self.container.get_archive(path_to_file)
    except docker.errors.APIError as e:
      log.error(f"Get archive failed: {e}")
      return None
    
    # Read file from docker
    f = io.BytesIO()
    for chunk in bits:
      f.write(chunk)
    f.seek(0)
    
    # Open the tarball we just pulled and read the contents to a string buffer
    with tarfile.open(fileobj=f, mode="r") as tarhandle:
      results_f = tarhandle.getmember("results.json")
      f = tarhandle.extractfile(results_f)
      f.seek(0)
      return f.read().decode()
  
  def __enter__(self):
    log.debug(f"Starting docker image {self.image} context")
    self.start_container(self.image)
  
  def __exit__(self, exc_type, exc_val, exc_tb):
    log.debug(f"Exiting docker image context")
    self.stop_container()
    if exc_type is not None:
      log.error(f"An exception occured: {exc_val}")
      log.error(exc_tb)
    return False
  
  def grade_submission(self, submission, files_to_copy=None, *args, **kwargs) -> Feedback:
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
      return super().grade_submission(submission, *args, **kwargs)