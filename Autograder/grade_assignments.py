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

from Autograder.lms_interface.canvas_interface import CanvasInterface, CanvasCourse, CanvasAssignment, CanvasQuiz
from Autograder.assignment import AssignmentRegistry
from Autograder.grader import GraderRegistry
from Autograder.registry import TypeRegistry
from Autograder.docker_utils import DockerClient, DockerContainer

import logging

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser()
  subparsers = parser.add_subparsers(dest="command", help="Available commands")

  # TEST command - for testing text submission flow
  test_parser = subparsers.add_parser(
    "TEST", help="Test text submission flow with learning-logs.yaml")
  test_parser.add_argument("--limit", default=None, type=int)

  # Keep existing arguments for backward compatibility when no subcommand is used
  parser.add_argument("--yaml",
                      default=os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "example_files/programming_assignments.yaml"))
  parser.add_argument("--limit", default=None, type=int)
  parser.add_argument("--regrade",
                      "--do_regrade",
                      dest="do_regrade",
                      action="store_true")
  parser.add_argument("--merge_only", dest="merge_only", action="store_true")
  parser.add_argument(
    "--max_workers",
    default=None,
    type=int,
    help=
    "Maximum number of parallel grading threads (default: number of assignments)"
  )
  parser.add_argument("--test",
                      action="store_true",
                      help="Only downloads for test student")

  args = parser.parse_args()

  # Handle TEST command
  if args.command == "TEST":
    args.yaml = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "example_files/learning-logs.yaml")
    args.do_regrade = True
    args.test = True
    args.max_workers = 1

  return args


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
  except IOError as e:
    log.warning("Early exiting because another instance is already running")
    log.warning(e)
    raise SystemExit(0)
  finally:
    try:
      lock_fd.close()
    except Exception:
      pass


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

    assignment_id = yaml_assignment['id']
    assignment_type = merged_assignment.get(
      'type', 'assignment')  # Default to assignment

    # Create assignment or quiz object based on type
    if assignment_type.lower() == 'quiz':
      lms_assignment = course.get_quiz(assignment_id)
      log.info(f"[Thread {thread_id}] Grading quiz \"{lms_assignment.name}\"")
    else:
      lms_assignment = course.get_assignment(assignment_id)
      log.info(
        f"[Thread {thread_id}] Grading assignment \"{lms_assignment.name}\"")

    # Get unified settings (new format uses 'settings', legacy uses 'kwargs')
    settings = merged_assignment.get('settings') or merged_assignment.get(
      'kwargs', {})

    # Add runtime context to settings
    settings = settings.copy()  # Don't modify the original
    settings["course_name"] = assignment_data.get("course_name")
    settings["slack_channel"] = assignment_data.get("slack_channel")

    # Handle prefer_anthropic from both new and legacy formats
    if 'prefer_anthropic' in merged_assignment and 'prefer_anthropic' not in settings:
      settings["prefer_anthropic"] = merged_assignment.get("prefer_anthropic")

    do_regrade = args.do_regrade

    # Get the grader from the registry
    grader_name = merged_assignment.get("grader")
    repo_path = merged_assignment.get('repo_path')

    # Create grader with assignment identifier for better logging
    assignment_name = settings.get("assignment_name", lms_assignment.name.split()[0])
    del settings["assignment_name"]
    grader = GraderRegistry.create(grader_name,
                                   assignment_path=repo_path,
                                   assignment_name=assignment_name,
                                   **settings)

    with tempfile.TemporaryDirectory() as working_dir:
      # Focus on the given assignment
      # For new format, settings are already complete; for legacy, use assignment_kwargs
      assignment_creation_kwargs = merged_assignment.get(
        'assignment_kwargs', {})

      with AssignmentRegistry.create(
          merged_assignment['kind'],
          lms_assignment=lms_assignment,
          grading_root_dir=working_dir,
          **assignment_creation_kwargs) as grading_assignment:

        # If the grader doesn't need preparation, skip the prep step
        if grader.assignment_needs_preparation():
          # For manual grading, we'll skip the interactive prompt in multi-threaded mode
          if grader_name.lower() in ["manual"]:
            log.warning(
              f"[Thread {thread_id}] Manual grading detected for {lms_assignment.name} - skipping interactive prompts in multi-threaded mode"
            )

          grading_assignment.prepare(limit=args.limit,
                                     do_regrade=do_regrade,
                                     merge_only=args.merge_only,
                                     test=args.test,
                                     **settings)

        grader.grade_assignment(grading_assignment,
                                **settings,
                                merge_only=args.merge_only,
                                do_regrade=do_regrade)

        for submission in grading_assignment.submissions:
          log.info(f"{submission}")

        if grader.ready_to_finalize:
          if grader_name.lower() in ["manual"]:
            log.warning(
              f"[Thread {thread_id}] Manual grading finalization for {lms_assignment.name} - skipping interactive prompts in multi-threaded mode"
            )
          # Check for record retention setting (check both settings and top-level)
          record_retention = settings.get(
            'record_retention') or merged_assignment.get(
              'record_retention', False)
          if record_retention:
            # Determine where to save records
            records_dir = settings.get('records_dir') or merged_assignment.get(
              'records_dir')
            if records_dir is None:
              # Default to 'records' directory in the main project directory
              records_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "records")
            else:
              # Expand user paths (like ~/records)
              records_dir = os.path.expanduser(records_dir)

            grading_assignment.finalize(push=push_grades,
                                        merge_only=args.merge_only,
                                        record_retention=record_retention,
                                        records_dir=records_dir)
          else:
            grading_assignment.finalize(push=push_grades,
                                        merge_only=args.merge_only)

    return {
      'success': True,
      'assignment_name': lms_assignment.name,
      'assignment_id': assignment_id,
      'thread_id': thread_id
    }

  except Exception as e:
    log.error(
      f"[Thread {thread_id}] Error grading assignment {assignment_id or 'unknown'}: {e}"
    )
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
        log.debug(
          f"[Thread {thread_id}] Cleanup completed for assignment {assignment_id or 'unknown'}"
        )
    except Exception as cleanup_error:
      log.warning(
        f"[Thread {thread_id}] Error during cleanup: {cleanup_error}")


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

  log.debug(f"grader_info: {grader_info}")
  return grader_info


def normalize_assignment(assignment) -> Dict:
  """
  Normalize assignment format to dict with 'id' key.

  Supports:
  - Simple ID: 506883
  - Dict format: {id: 506883, repo_path: PA1, ...}

  Args:
    assignment: Either an int/string ID or a dict

  Returns:
    Dict with 'id' key and any additional fields
  """
  if isinstance(assignment, (int, str)):
    return {'id': int(assignment)}
  elif isinstance(assignment, dict):
    if 'id' not in assignment:
      raise ValueError(f"Assignment dict must have 'id' key: {assignment}")
    return assignment
  else:
    raise ValueError(f"Invalid assignment format: {assignment}")


def create_assignment_data(course,
                           course_name,
                           yaml_assignment: Dict,
                           merged_assignment: Dict,
                           args: argparse.Namespace,
                           push_grades: bool,
                           slack_channel: Optional[str] = None) -> Dict:
  """
  Create assignment data structure for grading.

  Args:
    course: Canvas course object
    yaml_assignment: Assignment configuration from YAML
    merged_assignment: Merged assignment configuration
    args: Command line arguments
    push_grades: Whether to push grades to LMS

  Returns:
    Dictionary containing assignment data for grading
  """
  assignment_id = yaml_assignment['id']

  return {
    'course': course,
    'course_name': course_name,
    'yaml_assignment': yaml_assignment,
    'merged_assignment': merged_assignment,
    'args': args,
    'push_grades': push_grades,
    'slack_channel': slack_channel,
  }


def collect_assignments_to_grade(config: Dict,
                                 args: argparse.Namespace) -> List[Dict]:
  """
  Process courses and collect all assignments that need grading.

  Supports both legacy format (assignment_defaults + assignments) and
  new format (assignment_types + assignment_groups).

  Args:
    config: Loaded YAML configuration
    args: Command line arguments

  Returns:
    List of assignment data dictionaries ready for grading
  """
  # Pull flags from YAML file that will be applied to all submissions
  use_prod = config.get('prod', False)
  push_grades = config.get('push', False)

  # Load assignment types if present (new format)
  if 'assignment_types' in config:
    TypeRegistry.load_from_yaml(config)
    log.info("Using new configuration format with assignment_types")

  # Create the LMS interface
  lms_interface = CanvasInterface(prod=use_prod)

  assignments_to_grade = []

  # Walk through all defined courses
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

    # Check if using new format (assignment_groups) or legacy format (assignments)
    if 'assignment_groups' in yaml_course:
      # NEW FORMAT: Process assignment groups
      assignments_to_grade.extend(
        _process_assignment_groups(course, yaml_course, args, push_grades))
    else:
      # LEGACY FORMAT: Process assignments directly
      assignments_to_grade.extend(
        _process_legacy_assignments(course, yaml_course, args, push_grades))

  return assignments_to_grade


def _process_assignment_groups(course, yaml_course: Dict,
                               args: argparse.Namespace,
                               push_grades: bool) -> List[Dict]:
  """
  Process assignment_groups from new config format.

  Args:
    course: Canvas course object
    yaml_course: Course configuration from YAML
    args: Command line arguments
    push_grades: Whether to push grades

  Returns:
    List of assignment data dicts
  """
  assignments = []
  course_name = yaml_course.get('name')
  course_slack_channel = yaml_course.get('slack_channel')

  # Extract course-level settings (anything that's not a reserved key)
  reserved_keys = {
    'name', 'id', 'assignment_groups', 'assignment_defaults', 'assignments',
    'grader'
  }
  course_settings = {
    k: v
    for k, v in yaml_course.items() if k not in reserved_keys
  }

  for group in yaml_course.get('assignment_groups', []):
    group_type = group.get('type')
    if not group_type:
      log.error(f"Assignment group missing 'type': {group}")
      continue

    # Get type configuration
    try:
      type_config = TypeRegistry.get_type_config(group_type)
    except ValueError as e:
      log.error(f"Error getting type config: {e}")
      continue

    # Extract group-level settings (anything that's not a reserved key)
    group_reserved_keys = {'name', 'type', 'assignments', 'settings'}
    group_settings = {
      k: v
      for k, v in group.items() if k not in group_reserved_keys
    }

    # Also merge in explicit 'settings' dict if present
    if 'settings' in group:
      group_settings.update(group['settings'])

    # Process each assignment in the group
    for assignment in group.get('assignments', []):
      # Normalize assignment format
      normalized_assignment = normalize_assignment(assignment)

      # Extract assignment-level settings (anything except 'id')
      assignment_settings = {
        k: v
        for k, v in normalized_assignment.items() if k != 'id'
      }

      # Merge settings: type defaults -> course -> group -> assignment
      merged_settings = TypeRegistry.merge_settings(group_type,
                                                    course_settings,
                                                    group_settings,
                                                    assignment_settings)

      # Build merged_assignment dict with all info needed for grading
      merged_assignment = {
        'kind': type_config['kind'],
        'grader': type_config.get('grader', 'Dummy'),
        'settings': merged_settings,
        # Legacy compatibility - pass settings as kwargs too
        'kwargs': merged_settings.copy()
      }

      # Add any fields from normalized_assignment (like repo_path)
      for key in normalized_assignment:
        if key != 'id':
          merged_assignment[key] = normalized_assignment[key]

      assignment_data = create_assignment_data(course, course_name,
                                               normalized_assignment,
                                               merged_assignment, args,
                                               push_grades,
                                               course_slack_channel)
      assignments.append(assignment_data)

  return assignments


def _process_legacy_assignments(course, yaml_course: Dict,
                                args: argparse.Namespace,
                                push_grades: bool) -> List[Dict]:
  """
  Process assignments from legacy config format.

  Args:
    course: Canvas course object
    yaml_course: Course configuration from YAML
    args: Command line arguments
    push_grades: Whether to push grades

  Returns:
    List of assignment data dicts
  """
  assignments = []

  # Get course-level defaults
  course_defaults = yaml_course.get('assignment_defaults', {})
  course_grader = yaml_course.get('grader')

  # Walk through assignments in course to grade
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
    assignment_data = create_assignment_data(course, yaml_course.get("name"),
                                             yaml_assignment,
                                             merged_assignment, args,
                                             push_grades,
                                             yaml_course.get("slack_channel"))
    assignments.append(assignment_data)

  return assignments


def execute_grading(assignments_to_grade: List[Dict],
                    args: argparse.Namespace) -> List[Dict]:
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
    max_workers = min(
      len(assignments_to_grade),
      4)  # Default to 4 or number of assignments, whichever is smaller

  log.info(f"Using {max_workers} worker threads for grading")

  # Grade assignments in parallel
  results = []
  # Multi-threaded execution
  log.info("Running in multi-threaded mode")
  with ThreadPoolExecutor(max_workers=max_workers) as executor:
    # Submit all assignments for grading
    future_to_assignment = {
      executor.submit(grade_single_assignment, assignment_data):
      assignment_data
      for assignment_data in assignments_to_grade
    }

    # Collect results as they complete
    for future in as_completed(future_to_assignment):
      assignment_data = future_to_assignment[future]
      try:
        result = future.result()
        results.append(result)

        if result['success']:
          log.info(
            f"Successfully graded assignment {result['assignment_name']} (ID: {result['assignment_id']})"
          )
        else:
          log.error(
            f"Failed to grade assignment {result['assignment_id']}: {result['error']}"
          )

      except Exception as exc:
        log.error(
          f"Assignment {assignment_data['yaml_assignment']['id']} generated an exception: {exc}"
        )
        results.append({
          'success':
          False,
          'assignment_id':
          assignment_data['yaml_assignment']['id'],
          'error':
          str(exc)
        })

  return results


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
  args = parse_args()

  with ensure_single_instance():
    try:
      config = load_and_validate_config(args.yaml)

      assignments_to_grade = collect_assignments_to_grade(config, args)
      results = execute_grading(assignments_to_grade, args)

      print_results_summary(results)
    finally:
      # Always perform global Docker cleanup at the end
      log.info("Performing final Docker cleanup...")
      DockerClient.cleanup()


if __name__ == "__main__":
  main()
