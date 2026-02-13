"""
Tests for submission handling (lms_interface/classes.py).

Covers:
- Submission status parsing
- TextSubmission text handling
- FileSubmission file handling
- Feedback object operations
- Student object handling
"""

import io
import pytest
from unittest.mock import MagicMock, patch
import urllib.request
import urllib.error

from lms_interface.classes import (
    Feedback,
    FileSubmission,
    FileSubmission__Canvas,
    Student,
    Submission,
    TextSubmission,
    TextSubmission__Canvas,
)


class TestSubmissionStatus:
    """Tests for Submission.Status enum and parsing."""

    def test_from_string_graded(self):
        status = Submission.Status.from_string("graded", 95.0)
        assert status == Submission.Status.GRADED

    def test_from_string_submitted(self):
        status = Submission.Status.from_string("submitted", None)
        assert status == Submission.Status.UNGRADED

    def test_from_string_pending_review(self):
        status = Submission.Status.from_string("pending_review", None)
        assert status == Submission.Status.UNGRADED

    def test_from_string_unsubmitted(self):
        status = Submission.Status.from_string("unsubmitted", None)
        assert status == Submission.Status.MISSING

    def test_from_string_unknown_with_none_score_is_ungraded(self):
        # Unknown status with None score is treated as UNGRADED
        # (status not MISSING and score is None triggers UNGRADED)
        status = Submission.Status.from_string("unknown_status", None)
        assert status == Submission.Status.UNGRADED

    def test_from_string_unknown_with_score_is_missing(self):
        # Unknown status with a score defaults to MISSING
        status = Submission.Status.from_string("unknown_status", 0.0)
        assert status == Submission.Status.MISSING

    def test_from_string_submitted_with_score_is_ungraded(self):
        # Edge case: submitted but has a score (shouldn't happen but test behavior)
        status = Submission.Status.from_string("submitted", 50.0)
        assert status == Submission.Status.UNGRADED

    def test_from_string_graded_with_none_score_is_ungraded(self):
        # Graded status but no score yet
        status = Submission.Status.from_string("graded", None)
        assert status == Submission.Status.UNGRADED


class TestStudent:
    """Tests for Student dataclass."""

    def test_student_creation(self):
        student = Student(name="John Doe", user_id=12345, _inner=None)
        assert student.name == "John Doe"
        assert student.user_id == 12345

    def test_student_attribute_access_from_inner(self):
        mock_inner = MagicMock()
        mock_inner.email = "john@example.edu"

        student = Student(name="John Doe", user_id=12345, _inner=mock_inner)
        assert student.email == "john@example.edu"

    def test_student_missing_attribute_raises(self):
        student = Student(name="John Doe", user_id=12345, _inner=None)

        with pytest.raises(AttributeError, match="not found"):
            _ = student.nonexistent_attribute


class TestSubmission:
    """Tests for base Submission class."""

    def test_submission_creation(self):
        student = Student(name="Test", user_id=1, _inner=None)
        submission = Submission(student=student)

        assert submission.student == student
        assert submission.status == Submission.Status.UNGRADED
        assert submission.feedback is None

    def test_submission_set_extra(self):
        submission = Submission(student=None)
        submission.set_extra({"key1": "value1", "key2": 42})

        assert submission.extra_info["key1"] == "value1"
        assert submission.extra_info["key2"] == 42

    def test_submission_str_representation(self):
        student = Student(name="Test Student", user_id=1, _inner=None)
        submission = Submission(student=student)
        submission.feedback = Feedback(percentage_score=85.0, comments="Good work")

        str_repr = str(submission)
        assert "Test Student" in str_repr
        assert "85" in str_repr


class TestTextSubmission:
    """Tests for TextSubmission class."""

    def test_text_submission_creation(self):
        student = Student(name="Test", user_id=1, _inner=None)
        submission = TextSubmission(
            student=student,
            submission_text="This is my essay content."
        )

        assert submission.get_text() == "This is my essay content."

    def test_text_submission_word_count(self):
        submission = TextSubmission(
            student=None,
            submission_text="one two three four five"
        )
        assert submission.get_word_count() == 5

    def test_text_submission_word_count_empty(self):
        submission = TextSubmission(student=None, submission_text="")
        assert submission.get_word_count() == 0

    def test_text_submission_word_count_none(self):
        submission = TextSubmission(student=None, submission_text=None)
        assert submission.get_word_count() == 0

    def test_text_submission_character_count_with_spaces(self):
        submission = TextSubmission(student=None, submission_text="hello world")
        assert submission.get_character_count(include_spaces=True) == 11

    def test_text_submission_character_count_without_spaces(self):
        submission = TextSubmission(student=None, submission_text="hello world")
        assert submission.get_character_count(include_spaces=False) == 10

    def test_text_submission_paragraph_count(self):
        submission = TextSubmission(
            student=None,
            submission_text="First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        )
        assert submission.get_paragraph_count() == 3

    def test_text_submission_paragraph_count_empty(self):
        submission = TextSubmission(student=None, submission_text="")
        assert submission.get_paragraph_count() == 0

    def test_text_submission_str_includes_word_count(self):
        student = Student(name="Test", user_id=1, _inner=None)
        submission = TextSubmission(
            student=student,
            submission_text="one two three"
        )
        str_repr = str(submission)
        assert "3 words" in str_repr


class TestTextSubmissionCanvas:
    """Tests for Canvas-specific text submission."""

    def test_canvas_text_submission_extracts_body(self):
        mock_canvas_data = MagicMock()
        mock_canvas_data.body = "<p>This is HTML content</p>"

        submission = TextSubmission__Canvas(
            student=None,
            canvas_submission_data=mock_canvas_data
        )

        assert submission.get_text() == "<p>This is HTML content</p>"

    def test_canvas_text_submission_handles_none_body(self):
        mock_canvas_data = MagicMock()
        mock_canvas_data.body = None

        submission = TextSubmission__Canvas(
            student=None,
            canvas_submission_data=mock_canvas_data
        )

        assert submission.get_text() == ""

    def test_canvas_text_submission_stores_index(self):
        submission = TextSubmission__Canvas(
            student=None,
            canvas_submission_data=None,
            submission_index=3
        )
        assert submission.submission_index == 3


class TestFileSubmission:
    """Tests for FileSubmission class."""

    def test_file_submission_stores_files(self):
        student = Student(name="Test", user_id=1, _inner=None)
        submission = FileSubmission(student=student)

        file1 = io.BytesIO(b"content1")
        file1.name = "file1.txt"
        file2 = io.BytesIO(b"content2")
        file2.name = "file2.txt"

        submission.files = [file1, file2]

        assert len(submission.files) == 2
        assert submission.files[0].name == "file1.txt"


class TestFileSubmissionCanvas:
    """Tests for Canvas-specific file submission with download handling."""

    def test_canvas_file_submission_lazy_downloads(self):
        """Test that files are only downloaded when accessed."""
        student = Student(name="Test", user_id=1, _inner=None)

        attachments = [
            {"filename": "code.py", "url": "https://example.com/code.py"}
        ]

        submission = FileSubmission__Canvas(
            student=student,
            attachments=attachments
        )

        # Files should not be downloaded yet
        assert submission._files is None

    def test_canvas_file_submission_downloads_on_access(self):
        """Test that accessing files triggers download."""
        student = Student(name="Test", user_id=1, _inner=None)

        attachments = [
            {"filename": "code.py", "url": "https://example.com/code.py"}
        ]

        submission = FileSubmission__Canvas(
            student=student,
            attachments=attachments
        )

        # Mock the download
        mock_response = MagicMock()
        mock_response.read.return_value = b"print('hello')"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.object(urllib.request, 'urlopen', return_value=mock_response):
            files = submission.files

        assert len(files) == 1
        assert files[0].name == "code.py"
        files[0].seek(0)
        assert files[0].read() == b"print('hello')"

    def test_canvas_file_submission_caches_downloads(self):
        """Test that files are only downloaded once."""
        student = Student(name="Test", user_id=1, _inner=None)

        attachments = [
            {"filename": "code.py", "url": "https://example.com/code.py"}
        ]

        submission = FileSubmission__Canvas(
            student=student,
            attachments=attachments
        )

        mock_response = MagicMock()
        mock_response.read.return_value = b"content"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.object(urllib.request, 'urlopen', return_value=mock_response) as mock_urlopen:
            # Access files twice
            _ = submission.files
            _ = submission.files

            # Should only download once
            assert mock_urlopen.call_count == 1

    def test_canvas_file_submission_handles_multiple_files(self):
        """Test downloading multiple attachments."""
        student = Student(name="Test", user_id=1, _inner=None)

        attachments = [
            {"filename": "main.c", "url": "https://example.com/main.c"},
            {"filename": "util.h", "url": "https://example.com/util.h"},
        ]

        submission = FileSubmission__Canvas(
            student=student,
            attachments=attachments
        )

        call_count = [0]

        def mock_urlopen(url):
            call_count[0] += 1
            mock_response = MagicMock()
            mock_response.read.return_value = f"content{call_count[0]}".encode()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        with patch.object(urllib.request, 'urlopen', side_effect=mock_urlopen):
            files = submission.files

        assert len(files) == 2
        assert files[0].name == "main.c"
        assert files[1].name == "util.h"

    def test_canvas_file_submission_no_attachments(self):
        """Test handling when there are no attachments."""
        student = Student(name="Test", user_id=1, _inner=None)

        submission = FileSubmission__Canvas(
            student=student,
            attachments=None
        )

        # Should return None when no attachments
        assert submission.files is None

    def test_canvas_file_submission_stores_index(self):
        submission = FileSubmission__Canvas(
            student=None,
            attachments=None,
            submission_index=5
        )
        assert submission.submission_index == 5


class TestFeedback:
    """Tests for Feedback dataclass."""

    def test_feedback_creation(self):
        feedback = Feedback(percentage_score=95.5, comments="Great work!")
        assert feedback.percentage_score == 95.5
        assert feedback.comments == "Great work!"

    def test_feedback_with_attachments(self):
        attachment = io.BytesIO(b"report content")
        feedback = Feedback(
            percentage_score=80.0,
            comments="See attached report",
            attachments=[attachment]
        )
        assert len(feedback.attachments) == 1

    def test_feedback_str_representation(self):
        feedback = Feedback(percentage_score=85.0, comments="Good job on this assignment!")
        str_repr = str(feedback)
        assert "85" in str_repr
        assert "Good job" in str_repr

    def test_feedback_str_truncates_long_comments(self):
        feedback = Feedback(
            percentage_score=90.0,
            comments="This is a very long comment that should be truncated in the string representation"
        )
        str_repr = str(feedback)
        assert "..." in str_repr

    def test_feedback_comparison_by_score(self):
        feedback1 = Feedback(percentage_score=80.0)
        feedback2 = Feedback(percentage_score=90.0)
        feedback3 = Feedback(percentage_score=80.0)

        assert feedback1 < feedback2
        assert feedback2 > feedback1
        assert feedback1 == feedback3

    def test_feedback_none_score_comparison(self):
        feedback_with_score = Feedback(percentage_score=50.0)
        feedback_none = Feedback(percentage_score=None)

        # None is treated as greater (not graded yet)
        assert feedback_with_score < feedback_none

    def test_feedback_equality_type_check(self):
        feedback = Feedback(percentage_score=80.0)
        assert feedback != "not a feedback object"
        assert feedback.__eq__("not feedback") == NotImplemented
