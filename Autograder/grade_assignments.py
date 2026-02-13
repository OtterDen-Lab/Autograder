#!env python
import argparse
import contextlib
import fcntl
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List
import json
from datetime import datetime
import requests

import yaml

from lms_interface.canvas_interface import CanvasInterface
from Autograder.assignment import AssignmentRegistry
from Autograder.grader import GraderRegistry
from Autograder.docker_utils import DockerClient
from Autograder.config_models import RunConfig, AssignmentRunRequest, parse_run_config

import logging

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser()
  subparsers = parser.add_subparsers(dest="command", help="Available commands")

  # TEST command - for testing text submission flow
  test_parser = subparsers.add_parser(
    "TEST", help="Test text submission flow with learning-logs.yaml")
  test_parser.add_argument("--limit", default=None, type=int)

  parser.add_argument(
    "--yaml",
    default=None,
    help="Path to grading YAML configuration (required unless using TEST)")
  parser.add_argument("--env",
                      default=None,
                      help="Path to the .env file (defaults to ~/.env)")
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
  parser.add_argument("--report",
                      default=None,
                      help="Write a JSON grading report to the given path")
  parser.add_argument(
    "--error-slack-channel",
    default=None,
    help="Slack channel ID for run-level error notifications")
  parser.add_argument("--debug",
                      action="store_true",
                      help="Enable debug logging")
  parser.add_argument(
    "--reveal-identity",
    action="store_true",
    help="Include Canvas numeric IDs in logs (requires AUTOGRADER_BREAK_GLASS=1)"
  )

  args = parser.parse_args()

  # Handle TEST command
  if args.command == "TEST":
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    args.yaml = os.path.join(repo_root, "example_files", "learning-logs.yaml")
    args.do_regrade = True
    args.test = True
    args.max_workers = 1

  if args.yaml is None:
    parser.error("--yaml is required (or use the TEST command)")
  args.yaml = os.path.abspath(os.path.expanduser(args.yaml))
  if not os.path.isfile(args.yaml):
    parser.error(f"--yaml file not found: {args.yaml}")

  if args.env is not None:
    args.env = os.path.abspath(os.path.expanduser(args.env))
    if not os.path.isfile(args.env):
      parser.error(f"--env file not found: {args.env}")

  return args


def configure_logging(debug: bool) -> None:
  level = logging.DEBUG if debug else logging.INFO
  logging.getLogger("Autograder").setLevel(level)
  logging.getLogger(__name__).setLevel(level)


def resolve_reveal_identity(args: argparse.Namespace,
                            config: RunConfig) -> bool:
  requested = bool(getattr(args, "reveal_identity", False)
                   or config.reveal_identity)
  if not requested:
    return False

  if os.getenv("AUTOGRADER_BREAK_GLASS") != "1":
    raise SystemExit(
      "Identity reveal requested but AUTOGRADER_BREAK_GLASS is not set to 1.")

  log.warning(
    "Break-glass identity reveal is enabled; Canvas numeric IDs may appear in logs."
  )
  return True


def format_student_label(student, reveal_identity: bool = False) -> str:
  if student is None:
    return "Unknown Student"

  name = getattr(student, "name", "Unknown Student")
  user_id = getattr(student, "user_id", None)
  if reveal_identity and user_id is not None and str(user_id) not in str(name):
    return f"{name} [canvas_user_id={user_id}]"
  return str(name)


def format_submission_for_log(submission, reveal_identity: bool = False) -> str:
  student = getattr(submission, "student", None)
  feedback = getattr(submission, "feedback", None)
  return f"{type(submission).__name__}({format_student_label(student, reveal_identity)} : {feedback})"


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


def grade_single_assignment(assignment_data: AssignmentRunRequest) -> Dict:
  """
  Grade a single assignment in a separate thread.

  Args:
    assignment_data: Assignment request containing all data needed to grade one assignment

  Returns:
    Dict with grading results and any errors
  """
  thread_id = threading.current_thread().ident
  assignment_id = None  # Initialize for error handling
  assignment_name = None
  course_name = assignment_data.course_name
  try:
    course = assignment_data.course
    args = assignment_data.args
    push_grades = assignment_data.push_grades

    assignment_id = assignment_data.assignment_id
    assignment_type = assignment_data.assignment_type
    grader_name = assignment_data.grader_name
    assignment_kind = assignment_data.assignment_kind

    # Explicitly block unsupported/vestigial flows.
    if assignment_type.lower() == 'quiz' or str(grader_name).lower(
    ) == "quizgrader":
      raise NotImplementedError(
        "Quiz grading flow is intentionally disabled in this build.")
    if assignment_kind in {"Exam", "ExamCST231", "QuizAssignment"}:
      raise NotImplementedError(
        f"Assignment kind '{assignment_kind}' is intentionally disabled in this build."
      )

    lms_assignment = course.get_assignment(assignment_id)
    assignment_name = lms_assignment.name
    log.info(f"[Thread {thread_id}] Grading assignment \"{assignment_name}\"")

    settings = assignment_data.settings

    # Add runtime context to settings
    settings = settings.copy()  # Don't modify the original
    settings["course_name"] = assignment_data.course_name
    settings["slack_channel"] = assignment_data.slack_channel

    do_regrade = args.do_regrade

    repo_path = assignment_data.repo_path

    # Create grader with assignment identifier for better logging.
    # Prefer explicit assignment_name, then repo_path (e.g., "HW3"),
    # then full LMS assignment name.
    assignment_name = assignment_data.assignment_name or settings.get(
      "assignment_name")
    if isinstance(assignment_name, str):
      assignment_name = assignment_name.strip()
    if not assignment_name:
      assignment_name = repo_path or lms_assignment.name
    grader = GraderRegistry.create(grader_name,
                                   assignment_path=repo_path,
                                   assignment_name=assignment_name,
                                   **settings)

    with AssignmentRegistry.create(
        assignment_kind,
        lms_assignment=lms_assignment,
        grading_root_dir=None) as grading_assignment:

      # If the grader doesn't need preparation, skip the prep step
      if grader.assignment_needs_preparation():
        grading_assignment.prepare(limit=args.limit,
                                   do_regrade=do_regrade,
                                   merge_only=args.merge_only,
                                   test=args.test,
                                   **settings)

      if not grading_assignment.submissions:
        log.info(
          f"[Thread {thread_id}] No submissions for {lms_assignment.name}; skipping grading."
        )
        return {
          'success': True,
          'assignment_name': assignment_name,
          'course_name': course_name,
          'assignment_id': assignment_id,
          'thread_id': thread_id
        }

      with grader:
        grader.grade_assignment(grading_assignment,
                                **settings,
                                reveal_identity=assignment_data.reveal_identity,
                                privacy_mode=assignment_data.privacy_mode,
                                merge_only=args.merge_only,
                                do_regrade=do_regrade)

        for submission in grading_assignment.submissions:
          log.info(
            format_submission_for_log(
              submission, reveal_identity=assignment_data.reveal_identity))

        if grader.ready_to_finalize:
          record_retention = bool(settings.get('record_retention'))
          if record_retention:
            # Determine where to save records
            records_dir = settings.get('records_dir')
            if not records_dir:
              raise ValueError(
                "record_retention=true requires an explicit records_dir in config"
              )
            # Expand user paths (like ~/records)
            records_dir = os.path.abspath(os.path.expanduser(records_dir))

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
      'assignment_name': assignment_name,
      'course_name': course_name,
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


def load_and_validate_config(yaml_path: str) -> RunConfig:
  """
  Load YAML configuration and validate into a typed contract.

  Args:
    yaml_path: Path to the YAML configuration file

  Returns:
    Parsed run configuration object
  """
  with open(yaml_path) as fid:
    raw_config = yaml.safe_load(fid)
  try:
    run_config = parse_run_config(raw_config)
  except ValueError as e:
    raise SystemExit(f"Invalid config: {e}") from e

  log.debug(f"run_config: {run_config}")
  return run_config


def collect_assignments_to_grade(config: RunConfig,
                                 args: argparse.Namespace
                                 ) -> List[AssignmentRunRequest]:
  """
  Process courses and collect typed assignment run requests.

  Args:
    config: Loaded YAML configuration
    args: Command line arguments

  Returns:
    List of assignment run requests ready for grading
  """
  env_path = args.env or os.path.join(os.path.expanduser("~"), ".env")
  reveal_identity = resolve_reveal_identity(args, config)
  log.info(
    f"Using privacy_mode={config.privacy_mode}, reveal_identity={reveal_identity}"
  )

  # Create the LMS interface
  lms_interface = CanvasInterface(prod=config.prod,
                                  env_path=env_path,
                                  privacy_mode=config.privacy_mode,
                                  reveal_identity=reveal_identity)

  assignments_to_grade = []

  for course_config in config.courses:
    course = lms_interface.get_course(course_config.id)
    log.info(f"Preparing to grade Course \"{course.name}\"")

    for group in course_config.assignment_groups:
      type_config = config.assignment_types[group.type_name]

      for assignment in group.assignments:
        if assignment.disabled:
          continue

        settings = type_config.settings.copy()
        settings.update(course_config.settings)
        settings.update(group.settings)
        settings.update(assignment.settings)

        assignments_to_grade.append(
          AssignmentRunRequest(
            course=course,
            course_name=course_config.name,
            assignment_id=assignment.id,
            assignment_type=group.type_name,
            assignment_kind=type_config.kind,
            grader_name=type_config.grader,
            settings=settings,
            repo_path=assignment.repo_path,
            assignment_name=assignment.assignment_name,
            args=args,
            push_grades=config.push,
            slack_channel=course_config.slack_channel,
            reveal_identity=reveal_identity,
            privacy_mode=config.privacy_mode,
          ))

  return assignments_to_grade


def execute_grading(assignments_to_grade: List[AssignmentRunRequest],
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
  if not assignments_to_grade:
    log.warning("No assignments found to grade for the provided configuration.")
    return []

  # Determine number of worker threads
  max_workers = args.max_workers
  if max_workers is None:
    max_workers = min(
      len(assignments_to_grade),
      4)  # Default to 4 or number of assignments, whichever is smaller
  if max_workers < 1:
    max_workers = 1

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
          f"Assignment {assignment_data.assignment_id} generated an exception: {exc}"
        )
        results.append({
          'success':
          False,
          'assignment_id':
          assignment_data.assignment_id,
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


def send_slack_run_summary(results: List[Dict], args: argparse.Namespace,
                           config: RunConfig) -> None:
  reporting_config = config.reporting
  slack_token = os.getenv("SLACK_BOT_TOKEN")
  slack_channel = (args.error_slack_channel
                   or reporting_config.get("slack_channel")
                   or config.error_slack_channel
                   or os.getenv("ERROR_SLACK_CHANNEL"))

  if not slack_token or not slack_channel:
    log.warning(
      "Slack run summary not configured (missing SLACK_BOT_TOKEN or channel)."
    )
    return

  successful = sum(1 for r in results if r['success'])
  failed = len(results) - successful
  notify_on = reporting_config.get("notify_on", "failures").lower()
  if notify_on == "failures" and failed == 0:
    return

  failure_lines = []
  for result in results:
    if not result['success']:
      assignment_label = (result.get('assignment_name')
                          or f"ID {result.get('assignment_id')}")
      course_label = result.get('course_name') or "Unknown Course"
      error_msg = result.get('error', 'Unknown error')
      failure_lines.append(
        f"- {course_label} / {assignment_label}: {error_msg}")

  message_lines = [
    f":warning: Grading run completed with {failed} failure(s) ({successful} succeeded).",
    f"Config: `{args.yaml}`",
  ]
  if failure_lines:
    message_lines.append("Failures:")
    message_lines.extend(failure_lines)

  try:
    response = requests.post(
      "https://slack.com/api/chat.postMessage",
      headers={"Authorization": f"Bearer {slack_token}"},
      json={
        "channel": slack_channel,
        "text": "\n".join(message_lines),
        "mrkdwn": True,
        "unfurl_links": False,
        "unfurl_media": False
      },
      timeout=10)

    if not response.json().get('ok'):
      log.warning(
        f"Slack run summary failed: {response.json().get('error')}")
    else:
      log.info("Slack run summary sent successfully")
  except Exception as e:
    log.warning(f"Failed to send Slack run summary: {e}")


def write_run_report(results: List[Dict], args: argparse.Namespace) -> None:
  if not args.report:
    return

  report_dir = os.path.dirname(os.path.abspath(args.report))
  if report_dir and not os.path.exists(report_dir):
    os.makedirs(report_dir, exist_ok=True)

  successful = sum(1 for r in results if r['success'])
  failed = len(results) - successful

  report_payload = {
    "run_started_at": datetime.now().isoformat(timespec="seconds"),
    "yaml_path": args.yaml,
    "successful": successful,
    "failed": failed,
    "results": results,
  }

  with open(args.report, "w", encoding="utf-8") as report_file:
    json.dump(report_payload, report_file, indent=2)


def main() -> int:
  """
  Main entry point for the grading script.

  Coordinates the entire grading process using a clean, modular approach.
  """
  args = parse_args()
  configure_logging(args.debug)

  exit_code = 0
  with ensure_single_instance():
    try:
      config = load_and_validate_config(args.yaml)

      assignments_to_grade = collect_assignments_to_grade(config, args)
      results = execute_grading(assignments_to_grade, args)

      print_results_summary(results)
      write_run_report(results, args)
      send_slack_run_summary(results, args, config)

      if any(not r['success'] for r in results):
        exit_code = 1
    finally:
      # Always perform global Docker cleanup at the end
      log.info("Performing final Docker cleanup...")
      DockerClient.cleanup()

  return exit_code


if __name__ == "__main__":
  raise SystemExit(main())
