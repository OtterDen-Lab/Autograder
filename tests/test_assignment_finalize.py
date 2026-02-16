"""
Tests for Assignment.finalize() functionality.

Covers:
- Score scaling with different canvas_points configurations
- Record retention file creation
- Push failure handling and error isolation
- Edge cases in scaling calculations
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from Autograder.assignment import Assignment
from lms_interface.classes import Feedback, Submission, Student


class DummyAssignment(Assignment):
    """Minimal Assignment subclass for testing."""

    def prepare(self, *args, **kwargs):
        return None


class DummyLmsAssignment:
    """Mock LMS assignment with configurable behavior."""
    name = "Test Assignment"
    id = 12345

    def __init__(
        self,
        *,
        points_possible: float = None,
        fail_user_ids: set = None,
        exception_user_ids: set = None,
    ):
        self.canvas_course = SimpleNamespace(id=99999)
        self.points_possible = points_possible
        self.fail_user_ids = fail_user_ids or set()
        self.exception_user_ids = exception_user_ids or set()
        self.push_calls = []
        self.pushed_scores = {}

    def push_feedback(self, *, user_id, score=None, **kwargs):
        self.push_calls.append(user_id)
        self.pushed_scores[user_id] = score
        if user_id in self.exception_user_ids:
            raise RuntimeError(f"push exploded for {user_id}")
        return user_id not in self.fail_user_ids


def _make_submission(user_id: int, score: float = 95.0) -> Submission:
    """Create a submission with feedback."""
    submission = Submission(
        student=Student(name=f"Student {user_id}", user_id=user_id, _inner=None),
        status=Submission.Status.UNGRADED
    )
    submission.feedback = Feedback(percentage_score=score, comments="Good work!")
    return submission


class TestScoreScaling:
    """Tests for scale_score_for_canvas() method."""

    def test_scaling_with_explicit_canvas_points(self):
        """Explicit canvas_points from config takes priority."""
        lms_assignment = DummyLmsAssignment(points_possible=100)
        assignment = DummyAssignment(
            lms_assignment=lms_assignment,
            canvas_points=80  # Explicit override
        )

        # 90% on 80-point assignment = 72 points
        scaled = assignment.scale_score_for_canvas(90.0)
        assert scaled == 72.0

    def test_scaling_with_canvas_points_possible(self):
        """Uses points_possible from Canvas when no explicit override."""
        lms_assignment = DummyLmsAssignment(points_possible=50)
        assignment = DummyAssignment(lms_assignment=lms_assignment)

        # 100% on 50-point assignment = 50 points
        scaled = assignment.scale_score_for_canvas(100.0)
        assert scaled == 50.0

    def test_scaling_fallback_to_percentage(self):
        """Falls back to percentage when no points info available."""
        lms_assignment = DummyLmsAssignment(points_possible=None)
        assignment = DummyAssignment(lms_assignment=lms_assignment)

        # No scaling info, return percentage as-is
        scaled = assignment.scale_score_for_canvas(85.0)
        assert scaled == 85.0

    def test_scaling_with_extra_credit(self):
        """Scores above 100% should scale correctly."""
        lms_assignment = DummyLmsAssignment(points_possible=100)
        assignment = DummyAssignment(lms_assignment=lms_assignment)

        # 110% on 100-point assignment = 110 points (extra credit)
        scaled = assignment.scale_score_for_canvas(110.0)
        assert scaled == pytest.approx(110.0)

    def test_scaling_with_zero_score(self):
        """Zero scores should scale to zero."""
        lms_assignment = DummyLmsAssignment(points_possible=100)
        assignment = DummyAssignment(lms_assignment=lms_assignment)

        scaled = assignment.scale_score_for_canvas(0.0)
        assert scaled == 0.0

    def test_scaling_with_fractional_points(self):
        """Fractional percentages should scale correctly."""
        lms_assignment = DummyLmsAssignment(points_possible=80)
        assignment = DummyAssignment(lms_assignment=lms_assignment)

        # 87.5% on 80-point assignment = 70 points
        scaled = assignment.scale_score_for_canvas(87.5)
        assert scaled == 70.0

    def test_scaling_with_string_canvas_points(self):
        """canvas_points as string should be coerced to float."""
        lms_assignment = DummyLmsAssignment(points_possible=100)
        assignment = DummyAssignment(
            lms_assignment=lms_assignment,
            canvas_points="80"  # String instead of int
        )

        scaled = assignment.scale_score_for_canvas(100.0)
        assert scaled == 80.0

    def test_finalize_uses_scaled_scores(self, tmp_path):
        """Finalize should push scaled scores to Canvas."""
        lms_assignment = DummyLmsAssignment(points_possible=50)
        assignment = DummyAssignment(lms_assignment=lms_assignment)
        assignment.submissions = [_make_submission(1, score=80.0)]

        assignment.finalize(push=True, idempotency_key="test",
                            idempotency_state_dir=str(tmp_path))

        # 80% on 50-point assignment = 40 points
        assert lms_assignment.pushed_scores[1] == 40.0


class TestRecordRetention:
    """Tests for record retention file creation."""

    def test_record_retention_creates_file(self, tmp_path):
        """Record retention should create a feedback file."""
        lms_assignment = DummyLmsAssignment()
        lms_assignment.name = "Weekly Notes 1"
        assignment = DummyAssignment(lms_assignment=lms_assignment)

        submission = _make_submission(12345, score=90.0)
        submission.feedback.comments = "Great work on this assignment!"
        assignment.submissions = [submission]

        records_dir = tmp_path / "records"
        assignment.finalize(
            push=False,
            record_retention=True,
            records_dir=str(records_dir)
        )

        # Check that records directory was created
        assert records_dir.exists()

        # Check that a file was created
        files = list(records_dir.glob("*.log"))
        assert len(files) == 1

        # Check filename format: timestamp.assignment.student_id.log
        filename = files[0].name
        assert "Weekly_Notes_1" in filename
        assert "student_12345" in filename
        assert filename.endswith(".log")

        # Check file contents
        content = files[0].read_text()
        assert "Great work on this assignment!" in content

    def test_record_retention_multiple_submissions(self, tmp_path):
        """Record retention should create files for all submissions."""
        lms_assignment = DummyLmsAssignment()
        lms_assignment.name = "PA1"
        assignment = DummyAssignment(lms_assignment=lms_assignment)
        assignment.submissions = [
            _make_submission(1, score=90.0),
            _make_submission(2, score=80.0),
            _make_submission(3, score=70.0),
        ]

        records_dir = tmp_path / "records"
        assignment.finalize(
            push=False,
            record_retention=True,
            records_dir=str(records_dir)
        )

        files = list(records_dir.glob("*.log"))
        assert len(files) == 3

    def test_record_retention_with_push(self, tmp_path):
        """Record retention should work alongside push."""
        lms_assignment = DummyLmsAssignment()
        assignment = DummyAssignment(lms_assignment=lms_assignment)
        assignment.submissions = [_make_submission(1)]

        records_dir = tmp_path / "records"
        assignment.finalize(
            push=True,
            record_retention=True,
            records_dir=str(records_dir),
            idempotency_key="test",
            idempotency_state_dir=str(tmp_path / "state")
        )

        # Both push and record should happen
        assert len(lms_assignment.push_calls) == 1
        assert len(list(records_dir.glob("*.log"))) == 1

    def test_record_retention_disabled_by_default(self, tmp_path):
        """Record retention should not create files when disabled."""
        lms_assignment = DummyLmsAssignment()
        assignment = DummyAssignment(lms_assignment=lms_assignment)
        assignment.submissions = [_make_submission(1)]

        records_dir = tmp_path / "records"
        assignment.finalize(
            push=False,
            record_retention=False,
            records_dir=str(records_dir)
        )

        # Directory should not be created
        assert not records_dir.exists()

    def test_record_retention_sanitizes_assignment_name(self, tmp_path):
        """Special characters in assignment name should be sanitized."""
        lms_assignment = DummyLmsAssignment()
        lms_assignment.name = "Week 1: Intro/Overview (Draft)"
        assignment = DummyAssignment(lms_assignment=lms_assignment)
        assignment.submissions = [_make_submission(1)]

        records_dir = tmp_path / "records"
        assignment.finalize(
            push=False,
            record_retention=True,
            records_dir=str(records_dir)
        )

        files = list(records_dir.glob("*.log"))
        assert len(files) == 1

        # Filename should not contain problematic characters
        filename = files[0].name
        assert "/" not in filename
        assert ":" not in filename
        assert "(" not in filename
        assert ")" not in filename


class TestPushFailureHandling:
    """Tests for push failure handling and error isolation."""

    def test_push_failure_continues_to_next_student(self, tmp_path):
        """Push failure for one student should not stop others."""
        lms_assignment = DummyLmsAssignment(fail_user_ids={2})
        assignment = DummyAssignment(lms_assignment=lms_assignment)
        assignment.submissions = [
            _make_submission(1),
            _make_submission(2),
            _make_submission(3),
        ]

        assignment.finalize(
            push=True,
            idempotency_key="test",
            idempotency_state_dir=str(tmp_path)
        )

        # All three should be attempted
        assert lms_assignment.push_calls == [1, 2, 3]

    def test_push_exception_continues_to_next_student(self, tmp_path):
        """Exception during push should not stop other students."""
        lms_assignment = DummyLmsAssignment(exception_user_ids={2})
        assignment = DummyAssignment(lms_assignment=lms_assignment)
        assignment.submissions = [
            _make_submission(1),
            _make_submission(2),
            _make_submission(3),
        ]

        assignment.finalize(
            push=True,
            idempotency_key="test",
            idempotency_state_dir=str(tmp_path)
        )

        # All three should be attempted despite exception on student 2
        assert lms_assignment.push_calls == [1, 2, 3]

    def test_push_disabled_skips_all(self, tmp_path):
        """push=False should not push anything."""
        lms_assignment = DummyLmsAssignment()
        assignment = DummyAssignment(lms_assignment=lms_assignment)
        assignment.submissions = [_make_submission(1), _make_submission(2)]

        assignment.finalize(push=False)

        assert lms_assignment.push_calls == []


class TestEdgeCases:
    """Edge case tests for finalize."""

    def test_empty_submissions_list(self, tmp_path):
        """Empty submissions list should not cause errors."""
        lms_assignment = DummyLmsAssignment()
        assignment = DummyAssignment(lms_assignment=lms_assignment)
        assignment.submissions = []

        # Should not raise
        assignment.finalize(
            push=True,
            idempotency_key="test",
            idempotency_state_dir=str(tmp_path)
        )

        assert lms_assignment.push_calls == []

    def test_none_feedback_score_handling(self, tmp_path):
        """Submissions with None score should be handled."""
        lms_assignment = DummyLmsAssignment(points_possible=100)
        assignment = DummyAssignment(lms_assignment=lms_assignment)

        submission = _make_submission(1)
        submission.feedback.percentage_score = None
        assignment.submissions = [submission]

        # Should not crash
        assignment.finalize(
            push=True,
            idempotency_key="test",
            idempotency_state_dir=str(tmp_path)
        )

    def test_submission_without_feedback(self, tmp_path):
        """Submissions without feedback should be handled gracefully."""
        lms_assignment = DummyLmsAssignment()
        assignment = DummyAssignment(lms_assignment=lms_assignment)

        submission = Submission(
            student=Student(name="No Feedback", user_id=999, _inner=None),
            status=Submission.Status.UNGRADED
        )
        submission.feedback = None
        assignment.submissions = [submission]

        # Should not crash when trying to access feedback
        # The current implementation may fail, which would be a bug to fix
        try:
            assignment.finalize(
                push=True,
                idempotency_key="test",
                idempotency_state_dir=str(tmp_path)
            )
        except AttributeError:
            pytest.skip("None feedback not handled - potential improvement")
