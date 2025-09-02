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
from typing import Optional, Dict, List

import yaml

from lms_interface.canvas_interface import CanvasInterface, CanvasCourse, CanvasAssignment
from Autograder.assignment import AssignmentRegistry
from Autograder.grader import GraderRegistry

import logging

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser()
  
  parser.add_argument("--yaml", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "example_files/programming_assignments.yaml"))
  parser.add_argument("--limit", default=None, type=int)
  parser.add_argument("--regrade", "--do_regrade", dest="do_regrade", action="store_true")
  parser.add_argument("--merge_only", dest="merge_only", action="store_true")
  parser.add_argument("--max_workers", default=None, type=int, help="Maximum number of parallel grading threads (default: number of assignments)")
  
  parser.add_argument("--test", action="store_true", help="Only downloads for test student")
  
  return parser.parse_args()


@contextlib.contextmanager
def working_directory(directory: Optional[str] = None):
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


def grade_single_assignment(assignment_data: Dict) -> Dict:
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
    
    # Create grader with assignment identifier for better logging
    assignment_name = lms_assignment.name
    grader = GraderRegistry.create(
      grader_name,
      assignment_path=repo_path,
      assignment_name=assignment_name,
      **assignment_grading_kwargs
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
            test=args.test,
            **merged_assignment.get("kwargs", {})
          )
          
        grader.grade_assignment(grading_assignment, **assignment_grading_kwargs, merge_only=args.merge_only, do_regrade=do_regrade)
        
        for submission in grading_assignment.submissions:
          log.info(f"{submission}")
        
        if grader.ready_to_finalize:
          if grader_name.lower() in ["manual"]:
            log.warning(f"[Thread {thread_id}] Manual grading finalization for {lms_assignment.name} - skipping interactive prompts in multi-threaded mode")
          grading_assignment.finalize(push=push_grades, merge_only=args.merge_only)
    
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
  finally:
    # Ensure cleanup always happens, even if errors occurred
    try:
      if 'grader' in locals():
        grader.cleanup()
        log.debug(f"[Thread {thread_id}] Cleanup completed for assignment {assignment_id or 'unknown'}")
    except Exception as cleanup_error:
      log.warning(f"[Thread {thread_id}] Error during cleanup: {cleanup_error}")


@contextlib.contextmanager
def ensure_single_instance():
  """
  Context manager for file locking to prevent multiple instances.
  
  Ensures only one grading process runs at a time to avoid conflicts
  with Docker and Canvas operations.
  """
  lockfile = "/tmp/TeachingTools.grade_assignments.lock"
  lock_fd = open(lockfile, "w")
  try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield
  except IOError:
    log.warning("Early exiting because another instance is already running")
    raise SystemExit(0)
  finally:
    try:
      lock_fd.close()
    except Exception:
      pass


def load_and_validate_config(yaml_path: str) -> Dict:
  """
  Load YAML configuration and extract global settings.
  
  Args:
    yaml_path: Path to the YAML configuration file
    
  Returns:
    Dictionary containing the loaded configuration
  """
  with open(yaml_path) as fid:
    grader_info = yaml.safe_load(fid)
  
  log.debug(grader_info)
  return grader_info


def create_assignment_data(course, yaml_assignment: Dict, merged_assignment: Dict, 
                          args: argparse.Namespace, push_grades: bool, root_dir: Optional[str]) -> Dict:
  """
  Create assignment data structure for grading.
  
  Args:
    course: Canvas course object
    yaml_assignment: Assignment configuration from YAML
    merged_assignment: Merged assignment configuration
    args: Command line arguments
    push_grades: Whether to push grades to LMS
    root_dir: Root directory for grading operations
    
  Returns:
    Dictionary containing assignment data for grading
  """
  assignment_id = yaml_assignment['id']
  
  # Create a unique working directory for each assignment to avoid conflicts
  if root_dir:
    # If root_dir is specified, create a unique subdirectory for this assignment
    assignment_root = os.path.join(root_dir, f"assignment_{assignment_id}_{uuid.uuid4().hex[:8]}")
  else:
    # If no root_dir, each thread will create its own temp directory
    assignment_root = None
    
  return {
    'course': course,
    'yaml_assignment': yaml_assignment,
    'merged_assignment': merged_assignment,
    'args': args,
    'push_grades': push_grades,
    'root_dir': assignment_root
  }


def collect_assignments_to_grade(config: Dict, args: argparse.Namespace) -> List[Dict]:
  """
  Process courses and collect all assignments that need grading.
  
  Args:
    config: Loaded YAML configuration
    args: Command line arguments
    
  Returns:
    List of assignment data dictionaries ready for grading
  """
  # Pull flags from YAML file that will be applied to all submissions
  use_prod = config.get('prod', False)
  push_grades = config.get('push', False)
  root_dir = config.get('root_dir', None)
  
  # Create the LMS interface
  lms_interface = CanvasInterface(prod=use_prod)
  
  assignments_to_grade = []
  
  # Walk through all defined courses, error if we don't have required information
  for yaml_course in config.get('courses', []):
    try:
      course_id = int(yaml_course['id'])
    except KeyError as e:
      log.error("No course ID specified. Please update.")
      log.error(f"{pprint.pformat(yaml_course)}")
      log.error(e)
      raise SystemExit(1)
    
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
        log.error("No assignment ID specified. Please update.")
        log.error(f"{pprint.pformat(yaml_course)}")
        log.error(e)
        raise SystemExit(1)
      
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
      assignment_data = create_assignment_data(
        course, yaml_assignment, merged_assignment, args, push_grades, root_dir
      )
      assignments_to_grade.append(assignment_data)
  
  return assignments_to_grade


def execute_grading(assignments_to_grade: List[Dict], args: argparse.Namespace) -> List[Dict]:
  """
  Execute grading either single-threaded or multi-threaded.
  
  Args:
    assignments_to_grade: List of assignment data for grading
    args: Command line arguments
    
  Returns:
    List of grading results
  """
  log.info(f"Found {len(assignments_to_grade)} assignments to grade")
  
  # Determine number of worker threads
  max_workers = args.max_workers
  if max_workers is None:
    max_workers = min(len(assignments_to_grade), 6)  # Default to 4 or number of assignments, whichever is smaller
  
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
  
  return results


def cleanup_all_docker_resources() -> None:
  """
  Global cleanup function to remove any remaining Docker containers and images
  created by grading operations.
  """
  try:
    # Import docker here to avoid issues when docker is not available
    import docker
    client = docker.from_env()
    
    # Clean up any remaining grader containers
    containers = client.containers.list(all=True, filters={'name': 'grader'})
    if containers:
      log.info(f"Cleaning up {len(containers)} remaining grader containers")
      for container in containers:
        try:
          container.stop(timeout=1)
          container.remove(force=True)
          log.debug(f"Cleaned up container: {container.name}")
        except Exception as e:
          log.debug(f"Failed to clean up container {container.name}: {e}")
    
    # Clean up grading images
    grading_images = client.images.list(filters={'dangling': False})
    cleaned_images = 0
    for image in grading_images:
      if image.tags:
        for tag in image.tags:
          if tag.startswith('grading:'):
            try:
              client.images.remove(image.id, force=True)
              log.debug(f"Cleaned up grading image: {tag}")
              cleaned_images += 1
              break
            except Exception as e:
              log.debug(f"Failed to clean up grading image {tag}: {e}")
    
    if cleaned_images > 0:
      log.info(f"Cleaned up {cleaned_images} grading images")
      
    # Clean up dangling images
    dangling_images = client.images.list(filters={'dangling': True})
    if dangling_images:
      for image in dangling_images:
        try:
          client.images.remove(image.id, force=True)
        except Exception:
          pass
      log.info(f"Cleaned up {len(dangling_images)} dangling images")
    
  except ImportError:
    log.debug("Docker not available, skipping Docker cleanup")
  except Exception as e:
    log.warning(f"Failed to perform global Docker cleanup: {e}")


def print_results_summary(results: List[Dict]) -> None:
  """
  Print summary of grading results.
  
  Args:
    results: List of grading result dictionaries
  """
  successful = sum(1 for r in results if r['success'])
  failed = len(results) - successful
  
  log.info(f"Grading completed: {successful} successful, {failed} failed")
  
  if failed > 0:
    log.error("The following assignments failed:")
    for result in results:
      if not result['success']:
        log.error(f"  Assignment {result['assignment_id']}: {result['error']}")


def main() -> None:
  """
  Main entry point for the grading script.
  
  Coordinates the entire grading process using a clean, modular approach.
  """
  with ensure_single_instance():
    try:
      args = parse_args()
      config = load_and_validate_config(args.yaml)
      
      assignments_to_grade = collect_assignments_to_grade(config, args)
      results = execute_grading(assignments_to_grade, args)
      
      print_results_summary(results)
    finally:
      # Always perform global Docker cleanup at the end
      log.info("Performing final Docker cleanup...")
      cleanup_all_docker_resources()


if __name__ == "__main__":
  main()

