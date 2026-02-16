"""
Helper utilities for grading orchestration.
"""

import canvasapi.exceptions
import requests

from Autograder import exceptions as autograder_exceptions


def format_student_label(student, reveal_identity: bool = False) -> str:
    """
    Format a student object into a display label.

    Args:
        student: Student object with name and user_id attributes
        reveal_identity: If True, include Canvas user_id in the label

    Returns:
        Formatted student label string
    """
    if student is None:
        return "Unknown Student"

    name = getattr(student, "name", "Unknown Student")
    user_id = getattr(student, "user_id", None)
    if reveal_identity and user_id is not None and str(user_id) not in str(name):
        return f"{name} [canvas_user_id={user_id}]"
    return str(name)


def format_submission_for_log(submission, reveal_identity: bool = False) -> str:
    """
    Format a submission object for logging.

    Args:
        submission: Submission object with student and feedback attributes
        reveal_identity: If True, include Canvas user_id in the label

    Returns:
        Formatted submission string for logging
    """
    student = getattr(submission, "student", None)
    feedback = getattr(submission, "feedback", None)
    return f"{type(submission).__name__}({format_student_label(student, reveal_identity)} : {feedback})"


def is_lms_exception(error: Exception) -> bool:
    """
    Check if an exception is related to LMS/Canvas operations.

    Args:
        error: The exception to check

    Returns:
        True if the exception is LMS-related
    """
    return isinstance(error, (requests.exceptions.RequestException,
                              canvasapi.exceptions.CanvasException))


def error_hint_for_exception(error: Exception) -> str | None:
    """
    Get a helpful hint message for a given exception type.

    Args:
        error: The exception to get a hint for

    Returns:
        Hint string or None if no hint available
    """
    if isinstance(error, autograder_exceptions.LMSError):
        return ("See documentation/instructor_onboarding.md for Canvas setup and "
                "credentials troubleshooting.")
    if isinstance(error, autograder_exceptions.ConfigurationError):
        return ("Check YAML/config values and refer to "
                "documentation/instructor_onboarding.md.")
    if isinstance(error, autograder_exceptions.AIError):
        return ("Verify provider credentials and model settings. "
                "See README.md AI configuration notes.")
    if isinstance(error, autograder_exceptions.DockerError):
        return ("Verify Docker is running and configured. "
                "See README.md Docker setup/troubleshooting.")
    return None
