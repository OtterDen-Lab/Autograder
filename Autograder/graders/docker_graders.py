"""
Docker-based grader implementations.

Contains configurable Docker graders that can run arbitrary grading scripts
in containerized environments.
"""
import yaml
from collections import defaultdict
from typing import Tuple

from Autograder.grader import Grader__docker
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback

import logging
log = logging.getLogger(__name__)


@GraderRegistry.register("docker-configurable")
class Grader__docker_configurable(Grader__docker):
  """
  Configurable Docker grader that can run custom grading scripts.
  
  Supports:
  - Custom grading scripts or command sequences
  - Additional package installs
  - Custom Dockerfiles
  - File copying to containers
  """
  
  def __init__(self, 
               grading_script=None, 
               grading_commands=None, 
               working_dir="/tmp/grading", 
               additional_installs=None, 
               dockerfile_text=None, 
               dockercompose_text=None,
               additional_files=None, 
               *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.grading_script = grading_script
    self.grading_commands = grading_commands if grading_commands else []
    self.working_dir = working_dir
    self.additional_installs = additional_installs if additional_installs else []
    self.dockerfile_text = dockerfile_text
    self.dockercompose_text = dockercompose_text
    self.additional_files = additional_files if additional_files else []
    
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
      self.image = self._build_custom_image()
  
  def _build_custom_image(self):
    """Build a custom Docker image with additional installs and files"""
    
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
    
    return self.build_docker_image(dockerfile_content)
  
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
    
    return rc, stdout, stderr
  
  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    rc, stdout, stderr = execution_results
    
    # Decode stdout if it's bytes
    stdout_str = stdout.decode() if isinstance(stdout, bytes) else stdout
    stderr_str = stderr.decode() if isinstance(stderr, bytes) else stderr
    
    # Try to parse YAML output from stdout
    score = 0.0
    feedback_text = ""
    
    try:
      # Look for YAML in stdout
      yaml_output = yaml.safe_load(stdout_str)
      if isinstance(yaml_output, dict):
        score = float(yaml_output.get('score', 0.0))
        feedback_text = yaml_output.get('feedback', '')
    except (yaml.YAMLError, ValueError, TypeError) as e:
      log.warning(f"Failed to parse YAML from grading output: {e}")
      # If YAML parsing fails, use default score and include raw output
      feedback_text = "Failed to parse grading results"
    
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


