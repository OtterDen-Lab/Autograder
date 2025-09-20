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
from lms_interface.classes import Feedback
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
      raise Autograder.exceptions.ConfigurationError(f"Docker client initialization failed: {e}") from e
    
    # Default to using ubuntu image
    self.base_name_name = image if image is not None else "ubuntu"
    self.image = None # Only set this when we actually run grading to reduce how often we build
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
      base_image_name: str = "python:3.11-slim", # assume this is based on linux
      source_repo: str = "https://github.com/CSUMB-SCD-instructors/course-template",
      student_code_path: str = "",
      extra_installs=None, # todo: these will be tough, do later
      *args, **kwargs
  ):
    
    if extra_installs is None:
      extra_installs = []
    
    self.course_name = course_name
    self.assignment_name = assignment_name
    self.base_image_name = base_image_name
    self.source_repo = source_repo
    self.student_code_path = student_code_path
    self.extra_installs = extra_installs
    
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
      shutil.copytree(
        pathlib.Path(repo_path).expanduser(),
        dest
      )
      return
    
    else: # Get it from the remote location
      env = os.environ.copy()
      
      # If you need to use an SSH deploy key just for this command:
      # (works for git@host:org/repo.git or ssh://host/...)
      if deploy_key_path:
        ssh_cmd = f"ssh -i {deploy_key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        env["GIT_SSH_COMMAND"] = ssh_cmd
      
      cmd = ["git", "clone", repo_path, str(dest)]
      if depth:
        cmd[2:2] = ["--depth", str(depth)]  # insert after "clone" (optional shallow clone)
      
      subprocess.run(cmd, check=True, env=env)
    
  def _get_image(self):
    # What we want to do is to create a docker image that has the repository in it and installs all the required dependencies.
    # In this case that means we need to get the repo from either locally or remotely and then run `uv sync` in the right directory

    with tempfile.TemporaryDirectory() as temp_build_dir:
      
      # Get the main repo
      self._get_repo(self.source_repo, os.path.join(temp_build_dir, "repo"), depth=1)
      
      # If we have a golden repo, let's use it to set the extra files
      if self.golden_repo:
        # Download the golden repo, and we'll delete it later
        self._get_repo(self.golden_repo, os.path.join(temp_build_dir, "golden"))
        
        logging.debug(temp_build_dir)
        
        for f in self.files_from_golden:
          log.debug(f"Copying over golden file: {f}")
          shutil.copy(
            os.path.join(temp_build_dir, "golden", "programming-assignments", self.assignment_name, f),
            os.path.join(temp_build_dir, "repo", "programming-assignments", self.assignment_name, f),
          )
        
        # Remove the golden for now
        shutil.rmtree(os.path.join(temp_build_dir, "golden"))
      
      # Set up dockerfile
      dockerfile_lines = [
        f"FROM {self.base_image_name}",
        "COPY repo /repo",
        "COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/",
        "WORKDIR /repo",
        "RUN rm -rf .venv",
        "USER root",
        "RUN uv sync --locked"
      ]
      
      # Next, we want to save our dockerfile
      with open(os.path.join(temp_build_dir, "Dockerfile"), "w") as dockerfile_fid:
        dockerfile_fid.write('\n'.join(dockerfile_lines) + "\n")
      
      image = self.docker_client.build_image_from_context(
        context_path=temp_build_dir,
        tag=f"template-grader:{self.course_name}-{self.assignment_name}-{uuid.uuid4().hex}",
        use_cached=True
      )
    return image
  
  def grade_submission(self, submission, *args, **kwargs) -> Feedback:
    # Prepare files to copy to docker container
    submission_files = []
    for f in submission.files:
      # Copy all files to the working directory
      submission_files.append(
        (
          f,
          os.path.join(
            f"/repo/programming-assignments/{self.assignment_name}",
            self.student_code_path
          )
        )
      )
    log.debug(f"submission.files: {submission.files}")
    log.debug(f"submission_files: {submission_files}")
    
    # Grade using parent class method
    return super().grade_submission(
      submission,
      files_to_copy=submission_files,
      *args, **kwargs
    )
  
  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    rc, stdout, stderr = execution_results
    
    # Try to read the feedback.yaml file (note: different from results.yaml in parent)
    feedback_content = self.read_file_from_container("/tmp/feedback.yaml")
    
    if feedback_content:
      try:
        feedback_data = yaml.safe_load(feedback_content)
        if isinstance(feedback_data, dict):
          grade = float(feedback_data.get('grade', 0.0))
          comments = feedback_data.get('comments', 'No comments provided')
          logs = feedback_data.get('logs', '')
          
          full_feedback = comments
          if logs and logs.strip():
            full_feedback += f"\n\n--- Execution Logs ---\n{logs}"
          
          return Feedback(score=grade, comments=full_feedback)
      except Exception as e:
        log.error(f"Failed to parse feedback YAML: {e}")
    
    # Fallback to parent class behavior
    return super().score_grading(execution_results, *args, **kwargs)
  
  def execute_grading(self, *args, **kwargs) -> Tuple[int, str, str]:
    # Execute the grading script
    rc, stdout, stderr = self.execute_command_in_container(
      command=self.grading_script,
      workdir=self.working_dir
    )
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
    self.container_manager.create_container("student", rollback_image, start_immediately=True)
  
  def start(self, image):
    """Start both golden and student containers."""
    self.container_manager.create_container("golden", image, start_immediately=True)
    self.container_manager.create_container("student", image, start_immediately=True)
  
  def stop_container(self):
    """Stop all containers."""
    self.container_manager.stop_all()
  
  def execute_grading(self, golden_lines=[], student_lines=[], rollback=True, *args, **kwargs):
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
      
      if (not self.outputs_match(stdout_g, stdout_s, stderr_g, stderr_s, rc_g, rc_s)) and rollback:
        # Bring the student container up to date with our container
        self.rollback()
    
    return golden_results, student_results
  
  @staticmethod
  def outputs_match(stdout_g, stdout_s, stderr_g, stderr_s, rc_g, rc_s) -> bool:
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
          golden_results["rc"][i], student_results["rc"][i]
      ):
        continue
      num_matches += 1
    
    return Feedback(
      score=(100.0 * num_matches / len(golden_results["stdout"])),
      comments=f"Matched {num_matches} out of {len(golden_results['stdout'])}"
    )
  
  def grade_assignment(self, input_files: List[str], *args, **kwargs) -> Feedback:
    
    golden_lines = self.rubric["steps"]
    student_lines = self.parse_student_file(input_files[0])
    
    # Start containers
    self.start(self.image)
    
    try:
      results = self.execute_grading(golden_lines=golden_lines, student_lines=student_lines, *args, **kwargs)
      feedback = self.score_grading(results, *args, **kwargs)
    finally:
      # Clean up containers
      self.stop_container()
    
    log.debug(f"final results: {feedback}")
    return feedback