"""
Factory functions for creating test objects.

These factories create properly-structured objects for testing without
needing actual Canvas or Docker connections.
"""

import io
from typing import Any

from lms_interface.classes import (
    Feedback,
    FileSubmission,
    Student,
    Submission,
    TextSubmission,
)


def make_student(
    name: str = "Test Student",
    user_id: int = 12345,
    inner: Any = None,
) -> Student:
    """Create a Student object for testing."""
    return Student(name=name, user_id=user_id, _inner=inner)


def make_submission(
    student: Student | None = None,
    status: Submission.Status = Submission.Status.UNGRADED,
    user_id: int = 12345,
    name: str = "Test Student",
) -> Submission:
    """Create a basic Submission object for testing."""
    if student is None:
        student = make_student(name=name, user_id=user_id)
    return Submission(student=student, status=status)


def make_text_submission(
    text: str = "This is a test submission with some content.",
    student: Student | None = None,
    status: Submission.Status = Submission.Status.UNGRADED,
    user_id: int = 12345,
    name: str = "Test Student",
) -> TextSubmission:
    """Create a TextSubmission object for testing."""
    if student is None:
        student = make_student(name=name, user_id=user_id)
    return TextSubmission(
        student=student,
        status=status,
        submission_text=text,
    )


def make_file_submission(
    files: list[tuple[str, bytes]] | None = None,
    student: Student | None = None,
    status: Submission.Status = Submission.Status.UNGRADED,
    user_id: int = 12345,
    name: str = "Test Student",
) -> FileSubmission:
    """
    Create a FileSubmission object for testing.

    Args:
        files: List of (filename, content) tuples. Defaults to a single test file.
        student: Student object. Created if not provided.
        status: Submission status.
        user_id: User ID for auto-created student.
        name: Name for auto-created student.

    Returns:
        FileSubmission with files as BytesIO objects.
    """
    if student is None:
        student = make_student(name=name, user_id=user_id)

    if files is None:
        files = [("main.c", b"int main() { return 0; }")]

    submission = FileSubmission(student=student, status=status)

    # Create BytesIO objects with names
    file_buffers = []
    for filename, content in files:
        buffer = io.BytesIO(content)
        buffer.name = filename
        file_buffers.append(buffer)

    submission._files = file_buffers
    return submission


def make_feedback(
    percentage_score: float = 100.0,
    comments: str = "Good work!",
    attachments: list[io.BytesIO] | None = None,
) -> Feedback:
    """Create a Feedback object for testing."""
    return Feedback(
        percentage_score=percentage_score,
        comments=comments,
        attachments=attachments or [],
    )
