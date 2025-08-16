#!env python
import argparse
import contextlib
import fcntl
import os
import pprint
import shutil
import tempfile
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from lms_interface.canvas_interface import CanvasInterface, CanvasCourse, CanvasAssignment
from Autograder.assignment import AssignmentRegistry
from Autograder.grader import GraderRegistry

import logging

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


def parse_args():
  parser = argparse.ArgumentParser()
  
  parser.add_argument("--yaml", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "example_files/programming_assignments.yaml"))
  parser.add_argument("--limit", default=None, type=int)
  parser.add_argument("--regrade", "--do_regrade", dest="do_regrade", action="store_true")
  parser.add_argument("--merge_only", dest="merge_only", action="store_true")
  parser.add_argument("--max_workers", default=None, type=int, help="Maximum number of parallel grading threads (default: number of assignments)")
  
  
  return parser.parse_args()


@contextlib.contextmanager
def working_directory(directory=None):
  """
  Context manager that either:
  1. Creates a temporary directory if no directory is provided
  2. Uses the provided directory if one is given
  
  In both cases, it yields the directory path and handles cleanup only for temp dirs
  Note: In multi-threaded mode, we don't change the working directory to avoid conflicts
  
  Help from Claude: https://claude.ai/share/f5dc7e5a-23ab-4b7d-bef7-e6234587956a
  """
  temp_dir = None
  original_dir = None
  
  thread_id = threading.current_thread().ident
  try:
    if directory is None:
      # Create a temporary directory if none is provided - make it thread-safe
      if threading.current_thread() != threading.main_thread():
        # For worker threads, create a unique temp directory
        temp_base = tempfile.gettempdir()
        temp_name = f"grader_thread_{thread_id}_{uuid.uuid4().hex[:8]}"
        directory = os.path.join(temp_base, temp_name)
        os.makedirs(directory, exist_ok=True)
        temp_dir = directory  # Store path for cleanup, not TemporaryDirectory object
      else:
        # For main thread, use standard TemporaryDirectory
        temp_dir_obj = tempfile.TemporaryDirectory()
        temp_dir = temp_dir_obj  # Store the object for cleanup
        directory = temp_dir_obj.name
    else:
      directory = os.path.expanduser(directory)
      if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    
    # Only change working directory if we're in the main thread to avoid conflicts
    if threading.current_thread() == threading.main_thread():
      original_dir = os.getcwd()
      os.chdir(directory)
    
    # Yield the path of the working directory
    yield directory
  
  finally:
    # Only restore working directory if we changed it
    if original_dir is not None:
      os.chdir(original_dir)
    
    # Clean up the temporary directory if we created one
    if temp_dir is not None:
      if threading.current_thread() != threading.main_thread():
        # For worker threads, manually remove the directory
        try:
          if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        except Exception as e:
          log.warning(f"Failed to cleanup temp directory {temp_dir}: {e}")
      else:
        # For main thread with TemporaryDirectory object
        if hasattr(temp_dir, 'cleanup'):
          temp_dir.cleanup()


def grade_single_assignment(assignment_data):
  """
  Grade a single assignment in a separate thread.
  
  Args:
    assignment_data: Dict containing all data needed to grade one assignment
  
  Returns:
    Dict with grading results and any errors
  """
  thread_id = threading.current_thread().ident
  assignment_id = None  # Initialize for error handling
  try:
    course = assignment_data['course']
    yaml_assignment = assignment_data['yaml_assignment']
    merged_assignment = assignment_data['merged_assignment']
    args = assignment_data['args']
    push_grades = assignment_data['push_grades']
    root_dir = assignment_data['root_dir']
    
    assignment_id = yaml_assignment['id']
    
    # Create assignment object if we have enough information
    lms_assignment = course.get_assignment(assignment_id)
    assignment_grading_kwargs = merged_assignment.get('kwargs', {})
    do_regrade = args.do_regrade
    
    log.info(f"[Thread {thread_id}] Grading assignment \"{lms_assignment.name}\"")
    
    # Get the grader from the registry
    grader_name = merged_assignment.get("grader")
    repo_path = merged_assignment.get('repo_path')
    
    grader = GraderRegistry.create(
      grader_name,
      assignment_path=repo_path
    )
    
    with working_directory(root_dir) as working_dir:
      # Focus on the given assignment
      with AssignmentRegistry.create(
          merged_assignment['kind'],
          lms_assignment=lms_assignment,
          grading_root_dir=working_dir,
          **merged_assignment.get('assignment_kwargs', {})
      ) as grading_assignment:
        
        # If the grader doesn't need preparation, skip the prep step
        if grader.assignment_needs_preparation():
          # For manual grading, we'll skip the interactive prompt in multi-threaded mode
          if grader_name.lower() in ["manual"]:
            log.warning(f"[Thread {thread_id}] Manual grading detected for {lms_assignment.name} - skipping interactive prompts in multi-threaded mode")
          
          grading_assignment.prepare(
            limit=args.limit,
            do_regrade=do_regrade,
            merge_only=args.merge_only,
            **merged_assignment.get("kwargs", {})
          )
          
        grader.grade_assignment(grading_assignment, **assignment_grading_kwargs, merge_only=args.merge_only)
        
        for submission in grading_assignment.submissions:
          log.debug(submission)
        
        if grader.ready_to_finalize:
          if grader_name.lower() in ["manual"]:
            log.warning(f"[Thread {thread_id}] Manual grading finalization for {lms_assignment.name} - skipping interactive prompts in multi-threaded mode")
          grading_assignment.finalize(push=push_grades, merge_only=args.merge_only)
    
    grader.cleanup()
    
    return {
      'success': True,
      'assignment_name': lms_assignment.name,
      'assignment_id': assignment_id,
      'thread_id': thread_id
    }
    
  except Exception as e:
    log.error(f"[Thread {thread_id}] Error grading assignment {assignment_id or 'unknown'}: {e}")
    log.error(f"[Thread {thread_id}] Traceback: {traceback.format_exc()}")
    return {
      'success': False,
      'assignment_id': assignment_id,
      'error': str(e),
      'thread_id': thread_id
    }


def main():
  
  # First, check to make sure we are the only version running, since this can cause problems with docker and canvas otherwise
  lockfile = "/tmp/TeachingTools.grade_assignments.lock"
  lock_fd = open(lockfile, "w")
  try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
  except IOError:
    log.warning("Early exiting because another instance is already running")
    return

  # Otherwise, continue with normal flow
  args = parse_args()
  
  # Load overall YAML
  with open(args.yaml) as fid:
    grader_info = yaml.safe_load(fid)
  
  log.debug(grader_info)
  
  # Pull flags from YAML file that will be applied to all submissions
  use_prod = grader_info.get('prod', False)
  push_grades = grader_info.get('push', False)
  root_dir = grader_info.get('root_dir', None)
  
  # Create the LMS interface
  lms_interface = CanvasInterface(prod=use_prod)
  
  # Collect all assignments to be graded
  assignments_to_grade = []
  
  # Walk through all defined courses, error if we don't have required information
  for yaml_course in grader_info.get('courses', []):
    try:
      course_id = int(yaml_course['id'])
    except KeyError as e:
      log.error("No course ID specified.  Please update.")
      log.error(f"{pprint.pformat(yaml_course)}")
      log.error(e)
      return
    
    # Create course object if found
    course = lms_interface.get_course(course_id)
    
    log.info(f"Preparing to grade Course \"{course.name}\"")
    
    # Get course-level defaults
    course_defaults = yaml_course.get('assignment_defaults', {})
    course_grader = yaml_course.get('grader')
    
    # Walk through assignments in course to grade, error if we don't have required information
    for yaml_assignment in yaml_course.get('assignments', []):
      if yaml_assignment.get('disabled', False):
        continue
      try:
        assignment_id = yaml_assignment['id']
      except KeyError as e:
        log.error("No assignment ID specified.  Please update.")
        log.error(f"{pprint.pformat(yaml_course)}")
        log.error(e)
        return
      
      # Merge course defaults with assignment-specific settings
      merged_assignment = {}
      merged_assignment.update(course_defaults)
      merged_assignment.update(yaml_assignment)
      
      # Merge kwargs specifically (deep merge)
      merged_kwargs = {}
      merged_kwargs.update(course_defaults.get('kwargs', {}))
      merged_kwargs.update(yaml_assignment.get('kwargs', {}))
      merged_assignment['kwargs'] = merged_kwargs
      
      # Use course default grader if not specified at assignment level
      if 'grader' not in merged_assignment:
        merged_assignment['grader'] = course_grader or "Dummy"
      
      # Add this assignment to our list to be graded
      # Create a unique working directory for each assignment to avoid conflicts
      if root_dir:
        # If root_dir is specified, create a unique subdirectory for this assignment
        assignment_root = os.path.join(root_dir, f"assignment_{assignment_id}_{uuid.uuid4().hex[:8]}")
      else:
        # If no root_dir, each thread will create its own temp directory
        assignment_root = None
        
      assignment_data = {
        'course': course,
        'yaml_assignment': yaml_assignment,
        'merged_assignment': merged_assignment,
        'args': args,
        'push_grades': push_grades,
        'root_dir': assignment_root
      }
      assignments_to_grade.append(assignment_data)
  
  log.info(f"Found {len(assignments_to_grade)} assignments to grade")
  
  # Determine number of worker threads
  max_workers = args.max_workers
  if max_workers is None:
    max_workers = min(len(assignments_to_grade), 4)  # Default to 4 or number of assignments, whichever is smaller
  
  log.info(f"Using {max_workers} worker threads for grading")
  
  # Grade assignments in parallel
  results = []
  if len(assignments_to_grade) == 1 or max_workers == 1:
    # Single-threaded execution for single assignment or when max_workers is 1
    log.info("Running in single-threaded mode")
    for assignment_data in assignments_to_grade:
      result = grade_single_assignment(assignment_data)
      results.append(result)
  else:
    # Multi-threaded execution
    log.info("Running in multi-threaded mode")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
      # Submit all assignments for grading
      future_to_assignment = {
        executor.submit(grade_single_assignment, assignment_data): assignment_data
        for assignment_data in assignments_to_grade
      }
      
      # Collect results as they complete
      for future in as_completed(future_to_assignment):
        assignment_data = future_to_assignment[future]
        try:
          result = future.result()
          results.append(result)
          
          if result['success']:
            log.info(f"Successfully graded assignment {result['assignment_name']} (ID: {result['assignment_id']})")
          else:
            log.error(f"Failed to grade assignment {result['assignment_id']}: {result['error']}")
            
        except Exception as exc:
          log.error(f"Assignment {assignment_data['yaml_assignment']['id']} generated an exception: {exc}")
          results.append({
            'success': False,
            'assignment_id': assignment_data['yaml_assignment']['id'],
            'error': str(exc)
          })
  
  # Summary of results
  successful = sum(1 for r in results if r['success'])
  failed = len(results) - successful
  
  log.info(f"Grading completed: {successful} successful, {failed} failed")
  
  if failed > 0:
    log.error("The following assignments failed:")
    for result in results:
      if not result['success']:
        log.error(f"  Assignment {result['assignment_id']}: {result['error']}")
  



if __name__ == "__main__":
  main()