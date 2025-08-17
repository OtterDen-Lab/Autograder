#!env python
from __future__ import annotations

import abc

from typing import List, Tuple, Optional

from Autograder.assignment import Assignment
from Autograder.registry import GraderRegistry
from Autograder.docker_utils import DockerClient, DockerContainer, DockerError
import Autograder.exceptions
from lms_interface.classes import Feedback, Submission

# Import all grader implementations to ensure they're registered
try:
  import Autograder.graders
except ImportError:
  # Graders package may not be available in all environments
  pass


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
      if not submission.files:
        submission.feedback = Feedback(0.0, "Assignment submission files missing")
        continue
      if submission.status == Submission.Status.GRADED:
        log.info("Skipping submission due to already being graded")
        continue
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
  def execute_grading(self, *args, **kwargs) -> any:
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
  
  def assignment_needs_preparation(self) -> bool:
    return True

  def prepare(self, *args, **kwargs) -> None:
    """
    Anything that is needed to take the assignment and prepare it for grading.
    For example, making a CSV file from the submissions for manual grading
    :param args:
    :param kwargs:
    :return:
    """
  
  def finalize(self, *args, **kwargs) -> None:
    """
    anything that is needed to connect the grades/feedback to the submissions after grading.
    For example, loading up the CSV and connecting grades to the submissions
    :param args:
    :param kwargs:
    :return:
    """
  
  def cleanup(self) -> None:
    pass


class Grader__docker(Grader, abc.ABC):
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
      raise Autograder.exceptions.ConfigurationError(f"Docker client initialization failed: {e}") from e
    
    # Default to using ubuntu image
    self.image = image if image is not None else "ubuntu"
    self.container: Optional[DockerContainer] = None
  
  def cleanup(self) -> None:
    """Clean up Docker resources."""
    if hasattr(self, 'image') and hasattr(self.image, 'remove'):
      self.docker_client.remove_image(self.image)
    
    if self.container:
      self.container.stop()
      
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
    self.container = DockerContainer(
      self.docker_client, 
      image_to_use,
      name_prefix="grader"
    )
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
  
  def execute_command_in_container(self, command="", container=None, workdir=None) -> Tuple[int, bytes, bytes]:
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
  
  def __enter__(self):
    """Context manager entry - start container."""
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