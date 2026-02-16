"""
Grading execution and coordination.

This module handles the main grading loop, including thread management
and single-instance locking.
"""

import contextlib
import fcntl
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Dict, List

from Autograder import exceptions as autograder_exceptions
from Autograder.assignment import AssignmentRegistry
from Autograder.config_models import AssignmentRunRequest, get_active_grader_compatibility
from Autograder.grader import GraderRegistry
from Autograder.cli.validators import resolve_records_dir
from .stages import run_prepare_stage, run_grade_stage, run_publish_stage
from .helpers import is_lms_exception, error_hint_for_exception

log = logging.getLogger(__name__)


@contextlib.contextmanager
def ensure_single_instance():
    """
    Context manager for file locking to prevent multiple instances.

    Ensures only one grading process runs at a time to avoid conflicts
    with Docker and Canvas operations.

    Yields:
        None

    Raises:
        SystemExit: If another grading instance is already running
    """
    lockfile = "/tmp/TeachingTools.grade_assignments.lock"
    lock_fd = open(lockfile, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except IOError as e:
        log.info("Early exiting because another grading instance is already running")
        log.debug(f"Lock acquisition details: {e}")
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
    assignment_id = None
    assignment_name = None
    course_name = assignment_data.course_name
    try:
        course = assignment_data.course
        args = assignment_data.args
        push_grades = assignment_data.push_grades

        assignment_id = assignment_data.assignment_id
        grader_name = assignment_data.grader_name
        assignment_kind = assignment_data.assignment_kind

        # Validate grader compatibility
        try:
            try:
                active_assignment_kinds, active_graders_by_kind = (
                    get_active_grader_compatibility(strict=True))
            except TypeError:
                active_assignment_kinds, active_graders_by_kind = (
                    get_active_grader_compatibility())
        except ValueError as e:
            raise autograder_exceptions.ConfigurationError(str(e)) from e
        if assignment_kind not in active_assignment_kinds:
            supported = ", ".join(sorted(active_assignment_kinds))
            raise autograder_exceptions.ConfigurationError(
                f"Assignment kind '{assignment_kind}' is not supported in this build. "
                f"Supported kinds: {supported}")
        allowed_graders = active_graders_by_kind.get(assignment_kind, set())
        if grader_name not in allowed_graders:
            allowed = ", ".join(sorted(allowed_graders)) or "(none)"
            raise autograder_exceptions.ConfigurationError(
                f"Grader '{grader_name}' is not supported for kind '{assignment_kind}'. "
                f"Allowed graders: {allowed}")

        # Load Canvas assignment
        try:
            lms_assignment = course.get_assignment(assignment_id)
        except Exception as e:
            if is_lms_exception(e) or isinstance(e, (ValueError, AttributeError)):
                raise autograder_exceptions.LMSError(
                    f"Failed to load Canvas assignment id={assignment_id} for course "
                    f"'{course_name}': {e}. Verify course/assignment IDs and Canvas API access. "
                    "If Canvas is under maintenance, retry after maintenance completes."
                ) from e
            raise
        if lms_assignment is None:
            raise autograder_exceptions.LMSError(
                f"Canvas assignment id={assignment_id} was not found for course "
                f"'{course_name}'. Verify the assignment ID in your YAML config."
            )
        try:
            lms_assignment_name = lms_assignment.name
        except AttributeError as e:
            raise autograder_exceptions.LMSError(
                f"Canvas assignment id={assignment_id} for course '{course_name}' is missing "
                f"required metadata ('name'): {e}. This can happen during Canvas maintenance."
            ) from e
        assignment_name = lms_assignment_name
        log.info(f"[Thread {thread_id}] Grading assignment \"{lms_assignment_name}\"")

        settings = assignment_data.settings

        # Add runtime context to settings
        settings = settings.copy()
        settings["course_name"] = assignment_data.course_name
        settings["slack_channel"] = assignment_data.slack_channel
        settings["assignment_kind"] = assignment_kind

        do_regrade = args.do_regrade
        record_retention = bool(settings.get('record_retention'))
        if record_retention:
            try:
                settings["records_dir"] = resolve_records_dir(settings.get("records_dir"))
            except ValueError as e:
                raise autograder_exceptions.ConfigurationError(
                    f"Invalid records configuration for assignment {assignment_id} "
                    f"('{assignment_name or 'unknown'}'): {e}") from e
        elif settings.get("records_dir") is not None and str(grader_name).lower(
        ) in {"textsubmissiongrader", "weeklystudynotesgrader"}:
            try:
                settings["records_dir"] = resolve_records_dir(settings.get("records_dir"))
            except ValueError as e:
                raise autograder_exceptions.ConfigurationError(
                    f"Invalid records configuration for assignment {assignment_id} "
                    f"('{assignment_name or 'unknown'}'): {e}") from e

        repo_path = assignment_data.repo_path

        # Create grader with assignment identifier for better logging
        assignment_name = assignment_data.assignment_name or settings.get(
            "assignment_name")
        if isinstance(assignment_name, str):
            assignment_name = assignment_name.strip()
        if not assignment_name:
            assignment_name = repo_path or lms_assignment_name
        grader = GraderRegistry.create(grader_name,
                                        assignment_path=repo_path,
                                        assignment_name=assignment_name,
                                        **settings)

        with AssignmentRegistry.create(
                assignment_kind,
                lms_assignment=lms_assignment,
                grading_root_dir=None) as grading_assignment:

            # Run prepare stage
            prepare_started = time.perf_counter()
            prepare_result = run_prepare_stage(grader, grading_assignment, args,
                                               settings, do_regrade)
            prepare_result.duration_ms = int(
                (time.perf_counter() - prepare_started) * 1000)
            stage_contract = {
                "prepare": asdict(prepare_result),
                "grade": None,
                "publish": None,
            }

            if not prepare_result.has_submissions:
                log.info(
                    f"[Thread {thread_id}] No submissions for {lms_assignment_name}; skipping grading."
                )
                return {
                    'success': True,
                    'assignment_name': lms_assignment_name,
                    'course_name': course_name,
                    'assignment_id': assignment_id,
                    'thread_id': thread_id,
                    'stage_contract': stage_contract,
                }

            with grader:
                # Run grade stage
                grade_started = time.perf_counter()
                grade_result = run_grade_stage(grader, grading_assignment, settings,
                                               assignment_data, args, do_regrade)
                grade_result.duration_ms = int(
                    (time.perf_counter() - grade_started) * 1000)
                stage_contract["grade"] = asdict(grade_result)

                # Run publish stage
                publish_started = time.perf_counter()
                publish_result = run_publish_stage(grader, grading_assignment, args,
                                                   push_grades, assignment_data,
                                                   record_retention, settings)
                publish_result.duration_ms = int(
                    (time.perf_counter() - publish_started) * 1000)
                stage_contract["publish"] = asdict(publish_result)

        return {
            'success': True,
            'assignment_name': lms_assignment_name,
            'assignment_id': assignment_id,
            'thread_id': thread_id,
            'course_name': course_name,
            'finalize_summary': publish_result.finalize_summary,
            'stage_contract': stage_contract,
        }

    except Exception as e:
        hint = error_hint_for_exception(e)
        error_message = str(e)
        if hint:
            error_message = f"{error_message} {hint}"
        log.exception(
            f"[Thread {thread_id}] Error grading assignment {assignment_id or 'unknown'}: {e}"
        )
        return {
            'success': False,
            'assignment_id': assignment_id,
            'assignment_name': assignment_name,
            'course_name': course_name,
            'error': error_message,
            'error_type': type(e).__name__,
            'thread_id': thread_id
        }
    finally:
        try:
            if 'grader' in locals():
                grader.cleanup()
                log.debug(
                    f"[Thread {thread_id}] Cleanup completed for assignment {assignment_id or 'unknown'}"
                )
        except Exception as cleanup_error:
            log.warning(
                f"[Thread {thread_id}] Error during cleanup: {cleanup_error}")


def execute_grading(assignments_to_grade: List[AssignmentRunRequest],
                    args) -> List[Dict]:
    """
    Execute grading for multiple assignments using thread pool.

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
        max_workers = min(len(assignments_to_grade), 4)
    if max_workers < 1:
        max_workers = 1

    log.info(f"Using {max_workers} worker threads for grading")

    # Grade assignments in parallel
    results = []
    log.info("Running in multi-threaded mode")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_assignment = {
            executor.submit(grade_single_assignment, assignment_data): assignment_data
            for assignment_data in assignments_to_grade
        }

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
                hint = error_hint_for_exception(exc)
                error_message = str(exc)
                if hint:
                    error_message = f"{error_message} {hint}"
                results.append({
                    'success': False,
                    'assignment_id': assignment_data.assignment_id,
                    'error_type': type(exc).__name__,
                    'error': error_message
                })

    return results
