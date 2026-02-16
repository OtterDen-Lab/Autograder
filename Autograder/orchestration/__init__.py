"""
Orchestration components for the autograder.

This module provides grading execution and stage management.
"""

from .stages import run_prepare_stage, run_grade_stage, run_publish_stage
from .executor import (
    execute_grading,
    grade_single_assignment,
    ensure_single_instance,
)
from .helpers import (
    format_student_label,
    format_submission_for_log,
    is_lms_exception,
    error_hint_for_exception,
)

__all__ = [
    # Stages
    "run_prepare_stage",
    "run_grade_stage",
    "run_publish_stage",
    # Executor
    "execute_grading",
    "grade_single_assignment",
    "ensure_single_instance",
    # Helpers
    "format_student_label",
    "format_submission_for_log",
    "is_lms_exception",
    "error_hint_for_exception",
]
