"""
CST334-specific grader implementations.

Contains graders tailored for CST334 programming assignments,
including both regular and online course variants.
"""
import json
import os
import textwrap
from typing import Tuple

from Autograder.grader import Grader__docker
from Autograder.graders.docker_graders import Grader__docker_configurable
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback

import logging
log = logging.getLogger(__name__)

# Constants
GRADING_TIMEOUT_SECONDS = 120  # Timeout for grading commands
DEFAULT_NUM_REPEATS = 3  # Number of times to repeat grading for best result


@GraderRegistry.register("CST334")
class Grader__CST334(Grader__docker_configurable):
  """
  Grader for CST334 programming assignments.
  
  Uses a specialized Docker image with CST334-specific tools and
  grading scripts from the course repository.
  """
  
  def __init__(self, assignment_path, git_repo="https://www.github.com/samogden/CST334-assignments.git", *args, **kwargs):
    # Always need to clone the assignments repo to get the grading scripts
    dockerfile_text = f"""FROM samogden/cst334
RUN git clone {git_repo} /tmp/grading/
WORKDIR /tmp/grading
CMD ["/bin/bash"]"""
    
    # Set working directory to the specific assignment folder
    assignment_working_dir = f"/tmp/grading/programming-assignments/{assignment_path}"
    
    super().__init__(
      dockerfile_text=dockerfile_text,
      grading_commands=[f"timeout {GRADING_TIMEOUT_SECONDS} python ../../helpers/grader.py --output /tmp/results.json"],
      working_dir=assignment_working_dir,
      *args,
      **kwargs
    )
    self.assignment_path = assignment_path
  
  def check_for_trickery(self, submission) -> bool:
    def contains_string(search_str, f) -> bool:
      try:
        if search_str.encode() in f.read():
          return True
        else:
          return False
      finally:
        f.seek(0)
      
    for f in submission.files:
      if contains_string("exit(0)", f):
        return True
    return False
  
  @staticmethod
  def build_feedback(results_dict, score=None) -> str:
    feedback_strs = [
      "##############",
      "## FEEDBACK ##",
      "##############",
      "",
    ]
    
    if score is not None:
      feedback_strs.extend([
        f"Score reported: {score} points",
        ""
      ])
    
    if "overall_feedback" in results_dict:
      feedback_strs.extend([
        "## Overall Feedback ##",
        results_dict["overall_feedback"],
        "\n\n"
      ])
    
    feedback_strs.extend([
      "## Unit Tests ##",
    ])
    if "suites" in results_dict:
      for suite_name in results_dict["suites"].keys():
        
        # Separate regular tests from RESERVE_ tests
        passed_tests = results_dict["suites"][suite_name]["PASSED"]
        regular_passed = [test for test in passed_tests if not test.startswith("RESERVE_")]
        reserve_passed = [test for test in passed_tests if test.startswith("RESERVE_")]
        
        if len(regular_passed) > 0:
          feedback_strs.extend([
            f"SUITE: {suite_name}",
            "  * passed:",
          ])
          feedback_strs.extend([
            textwrap.indent('\n'.join(regular_passed), '    '),
            ""
          ])
        
        if len(reserve_passed) > 0:
          feedback_strs.extend([
            f"SUITE: {suite_name} (Enhanced Tests)",
            "  * passed:",
          ])
          feedback_strs.extend([
            textwrap.indent('\n'.join(reserve_passed), '    '),
            ""
          ])
        
        if len(results_dict["suites"][suite_name]["FAILED"]) > 0:
          feedback_strs.extend([
            f"SUITE: {suite_name}",
            "  * failed:",
          ])
          feedback_strs.extend([
            textwrap.indent('\n'.join(results_dict["suites"][suite_name]["FAILED"]), '    '),
            ""
          ])
      feedback_strs.extend([
        "################",
        "",
      ])
    
    if "build_logs" in results_dict:
      feedback_strs.extend([
        "## Build Logs ##",
      ])
      feedback_strs.extend([
        "Build Logs:",
        ''.join(results_dict["build_logs"])[1:-1].encode('utf-8').decode('unicode_escape')
      ])
      feedback_strs.extend([
        "################",
      ])
    
    if "lint_logs" in results_dict:
      feedback_strs.extend([
        "## Lint Logs ##",
        f"Lint success: {results_dict['lint_success']}\n"
      ])
      feedback_strs.extend([
        "Lint Logs:",
        ''.join(results_dict["lint_logs"])[1:-1].encode('utf-8').decode('unicode_escape')
      ])
      feedback_strs.extend([
        "################",
      ])
    
    return '\n'.join(feedback_strs)
  
  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    """Override docker-configurable to use JSON instead of YAML parsing"""
    rc, stdout, stderr = execution_results
    
    # For CST334, we expect results in a JSON file (original behavior)
    results = self.read_file_from_container("/tmp/results.json")
    if results is None:
      return Feedback(
        score=0,
        comments="Something went wrong during grading, likely a timeout. Please check your assignment for infinite loops and/or contact your professor."
      )
    
    results_dict = json.loads(results)
    if "lint_success" in results_dict and results_dict["lint_success"] and "lint_bonus" in kwargs:
      results_dict["score"] += kwargs["lint_bonus"]
    
    return Feedback(
      score=results_dict["score"],
      comments=self.build_feedback(results_dict, results_dict["score"])
    )
  
  def grade_submission(self, submission, *args, **kwargs) -> Feedback:
    path_to_programming_assignment = os.path.join("programming-assignments", self.assignment_path)
    
    # Use CST334's original file copying logic (your code)
    # This is more sophisticated than docker-configurable's simple copying
    submission_files = []
    for f in submission.files:
      log.debug(f"f: {f.__class__} {f.name}")
      # Your original logic: .c files go to src/, others go to include/
      target_dir = f"/tmp/grading/{path_to_programming_assignment}/{'src' if f.name.endswith('.c') else 'include'}"
      submission_files.append((f, target_dir))
    
    # Check for trickery using your original detection logic
    if self.check_for_trickery(submission):
      return Feedback(
        score=0.0,
        comments="It was detected that you might have been trying to game the scoring via exiting early from a unit test. Please contact your professor if you think this was in error."
      )
    
    # Multiple grading runs with aggregation (preserves original CST334 behavior)
    all_feedback = []
    
    for i in range(kwargs.get("num_repeats", DEFAULT_NUM_REPEATS)):
      # Use parent docker infrastructure but with our custom file copying
      all_feedback.append(
        super(Grader__docker_configurable, self).grade_submission(
          submission,
          files_to_copy=submission_files,
          path_to_programming_assignment=path_to_programming_assignment,
          lint_bonus=1,
          *args, **kwargs
        )
      )
      
    # Select best feedback and add aggregated results (original CST334 logic)
    feedback = min(all_feedback)
    
    full_feedback = "##################\n"
    full_feedback += "## All results: ##\n"
    for i, result in enumerate(all_feedback):
      full_feedback += f"test {i}: {result.score} points\n"
    full_feedback += "##################\n"

    feedback.comments += f"\n\n\n{full_feedback}"
    return feedback


@GraderRegistry.register("CST334online")
class Grader__CST334online(Grader__CST334):
  """
  Grader for CST334 online course assignments.
  
  Uses the same logic as the regular CST334 grader but pulls
  from a different repository.
  """
  def __init__(self, assignment_path, *args, **kwargs):
    # Just use different git repo - leverage the parent's git_repo parameter
    super().__init__(
      assignment_path=assignment_path,
      git_repo="https://www.github.com/samogden/CST334-assignments-online.git",
      *args,
      **kwargs
    )