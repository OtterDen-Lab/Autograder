"""
Argument parsing and logging configuration for the autograder CLI.
"""

import argparse
import logging
import os

from Autograder.registry import GraderRegistry, AssignmentRegistry

log = logging.getLogger(__name__)


def list_graders() -> None:
    """
    Print a table of available graders and their compatible assignment kinds.
    """
    # Ensure modules are loaded
    GraderRegistry.load_premade_modules()
    AssignmentRegistry.load_premade_modules()

    # Collect grader info
    graders = []
    for name, grader_class in sorted(GraderRegistry._registry.items()):
        canonical_name = getattr(grader_class, "_registry_name", name)
        compatible_kinds = getattr(grader_class, "COMPATIBLE_KINDS", set())
        docstring = grader_class.__doc__ or ""
        # Extract first line of docstring as description
        description = docstring.strip().split("\n")[0].strip() if docstring else ""
        # Truncate long descriptions
        if len(description) > 50:
            description = description[:47] + "..."
        graders.append({
            "name": canonical_name,
            "kinds": sorted(compatible_kinds) if compatible_kinds else ["(any)"],
            "description": description,
        })

    # Print header
    print("\nAvailable Graders:")
    print("=" * 80)
    print(f"{'Grader Name':<25} {'Compatible Kinds':<25} {'Description':<30}")
    print("-" * 80)

    # Print each grader
    for grader in graders:
        kinds_str = ", ".join(grader["kinds"])
        print(f"{grader['name']:<25} {kinds_str:<25} {grader['description']:<30}")

    print()

    # Also list available assignment kinds
    print("Available Assignment Kinds:")
    print("-" * 40)
    for name, kind_class in sorted(AssignmentRegistry._registry.items()):
        canonical_name = getattr(kind_class, "_registry_name", name)
        print(f"  {canonical_name}")
    print()


def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments for the grading script.

    Returns:
        Parsed argument namespace
    """
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--yaml",
        default=None,
        help="Path to grading YAML configuration")
    parser.add_argument("--env",
                        default=None,
                        help="Path to the .env file (defaults to ~/.env)")
    parser.add_argument("--limit", default=None, type=int)
    parser.add_argument("--student-id",
                        default=None,
                        type=int,
                        help="Only grade the submission for this Canvas student ID")
    parser.add_argument("--regrade",
                        "--do_regrade",
                        dest="do_regrade",
                        action="store_true")
    parser.add_argument(
        "--max_workers",
        default=None,
        type=int,
        help="Maximum number of parallel grading threads (default: number of assignments)"
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
        "--show-stage-timings",
        action="store_true",
        help="Print stage timing and push aggregate summary at end of run")
    parser.add_argument(
        "--reveal-identity",
        action="store_true",
        help="Include Canvas numeric IDs in logs (requires AUTOGRADER_BREAK_GLASS=1)"
    )
    parser.add_argument(
        "--idempotency-key",
        default=None,
        help="Skip pushing feedback already pushed under this idempotency key")
    parser.add_argument(
        "--idempotency-state-dir",
        default=None,
        help="Directory for idempotency state files (default from config)")
    parser.add_argument(
        "--dump-config",
        action="store_true",
        help="Print effective merged assignment configuration before execution")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and Canvas access, then list assignments without downloading or grading submissions"
    )
    parser.add_argument(
        "--skip-scheduling-check",
        action="store_true",
        help="Ignore schedule due checks and run all configured scheduled assignment types"
    )
    parser.add_argument(
        "--list-graders",
        action="store_true",
        help="List available graders and their compatible assignment kinds, then exit"
    )

    args = parser.parse_args()

    # --list-graders doesn't need a YAML file
    if args.list_graders:
        return args

    if args.yaml is None:
        parser.error("--yaml is required")
    args.yaml = os.path.abspath(os.path.expanduser(args.yaml))
    if not os.path.isfile(args.yaml):
        parser.error(f"--yaml file not found: {args.yaml}")

    if args.env is not None:
        args.env = os.path.abspath(os.path.expanduser(args.env))
        if not os.path.isfile(args.env):
            parser.error(f"--env file not found: {args.env}")

    if args.max_workers is not None and args.max_workers < 1:
        parser.error("--max_workers must be >= 1")
    if args.student_id is not None and args.student_id < 1:
        parser.error("--student-id must be >= 1")

    return args


def configure_logging(debug: bool) -> None:
    """
    Configure logging levels for the autograder and external libraries.

    Args:
        debug: If True, enable debug-level logging
    """
    level = logging.DEBUG if debug else logging.INFO
    logging.getLogger("Autograder").setLevel(level)
    logging.getLogger(__name__).setLevel(level)
    external_level = logging.INFO if debug else logging.WARNING
    for logger_name in (
            "httpx",
            "httpcore",
            "openai",
            "anthropic",
            "urllib3",
            "docker",
    ):
        logging.getLogger(logger_name).setLevel(external_level)
