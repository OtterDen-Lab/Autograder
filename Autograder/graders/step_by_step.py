"""
Step-by-step grader implementation.

Compares student command sequences against golden commands by executing
them in parallel Docker containers and comparing outputs.
"""
import yaml
from collections import defaultdict
from typing import List

from Autograder.grader import Grader__docker
from Autograder.registry import GraderRegistry
from Autograder.docker_utils import DockerContainerManager
from lms_interface.classes import Feedback

import logging
log = logging.getLogger(__name__)


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
      
      if (not self.outputs_match(stdout_g, stdout_s, stderr_g, stderr_s, rc_g, rc_s) ) and rollback:
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