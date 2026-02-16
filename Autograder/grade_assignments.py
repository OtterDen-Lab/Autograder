#!env python
"""
Main entry point for the autograder CLI.

This module coordinates the grading process by:
1. Parsing command-line arguments
2. Loading and validating YAML configuration
3. Collecting assignments to grade from Canvas
4. Executing grading in parallel
5. Reporting results and sending notifications

The heavy lifting is delegated to specialized modules:
- cli: Argument parsing and validation
- orchestration: Grading execution and stages
- reporting: Result summaries and notifications
"""

import json
import logging
import os
from typing import Dict, List

import yaml

from lms_interface.canvas_interface import CanvasInterface
from Autograder.docker_utils import DockerClient
from Autograder.config_models import (
    RunConfig,
    AssignmentRunRequest,
    normalize_grader_settings,
    parse_run_config,
)
from Autograder import exceptions as autograder_exceptions

# Import from new modular structure
from Autograder.cli import (
    parse_args,
    configure_logging,
    resolve_reveal_identity,
    resolve_idempotency_settings,
)
from Autograder.orchestration import (
    execute_grading,
    ensure_single_instance,
    is_lms_exception,
)
from Autograder.reporting import (
    print_results_summary,
    print_stage_timing_summary,
    write_run_report,
    send_slack_run_summary,
    collect_push_failure_lines,
    summarize_stage_contracts,
    PrepareStageResult,
    GradeStageResult,
    PublishStageResult,
)
from Autograder.orchestration import (
    run_prepare_stage,
    run_grade_stage,
    run_publish_stage,
    grade_single_assignment,
    format_student_label,
    format_submission_for_log,
    error_hint_for_exception,
)
from Autograder.cli.validators import resolve_records_dir

# Re-export for backwards compatibility with tests
from Autograder.grader import GraderRegistry
from Autograder.assignment import AssignmentRegistry
import requests

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def load_and_validate_config(yaml_path: str) -> RunConfig:
    """
    Load YAML configuration and validate into a typed contract.

    Args:
        yaml_path: Path to the YAML configuration file

    Returns:
        Parsed run configuration object

    Raises:
        SystemExit: If the configuration is invalid
    """
    with open(yaml_path) as fid:
        raw_config = yaml.safe_load(fid)
    try:
        run_config = parse_run_config(raw_config)
    except ValueError as e:
        raise SystemExit(
            f"Invalid config file '{yaml_path}': {e}. "
            "See documentation/instructor_onboarding.md for supported schema/examples."
        ) from e

    log.debug(f"run_config: {run_config}")
    return run_config


def collect_assignments_to_grade(config: RunConfig,
                                 args) -> List[AssignmentRunRequest]:
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
    idempotency_key, idempotency_state_dir = resolve_idempotency_settings(
        args, config)
    log.info(
        f"Using privacy_mode={config.privacy_mode}, reveal_identity={reveal_identity}"
    )
    if idempotency_key:
        log.info(
            f"Idempotency enabled with key '{idempotency_key}' (state dir: {idempotency_state_dir})"
        )

    # Create the LMS interface
    try:
        lms_interface = CanvasInterface(prod=config.prod,
                                        env_path=env_path,
                                        privacy_mode=config.privacy_mode,
                                        reveal_identity=reveal_identity)
    except Exception as e:
        if is_lms_exception(e) or isinstance(e, ValueError):
            raise autograder_exceptions.LMSError(
                "Failed to initialize Canvas interface. "
                "Verify .env credentials (CANVAS_API_URL/CANVAS_API_KEY), network connectivity, and API token validity."
            ) from e
        raise

    assignments_to_grade = []

    for course_config in config.courses:
        try:
            course = lms_interface.get_course(course_config.id)
        except Exception as e:
            if is_lms_exception(e) or isinstance(e, ValueError):
                raise autograder_exceptions.LMSError(
                    f"Failed to load Canvas course id={course_config.id}. "
                    "Verify course ID, enrollment/access permissions, and API credentials."
                ) from e
            raise
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
                settings = normalize_grader_settings(
                    type_config.grader,
                    settings,
                    (f"course[{course_config.id}] group[{group.type_name}] "
                     f"assignment[{assignment.id}]"))

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
                        idempotency_key=idempotency_key,
                        idempotency_state_dir=idempotency_state_dir,
                    ))

    return assignments_to_grade


def build_dump_config_payload(
        config: RunConfig,
        assignments_to_grade: List[AssignmentRunRequest],
        args) -> Dict:
    """
    Build a payload representing the effective configuration.

    Args:
        config: Run configuration
        assignments_to_grade: Collected assignments
        args: Command line arguments

    Returns:
        Dictionary payload suitable for JSON serialization
    """
    assignments_payload = []
    for assignment in assignments_to_grade:
        course_name = assignment.course_name
        if not course_name and assignment.course is not None:
            course_name = getattr(assignment.course, "name", None)

        assignments_payload.append({
            "course_name": course_name,
            "assignment_id": assignment.assignment_id,
            "assignment_name": assignment.assignment_name,
            "assignment_type": assignment.assignment_type,
            "assignment_kind": assignment.assignment_kind,
            "grader_name": assignment.grader_name,
            "repo_path": assignment.repo_path,
            "push_grades": assignment.push_grades,
            "privacy_mode": assignment.privacy_mode,
            "reveal_identity": assignment.reveal_identity,
            "idempotency_key": assignment.idempotency_key,
            "idempotency_state_dir": assignment.idempotency_state_dir,
            "slack_channel": assignment.slack_channel,
            "settings": assignment.settings,
        })

    return {
        "yaml_path": args.yaml,
        "run": {
            "prod": config.prod,
            "push": config.push,
            "privacy_mode": config.privacy_mode,
            "reveal_identity": bool(assignments_to_grade[0].reveal_identity)
            if assignments_to_grade else bool(config.reveal_identity),
            "idempotency_key": (assignments_to_grade[0].idempotency_key
                                if assignments_to_grade else config.idempotency_key),
            "idempotency_state_dir":
            (assignments_to_grade[0].idempotency_state_dir if assignments_to_grade
             else config.idempotency_state_dir),
            "assignment_count": len(assignments_to_grade),
        },
        "assignments": assignments_payload,
    }


def dump_effective_config(config: RunConfig,
                          assignments_to_grade: List[AssignmentRunRequest],
                          args) -> None:
    """Print the effective merged configuration as JSON."""
    payload = build_dump_config_payload(config, assignments_to_grade, args)
    print(json.dumps(payload, indent=2))


def print_dry_run_summary(
        assignments_to_grade: List[AssignmentRunRequest]) -> None:
    """Print a summary of what would be graded in dry-run mode."""
    log.info(
        "Dry-run mode enabled: validating config and Canvas access only. No submissions will be downloaded, graded, or pushed."
    )
    log.info(
        f"Dry-run plan includes {len(assignments_to_grade)} assignment(s).")
    for assignment in assignments_to_grade:
        assignment_label = (assignment.assignment_name or assignment.repo_path
                            or f"ID {assignment.assignment_id}")
        log.info(
            f"  {assignment.course_name} / {assignment_label} "
            f"(ID: {assignment.assignment_id}, kind={assignment.assignment_kind}, "
            f"grader={assignment.grader_name}, push={assignment.push_grades})")


def main() -> int:
    """
    Main entry point for the grading script.

    Coordinates the entire grading process using a clean, modular approach.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    args = parse_args()
    configure_logging(args.debug)

    exit_code = 0
    with ensure_single_instance():
        try:
            config = load_and_validate_config(args.yaml)

            assignments_to_grade = collect_assignments_to_grade(config, args)
            if args.dump_config:
                dump_effective_config(config, assignments_to_grade, args)
            if args.dry_run:
                print_dry_run_summary(assignments_to_grade)
                return 0
            results = execute_grading(assignments_to_grade, args)

            print_results_summary(results)
            if args.show_stage_timings:
                print_stage_timing_summary(results)
            write_run_report(results, args)
            send_slack_run_summary(results, args, config)

            if any(not r['success'] for r in results):
                exit_code = 1
        except autograder_exceptions.AutograderError as e:
            log.error(e)
            exit_code = 1
        finally:
            # Always perform global Docker cleanup at the end
            log.info("Performing final Docker cleanup...")
            DockerClient.cleanup()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
