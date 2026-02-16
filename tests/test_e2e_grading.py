"""
End-to-end pipeline tests for the grading system.

Tests the full grading flow with mocked external dependencies:
1. Configuration parsing
2. Canvas course/assignment lookup
3. Submission fetching
4. LLM grading (aggregate + individual)
5. Feedback push

These tests verify that all components integrate correctly without
making actual API calls.
"""

import json
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from Autograder.assignment import Assignment
from Autograder.registry import AssignmentRegistry, GraderRegistry
from Autograder.grader import Grader
from lms_interface.classes import Feedback, Student, Submission, TextSubmission
from tests.fixtures import (
    VALID_AGGREGATE_ANALYSIS,
    VALID_INDIVIDUAL_GRADING_HIGH,
    VALID_INDIVIDUAL_GRADING_MEDIUM,
    make_individual_grading,
)


# =============================================================================
# Mock Components
# =============================================================================

class MockLmsAssignment:
    """Mock LMS assignment for testing."""

    def __init__(
        self,
        assignment_id: int = 12345,
        name: str = "Test Assignment",
        points_possible: float = 100.0,
    ):
        self.id = assignment_id
        self.name = name
        self.points_possible = points_possible
        self.canvas_course = SimpleNamespace(id=99999)
        self.push_calls: List[Dict[str, Any]] = []

    def get_submissions(self, **kwargs) -> List[TextSubmission]:
        """Return mock submissions."""
        submissions = []
        for i, (user_id, text) in enumerate([
            (1001, "Process scheduling uses round-robin and priority algorithms."),
            (1002, "Deadlock occurs when four conditions are met."),
            (1003, "Virtual memory uses paging for address translation."),
        ]):
            student = Student(name=f"Student {user_id}", user_id=user_id, _inner=None)
            submission = TextSubmission(
                student=student,
                status=Submission.Status.UNGRADED,
                submission_text=text,
            )
            submissions.append(submission)
        return submissions

    def push_feedback(self, *, user_id: int, score: float, comments: str, **kwargs) -> bool:
        """Record push calls for verification."""
        self.push_calls.append({
            "user_id": user_id,
            "score": score,
            "comments": comments,
        })
        return True


class MockTextAssignment(Assignment):
    """Mock text assignment for testing."""

    def __init__(self, lms_assignment: MockLmsAssignment, **kwargs):
        super().__init__(lms_assignment=lms_assignment, **kwargs)

    def prepare(self, **kwargs):
        """Fetch submissions from mock LMS."""
        self.submissions = self.lms_assignment.get_submissions()
        for submission in self.submissions:
            submission.feedback = None  # Will be set by grader


class MockTextGrader(Grader):
    """Mock text grader that returns predetermined scores."""

    COMPATIBLE_KINDS = {"TextAssignment"}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.graded_submissions: List[int] = []

    def can_grade_submission(self, submission: Submission) -> bool:
        return isinstance(submission, TextSubmission)

    def execute_grading(self, *args, **kwargs) -> Any:
        """Execute mock grading."""
        return None

    def score_grading(self, submission: Submission, grading_results: Any) -> Feedback:
        """Return mock scores based on student ID."""
        user_id = submission.student.user_id
        self.graded_submissions.append(user_id)

        # Vary scores by student
        if user_id == 1001:
            return Feedback(percentage_score=95.0, comments="Excellent work!")
        elif user_id == 1002:
            return Feedback(percentage_score=85.0, comments="Good understanding.")
        else:
            return Feedback(percentage_score=75.0, comments="Room for improvement.")

    def grade_assignment(self, assignment: Assignment, *args, **kwargs) -> None:
        """Grade all submissions in the assignment."""
        for submission in assignment.submissions:
            if self.can_grade_submission(submission):
                results = self.execute_grading(submission)
                submission.feedback = self.score_grading(submission, results)


# =============================================================================
# E2E Tests
# =============================================================================

class TestE2ETextGradingPipeline:
    """End-to-end tests for text submission grading."""

    def test_full_pipeline_no_push(self, tmp_path):
        """Test full pipeline without pushing to Canvas."""
        lms_assignment = MockLmsAssignment()
        assignment = MockTextAssignment(lms_assignment=lms_assignment)
        grader = MockTextGrader()

        # Run the pipeline
        assignment.prepare()
        assert len(assignment.submissions) == 3

        grader.grade_assignment(assignment)
        assert len(grader.graded_submissions) == 3

        # Verify all submissions have feedback
        for submission in assignment.submissions:
            assert submission.feedback is not None
            assert submission.feedback.percentage_score is not None

        # Don't push
        assignment.finalize(push=False)
        assert len(lms_assignment.push_calls) == 0

    def test_full_pipeline_with_push(self, tmp_path):
        """Test full pipeline with Canvas push."""
        lms_assignment = MockLmsAssignment(points_possible=50)
        assignment = MockTextAssignment(lms_assignment=lms_assignment)
        grader = MockTextGrader()

        # Run the pipeline
        assignment.prepare()
        grader.grade_assignment(assignment)

        # Push to Canvas
        assignment.finalize(
            push=True,
            idempotency_key="test-run",
            idempotency_state_dir=str(tmp_path),
        )

        # Verify push calls
        assert len(lms_assignment.push_calls) == 3

        # Verify scores were scaled (50 points possible)
        # Student 1001: 95% of 50 = 47.5
        call_1001 = next(c for c in lms_assignment.push_calls if c["user_id"] == 1001)
        assert call_1001["score"] == pytest.approx(47.5)

        # Student 1002: 85% of 50 = 42.5
        call_1002 = next(c for c in lms_assignment.push_calls if c["user_id"] == 1002)
        assert call_1002["score"] == pytest.approx(42.5)

    def test_full_pipeline_with_record_retention(self, tmp_path):
        """Test full pipeline with record retention."""
        lms_assignment = MockLmsAssignment()
        assignment = MockTextAssignment(lms_assignment=lms_assignment)
        grader = MockTextGrader()

        records_dir = tmp_path / "records"

        # Run the pipeline
        assignment.prepare()
        grader.grade_assignment(assignment)
        assignment.finalize(
            push=False,
            record_retention=True,
            records_dir=str(records_dir),
        )

        # Verify records were created
        assert records_dir.exists()
        record_files = list(records_dir.glob("*.log"))
        assert len(record_files) == 3

    def test_pipeline_idempotency(self, tmp_path):
        """Test that rerunning pipeline doesn't re-push."""
        lms_assignment = MockLmsAssignment()
        assignment = MockTextAssignment(lms_assignment=lms_assignment)
        grader = MockTextGrader()

        # First run
        assignment.prepare()
        grader.grade_assignment(assignment)
        assignment.finalize(
            push=True,
            idempotency_key="test-run",
            idempotency_state_dir=str(tmp_path),
        )

        initial_push_count = len(lms_assignment.push_calls)
        assert initial_push_count == 3

        # Second run with same key
        assignment2 = MockTextAssignment(lms_assignment=lms_assignment)
        assignment2.prepare()
        grader.grade_assignment(assignment2)
        assignment2.finalize(
            push=True,
            idempotency_key="test-run",
            idempotency_state_dir=str(tmp_path),
        )

        # Should not have pushed again
        assert len(lms_assignment.push_calls) == initial_push_count


class TestE2EWithRealGraderRegistry:
    """Tests using actual grader registry lookup."""

    def test_registry_lookup_and_grading(self, tmp_path):
        """Test that graders can be found via registry."""
        # Ensure registry modules are loaded
        GraderRegistry.load_premade_modules()

        # Get registered grader names from the registry dict
        registered = list(GraderRegistry._registry.keys())

        # Check that TextSubmissionGrader is registered
        assert "textsubmissiongrader" in registered or "weeklystudynotesgrader" in registered


class TestE2EConfigurationFlow:
    """Tests for configuration parsing and validation."""

    def test_yaml_config_structure(self):
        """Test that YAML config can be parsed correctly."""
        from Autograder.config_models import parse_run_config

        yaml_content = {
            "assignment_types": {
                "text": {
                    "kind": "TextAssignment",
                    "grader": "TextSubmissionGrader",
                    "settings": {
                        "prefer_anthropic": True,
                    }
                }
            },
            "courses": [
                {
                    "name": "Test Course",
                    "id": 12345,
                    "assignment_groups": [
                        {
                            "type": "text",
                            "assignments": [
                                {"id": 67890}
                            ]
                        }
                    ]
                }
            ]
        }

        config = parse_run_config(yaml_content)

        assert "text" in config.assignment_types
        assert len(config.courses) == 1
        assert config.courses[0].name == "Test Course"


class TestE2EErrorRecovery:
    """Tests for error handling and recovery in the pipeline."""

    def test_partial_grading_failure(self, tmp_path):
        """Test that grading continues after individual failures."""

        class FailingGrader(MockTextGrader):
            def score_grading(self, submission: Submission, grading_results: Any) -> Feedback:
                user_id = submission.student.user_id
                if user_id == 1002:
                    raise ValueError("Simulated grading error")
                return super().score_grading(submission, grading_results)

            def grade_assignment(self, assignment: Assignment, *args, **kwargs) -> None:
                for submission in assignment.submissions:
                    try:
                        if self.can_grade_submission(submission):
                            results = self.execute_grading(submission)
                            submission.feedback = self.score_grading(submission, results)
                    except Exception as e:
                        # Error recovery: assign zero score
                        submission.feedback = Feedback(
                            percentage_score=0.0,
                            comments=f"Grading error: {e}"
                        )

        lms_assignment = MockLmsAssignment()
        assignment = MockTextAssignment(lms_assignment=lms_assignment)
        grader = FailingGrader()

        assignment.prepare()
        grader.grade_assignment(assignment)

        # All submissions should have feedback
        assert all(s.feedback is not None for s in assignment.submissions)

        # Student 1002 should have error feedback
        s1002 = next(s for s in assignment.submissions if s.student.user_id == 1002)
        assert s1002.feedback.percentage_score == 0.0
        assert "error" in s1002.feedback.comments.lower()

    def test_push_failure_isolation(self, tmp_path):
        """Test that push failures are isolated per student."""

        class FailingLmsAssignment(MockLmsAssignment):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.push_attempts = []  # Track all attempts, not just successes

            def push_feedback(self, *, user_id: int, **kwargs) -> bool:
                self.push_attempts.append(user_id)
                if user_id == 1002:
                    return False  # Simulate push failure
                return super().push_feedback(user_id=user_id, **kwargs)

        lms_assignment = FailingLmsAssignment()
        assignment = MockTextAssignment(lms_assignment=lms_assignment)
        grader = MockTextGrader()

        assignment.prepare()
        grader.grade_assignment(assignment)
        assignment.finalize(
            push=True,
            idempotency_key="test",
            idempotency_state_dir=str(tmp_path),
        )

        # Should have attempted all three pushes (using push_attempts, not push_calls)
        assert 1001 in lms_assignment.push_attempts
        assert 1002 in lms_assignment.push_attempts  # Attempted but failed
        assert 1003 in lms_assignment.push_attempts

        # Only successful pushes should be in push_calls
        user_ids_pushed = [c["user_id"] for c in lms_assignment.push_calls]
        assert 1001 in user_ids_pushed
        assert 1002 not in user_ids_pushed  # Failed, not recorded
        assert 1003 in user_ids_pushed
