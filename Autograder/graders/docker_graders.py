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
import yaml
from collections import defaultdict
from typing import Tuple, Optional, List

from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback
from Autograder.docker_utils import DockerClient, DockerContainer, DockerError
import Autograder.exceptions
from Autograder.grader import Grader

import logging
log = logging.getLogger(__name__)


class Grader__docker(Grader):
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
    # Stop any running containers first
    if self.container:
      self.container.stop()
      self.container = None
    
    # Remove custom built images using safe removal (usage counting)
    if hasattr(self, 'image') and hasattr(self.image, 'remove'):
      try:
        self.docker_client.safe_remove_image(self.image)
        log.debug(f"Safely cleaned up Docker image: {getattr(self.image, 'tags', 'unknown')}")
      except Exception as e:
        log.warning(f"Failed to clean up Docker image: {e}")
    
    # Clean up any orphaned containers and images created by this grader
    self._cleanup_orphaned_resources()
  
  def _cleanup_orphaned_resources(self) -> None:
    """Clean up any orphaned Docker containers and images created by this grader."""
    try:
      # Clean up containers with our grader prefix
      containers = self.docker_client.client.containers.list(all=True, filters={'name': 'grader'})
      for container in containers:
        try:
          container.stop(timeout=1)
          container.remove(force=True)
          log.debug(f"Cleaned up orphaned container: {container.name}")
        except Exception as e:
          log.debug(f"Failed to clean up container {container.name}: {e}")
      
      # Clean up dangling images from our grading operations
      grading_images = self.docker_client.client.images.list(filters={'label': 'grading'})
      for image in grading_images:
        try:
          self.docker_client.remove_image(image)
        except Exception as e:
          log.debug(f"Failed to clean up grading image {image.tags}: {e}")
      
      # Clean up images with our grading tag pattern
      all_images = self.docker_client.client.images.list(filters={'dangling': False})
      for image in all_images:
        if image.tags:
          for tag in image.tags:
            if tag.startswith('grading:'):
              try:
                self.docker_client.remove_image(image)
                log.debug(f"Cleaned up grading image: {tag}")
                break
              except Exception as e:
                log.debug(f"Failed to clean up grading image {tag}: {e}")
    except Exception as e:
      log.warning(f"Failed to clean up orphaned Docker resources: {e}")
  
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


@GraderRegistry.register("docker-configurable")
class Grader__docker_configurable(Grader__docker):
  """
  Configurable Docker grader that can run custom grading scripts.
  
  Supports:
  - Custom grading scripts or command sequences
  - Additional package installs
  - Custom Dockerfiles
  - File copying to containers
  - Score scaling to Canvas assignment points (canvas_points parameter overrides Canvas API)
  """
  
  def __init__(self,
               grading_script=None,
               grading_commands=None,
               working_dir="/tmp/grading",
               additional_installs=None,
               dockerfile_text=None,
               dockercompose_text=None,
               additional_files=None,
               base_image="ubuntu",
               canvas_points=None,
               *args, **kwargs):
    # Map base_image to image parameter for parent class
    if base_image:
      kwargs['image'] = base_image
    super().__init__(*args, **kwargs)
    self.grading_script = grading_script
    self.grading_commands = grading_commands if grading_commands else []
    self.working_dir = working_dir
    self.additional_installs = additional_installs if additional_installs else []
    self.dockerfile_text = dockerfile_text
    self.dockercompose_text = dockercompose_text
    self.additional_files = additional_files if additional_files else []
    self.canvas_points = canvas_points  # OVERRIDES Canvas API points_possible for score scaling
    self.assignment = None  # Will store the assignment object for score scaling
    
    if not self.grading_script and not self.grading_commands:
      raise ValueError(
        "Must specify either grading_script or grading_commands"
      )
    
    if self.grading_script and self.grading_commands:
      raise ValueError(
        "Cannot specify both grading_script and grading_commands"
      )
    
    # Build custom image if needed
    if (self.dockerfile_text or self.additional_installs or
        self.additional_files):
      # todo: put off building until we actually need the image -- that is, until we actually need it
      # note: this will rely on haveing a separate "image" and "base_image"
      self.image = self._build_custom_image()
  
  def _build_custom_image(self):
    """Build a custom Docker image with additional installs and files using temporary build context"""

    
    with tempfile.TemporaryDirectory() as temp_build_dir:
      log.info(f"Creating temporary build context in {temp_build_dir}")
      
      # Copy additional files to build context
      if self.additional_files:
        for file_spec in self.additional_files:
          if isinstance(file_spec, dict):
            src = file_spec.get('src')
            dst_relative = file_spec.get('dst', self.working_dir).lstrip('/')
            if src:
              self._copy_to_build_context(src, temp_build_dir, dst_relative)
          elif isinstance(file_spec, str):
            dst_relative = self.working_dir.lstrip('/')
            self._copy_to_build_context(file_spec, temp_build_dir, dst_relative)
      
      # Build dockerfile
      if self.dockerfile_text:
        dockerfile_content = self.dockerfile_text
      else:
        base_image = self.image if hasattr(self, 'image') and self.image != "ubuntu" else "ubuntu"
        dockerfile_lines = [f"FROM {base_image}"]
        
        # Separate pre-copy and post-copy commands
        pre_copy_commands = []
        post_copy_commands = []
        
        for install_cmd in self.additional_installs:
          # Commands that need files should run after COPY
          if 'uv sync' in install_cmd or 'cd /tmp/course-template' in install_cmd:
            post_copy_commands.append(install_cmd)
          else:
            pre_copy_commands.append(install_cmd)
        
        # Add pre-copy installs (like installing uv)
        if pre_copy_commands:
          dockerfile_lines.append("# Install additional packages")
          for install_cmd in pre_copy_commands:
            dockerfile_lines.append(f"RUN {install_cmd}")
        
        # Add file copying using relative paths
        if self.additional_files:
          dockerfile_lines.append("# Copy additional files")
          for file_spec in self.additional_files:
            if isinstance(file_spec, dict):
              src = file_spec.get('src')
              dst = file_spec.get('dst', self.working_dir)
              if src:
                # Use relative path in build context
                src_relative = self._get_relative_copy_path(src, file_spec.get('dst', self.working_dir))
                # Ensure destination ends with / for directory copy
                dst_with_slash = dst if dst.endswith('/') else dst + '/'
                dockerfile_lines.append(f"COPY {src_relative} {dst_with_slash}")
            elif isinstance(file_spec, str):
              src_relative = os.path.basename(file_spec) if '*' not in file_spec else "*"
              # Ensure destination ends with / for directory copy
              work_dir_with_slash = self.working_dir if self.working_dir.endswith('/') else self.working_dir + '/'
              dockerfile_lines.append(f"COPY {src_relative} {work_dir_with_slash}")
        
        # Add post-copy commands (like uv sync)
        if post_copy_commands:
          dockerfile_lines.append("# Post-copy setup")
          for install_cmd in post_copy_commands:
            dockerfile_lines.append(f"RUN {install_cmd}")
        
        dockerfile_lines.extend([
          f"WORKDIR {self.working_dir}",
          "CMD [\"/bin/bash\"]"
        ])
        dockerfile_content = '\n'.join(dockerfile_lines)
      
      # Write Dockerfile to build context
      dockerfile_path = os.path.join(temp_build_dir, 'Dockerfile')
      with open(dockerfile_path, 'w') as f:
        f.write(dockerfile_content)
      
      log.info("Building custom Docker image with additional configuration...")
      
      # Build image using the temp directory as build context
      tag = f"grading:{self.__class__.__name__.lower()}"
      return self.docker_client.build_image_from_context(temp_build_dir, tag)

  def _copy_to_build_context(self, src_path, build_dir, dst_relative):
    """Copy files/directories to the Docker build context"""
    import glob
    import os
    import shutil
    log.debug(f"dockerfile_test: {self.dockerfile_text}")
    log.debug(f"image: {self.image}")
    
    if self.dockerfile_text:
      # Use provided dockerfile
      dockerfile_content = self.dockerfile_text
    else:
      # Build dockerfile from base image + additions
      base_image = self.image if hasattr(self, 'image') and self.image != "ubuntu" else "ubuntu"
      
      dockerfile_lines = [f"FROM {base_image}"]
      
      # Add additional package installs
      if self.additional_installs:
        dockerfile_lines.append("# Install additional packages")
        for install_cmd in self.additional_installs:
          dockerfile_lines.append(f"RUN {install_cmd}")
      
      # Add additional files via COPY commands
      if self.additional_files:
        dockerfile_lines.append("# Copy additional files")
        for file_spec in self.additional_files:
          if isinstance(file_spec, dict):
            src = file_spec.get('src')
            dst = file_spec.get('dst', self.working_dir)
            if src:
              dockerfile_lines.append(f"COPY {src} {dst}")
          elif isinstance(file_spec, str):
            dockerfile_lines.append(f"COPY {file_spec} {self.working_dir}")
      
      # Set working directory
      dockerfile_lines.append(f"WORKDIR {self.working_dir}")
      dockerfile_lines.append("CMD [\"/bin/bash\"]")
      
      dockerfile_content = '\n'.join(dockerfile_lines)
    
    log.info("Building custom Docker image with additional configuration...")
    log.debug(f"Dockerfile content:\n{dockerfile_content}")
    
    # Handle glob patterns
    if '*' in src_path:
      matched_files = glob.glob(os.path.expanduser(src_path))
      for matched_file in matched_files:
        dst_path = os.path.join(build_dir, dst_relative, os.path.basename(matched_file))
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        if os.path.isdir(matched_file):
          shutil.copytree(matched_file, dst_path, dirs_exist_ok=True)
        else:
          shutil.copy2(matched_file, dst_path)
    else:
      # Single file or directory
      expanded_src = os.path.expanduser(src_path)
      dst_path = os.path.join(build_dir, dst_relative)
      os.makedirs(os.path.dirname(dst_path), exist_ok=True)
      
      if os.path.isdir(expanded_src):
        shutil.copytree(expanded_src, dst_path, dirs_exist_ok=True)
      else:
        shutil.copy2(expanded_src, dst_path)

  def _get_relative_copy_path(self, src_path, dst_path):
    """Get the relative path for COPY command in Dockerfile"""
    if '*' in src_path:
      return "*"
    else:
      return dst_path.lstrip('/') + "/" + os.path.basename(os.path.expanduser(src_path))
  
  def execute_grading(self, *args, **kwargs) -> Tuple[int, str, str]:
    # Create working directory
    rc, stdout, stderr = self.execute_command_in_container(f"mkdir -p {self.working_dir}")
    if rc != 0:
      log.error(f"Failed to create working directory: {stderr}")
      return rc, stdout, stderr
    
    rc, stdout, stderr = self.execute_command_in_container(f"ls -l {self.working_dir}")
    if self.grading_script:
      # Execute the grading script
      rc, stdout, stderr = self.execute_command_in_container(
        command=self.grading_script,
        workdir=self.working_dir
      )
    else:
      # Execute the series of commands
      combined_stdout = []
      combined_stderr = []
      final_rc = 0
      
      for command in self.grading_commands:
        rc, stdout, stderr = self.execute_command_in_container(
          command=command,
          workdir=self.working_dir
        )
        if stdout:
          combined_stdout.append(stdout.decode() if isinstance(stdout, bytes) else stdout)
        if stderr:
          combined_stderr.append(stderr.decode() if isinstance(stderr, bytes) else stderr)
        if rc != 0:
          final_rc = rc
      
      rc = final_rc
      stdout = '\n'.join(combined_stdout).encode() if combined_stdout else b''
      stderr = '\n'.join(combined_stderr).encode() if combined_stderr else b''
      
      log.debug(f"stdout: {stdout}")
    
    return rc, stdout, stderr
  
  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    rc, stdout, stderr = execution_results
    
    # Decode stdout if it's bytes
    stdout_str = stdout.decode() if isinstance(stdout, bytes) else stdout
    stderr_str = stderr.decode() if isinstance(stderr, bytes) else stderr
    
    # Try to parse YAML output - first from results file, then from stdout
    score = 0.0
    feedback_text = ""
    yaml_output = None
    
    # First, try to read from results.yaml file in the container
    try:
      results_file_path = f"/tmp/results.yaml"
      results_content = self.read_file_from_container(results_file_path)
      if results_content:
        yaml_output = yaml.safe_load(results_content)
        log.info("Successfully loaded YAML from results.yaml file")
        log.info(f"YAML content: {yaml_output}")
    except Exception as e:
      log.debug(f"Failed to read results.yaml file: {e}")
    
    # Fallback to parsing stdout if file reading failed
    if yaml_output is None:
      try:
        yaml_output = yaml.safe_load(stdout_str)
        log.info("Successfully loaded YAML from stdout")
      except (yaml.YAMLError, ValueError, TypeError) as e:
        log.warning(f"Failed to parse YAML from grading output: {e}")
        feedback_text = "Failed to parse grading results"
    
    # Extract score and feedback if we have valid YAML
    if yaml_output and isinstance(yaml_output, dict):
      # Handle different possible YAML structures
      if 'test_summary' in yaml_output:
        # Structure from run_tests.py
        test_summary = yaml_output['test_summary']
        total_earned = test_summary.get('total_points_earned', 0)
        total_possible = test_summary.get('total_points_possible', 1)
        
        # Calculate percentage from local points
        percentage = float(total_earned / total_possible) if total_possible > 0 else 0.0
        
        # Determine Canvas points to use (in priority order)
        canvas_points_possible = None
        
        # 1. Use explicit canvas_points parameter from YAML config
        if self.canvas_points is not None:
          canvas_points_possible = float(self.canvas_points)
          log.info(f"Using explicit canvas_points from config: {canvas_points_possible}")
        
        # 2. Try to get points_possible from Canvas assignment
        elif self.assignment and hasattr(self.assignment, 'lms_assignment'):
          try:
            canvas_points_possible = getattr(self.assignment.lms_assignment, 'points_possible', None)
            if canvas_points_possible is not None:
              canvas_points_possible = float(canvas_points_possible)
              log.info(f"Using Canvas assignment points_possible: {canvas_points_possible}")
          except Exception as e:
            log.warning(f"Failed to get Canvas points_possible: {e}")
        
        # Convert percentage to Canvas points or use percentage as fallback
        if canvas_points_possible is not None:
          score = percentage * canvas_points_possible
          log.info(f"Converted local score {total_earned}/{total_possible} ({percentage:.1%}) to Canvas score {score}/{canvas_points_possible}")
        else:
          # Fallback to percentage (0-100) if no Canvas points info available
          score = percentage * 100
          log.info(f"Using percentage score: {score:.1f}% (no Canvas points info available)")
        
        # Create detailed feedback
        if canvas_points_possible is not None:
          feedback_lines = [
            f"Score: {total_earned}/{total_possible} local points ({percentage:.1%}) = {score:.1f}/{canvas_points_possible} Canvas points",
            f"Tests passed: {test_summary.get('passed_tests', 0)}/{test_summary.get('total_tests', 0)}",
            ""
          ]
        else:
          feedback_lines = [
            f"Score: {total_earned}/{total_possible} ({score:.1f}%)",
            f"Tests passed: {test_summary.get('passed_tests', 0)}/{test_summary.get('total_tests', 0)}",
            ""
          ]
        
        # Add individual test results
        for test_result in yaml_output.get('test_results', []):
          status = "✓" if test_result['status'] == 'PASSED' else "✗"
          points = f"{test_result.get('points_earned', 0)}/{test_result.get('points_possible', 0)}"
          feedback_lines.append(f"{status} {test_result['test_name']}: {points} pts")
          if test_result.get('error_message'):
            feedback_lines.append(f"   Error: {test_result['error_message']}")
        
        feedback_text = "\n".join(feedback_lines)
      else:
        # Simple structure with direct score and feedback
        score = float(yaml_output.get('score', 0.0))
        feedback_text = yaml_output.get('feedback', '')
    
    # Include raw stdout as additional feedback
    full_feedback = feedback_text
    if stdout_str.strip():
      full_feedback += f"\n\n--- Raw Output ---\n{stdout_str}"
    if stderr_str.strip():
      full_feedback += f"\n\n--- Error Output ---\n{stderr_str}"
    
    return Feedback(
      score=score,
      comments=full_feedback.strip()
    )
  
  def grade_assignment(self, assignment, *args, **kwargs) -> None:
    """Override to capture assignment object for score scaling."""
    self.assignment = assignment
    return super().grade_assignment(assignment, *args, **kwargs)
  
  def grade_submission(self, submission, *args, **kwargs) -> Feedback:
    # Prepare files to copy to docker container
    submission_files = []
    for f in submission.files:
      # Copy all files to the working directory
      submission_files.append((f, self.working_dir))
    
    # Grade using parent class method
    return super().grade_submission(
      submission,
      files_to_copy=submission_files,
      *args, **kwargs
    )


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
      base_image_name: str = "python:3.11-slim", # assume this is based on linux
      source_repo: str = "https://github.com/CSUMB-SCD-instructors/course-template", # remote access with deploy key could be interesting..
      extra_installs=None, # todo: these will be tough, do later
      *args, **kwargs
  ):
    
    if extra_installs is None:
      extra_installs = []
    self.assignment_name = assignment_name
    self.base_image_name = base_image_name
    self.source_repo = source_repo
    self.extra_installs = extra_installs
    
    super().__init__(*args, **kwargs)
    
    self._build_image()
    
    # todo: these two can likely be removed if we go full template.
    self.working_dir = "/repo"
    self.grading_script = f"/repo/.venv/bin/python /repo/scripts/grader.py --PA {self.assignment_name}"
    
    return
  
  @staticmethod
  def _clone_repo(remote: str, dest="repo", depth=None, deploy_key_path=None):
    dest = pathlib.Path(dest).expanduser().resolve()
    if dest.exists():
      raise FileExistsError(f"{dest} already exists")
    
    env = os.environ.copy()
    
    # If you need to use an SSH deploy key just for this command:
    # (works for git@host:org/repo.git or ssh://host/...)
    if deploy_key_path:
      ssh_cmd = f"ssh -i {deploy_key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
      env["GIT_SSH_COMMAND"] = ssh_cmd
    
    cmd = ["git", "clone", remote, str(dest)]
    if depth:
      cmd[2:2] = ["--depth", str(depth)]  # insert after "clone" (optional shallow clone)
    
    subprocess.run(cmd, check=True, env=env)
    
  def _build_image(self):
    # What we want to do is to create a docker image that has the repository in it and installs all the required dependencies.
    # In this case that means we need to get the repo from either locally or remotely and then run `uv sync` in the right directory

    with tempfile.TemporaryDirectory() as temp_build_dir:
      os.chdir(temp_build_dir)
      
      # First, let's make the repo in place.
      # This consists of copying the repo from it's origin to a folder named "repo" in the temp directory
      # todo: make work for remote as well
      
      # If local, then it will exist locally.  Else, try to pull it from remote.
      if pathlib.Path(self.source_repo).expanduser().exists():
        shutil.copytree(
          pathlib.Path(self.source_repo).expanduser(),
          "repo"
        )
      else: # Then we are using a remote repo
        self._clone_repo(self.source_repo, depth=1)
      
      # Set up dockerfile
      dockerfile_lines = [
        f"FROM {self.base_image_name}",
        "COPY repo /repo",
        "COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/",
        "WORKDIR /repo",
        "RUN rm -rf .venv",
        "RUN uv sync --locked",
      ]
      
      # Next, we want to save our dockerfile
      with open(os.path.join(temp_build_dir, "Dockerfile"), "w") as dockerfile_fid:
        dockerfile_fid.write('\n'.join(dockerfile_lines))
      
      self.image = self.docker_client.build_image_from_context(
        context_path=temp_build_dir,
        tag="template-grader-image",
        use_cached=True
      )
  
  def grade_submission(self, submission, *args, **kwargs) -> Feedback:
    # Prepare files to copy to docker container
    submission_files = []
    for f in submission.files:
      # Copy all files to the working directory
      submission_files.append((f, os.path.join(self.working_dir, f"programming-assignments/{self.assignment_name}")))
    
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
