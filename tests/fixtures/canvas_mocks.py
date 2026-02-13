"""
Mock classes and factories for Canvas API testing.

These mocks allow testing Canvas integration without actual API calls.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MockCanvasUser:
    """Mock Canvas user object."""
    id: int
    name: str
    sortable_name: str = ""

    def __post_init__(self):
        if not self.sortable_name:
            parts = self.name.split()
            if len(parts) >= 2:
                self.sortable_name = f"{parts[-1]}, {' '.join(parts[:-1])}"
            else:
                self.sortable_name = self.name


@dataclass
class MockCanvasSubmission:
    """Mock Canvas submission object."""
    user_id: int
    workflow_state: str = "submitted"
    score: float | None = None
    submission_history: list[dict] = field(default_factory=list)
    submission_comments: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.submission_history:
            self.submission_history = [{
                "workflow_state": self.workflow_state,
                "score": self.score,
                "body": None,
                "attachments": None,
            }]


@dataclass
class MockCanvasAttachment:
    """Mock Canvas attachment object."""
    id: int
    filename: str
    url: str
    size: int = 1024
    content_type: str = "text/plain"


class MockCanvasAssignment:
    """Mock Canvas assignment object."""

    def __init__(
        self,
        id: int = 123,
        name: str = "Test Assignment",
        points_possible: float = 100.0,
        submissions: list[MockCanvasSubmission] | None = None,
        push_error: Exception | None = None,
        push_fail_user_ids: set[int] | None = None,
    ):
        self.id = id
        self.name = name
        self.points_possible = points_possible
        self._submissions = submissions or []
        self._push_error = push_error
        self._push_fail_user_ids = push_fail_user_ids or set()
        self.push_calls: list[dict] = []

    def get_submissions(self, include: str | None = None, **kwargs):
        """Return submissions iterator."""
        return iter(self._submissions)

    def get_submission(self, user_id: int) -> MockCanvasSubmission:
        """Get a specific submission."""
        for sub in self._submissions:
            if sub.user_id == user_id:
                return sub
        raise MockCanvasException(f"Submission not found for user {user_id}")

    def submissions_bulk_update(self, grade_data: dict, student_ids: list[int]):
        """Mock bulk update."""
        self.push_calls.append({
            "grade_data": grade_data,
            "student_ids": student_ids,
        })
        if self._push_error:
            raise self._push_error


class MockCanvasCourse:
    """Mock Canvas course object."""

    def __init__(
        self,
        id: int = 456,
        name: str = "Test Course",
        assignments: dict[int, MockCanvasAssignment] | None = None,
        users: dict[int, MockCanvasUser] | None = None,
    ):
        self.id = id
        self.name = name
        self._assignments = assignments or {}
        self._users = users or {}

    def get_assignment(self, assignment_id: int) -> MockCanvasAssignment:
        """Get an assignment by ID."""
        if assignment_id in self._assignments:
            return self._assignments[assignment_id]
        raise MockResourceDoesNotExist(f"Assignment {assignment_id} not found")

    def get_assignments(self, **kwargs):
        """Return all assignments."""
        return iter(self._assignments.values())

    def get_user(self, user_id: int) -> MockCanvasUser:
        """Get a user by ID."""
        if user_id in self._users:
            return self._users[user_id]
        raise MockCanvasException(f"User {user_id} not found")

    def get_users(self, enrollment_type: list[str] | None = None):
        """Return users iterator."""
        return iter(self._users.values())


class MockCanvas:
    """Mock Canvas API client."""

    def __init__(self, courses: dict[int, MockCanvasCourse] | None = None):
        self._courses = courses or {}

    def get_course(self, course_id: int) -> MockCanvasCourse:
        """Get a course by ID."""
        if course_id in self._courses:
            return self._courses[course_id]
        raise MockResourceDoesNotExist(f"Course {course_id} not found")


class MockCanvasApi:
    """
    Complete mock Canvas API setup for testing.

    Usage:
        mock_api = MockCanvasApi()
        mock_api.add_course(123, "Test Course")
        mock_api.add_assignment(123, 456, "PA1")
        mock_api.add_submission(123, 456, user_id=789, workflow_state="submitted")

        # Use mock_api.canvas as the Canvas client
    """

    def __init__(self):
        self._courses: dict[int, MockCanvasCourse] = {}
        self._users: dict[int, MockCanvasUser] = {}
        self.canvas = MockCanvas(self._courses)

    def add_course(
        self,
        course_id: int,
        name: str = "Test Course",
    ) -> MockCanvasCourse:
        """Add a course to the mock API."""
        course = MockCanvasCourse(id=course_id, name=name)
        self._courses[course_id] = course
        self.canvas._courses = self._courses
        return course

    def add_user(
        self,
        user_id: int,
        name: str = "Test Student",
        course_id: int | None = None,
    ) -> MockCanvasUser:
        """Add a user, optionally to a specific course."""
        user = MockCanvasUser(id=user_id, name=name)
        self._users[user_id] = user
        if course_id and course_id in self._courses:
            self._courses[course_id]._users[user_id] = user
        return user

    def add_assignment(
        self,
        course_id: int,
        assignment_id: int,
        name: str = "Test Assignment",
        points_possible: float = 100.0,
    ) -> MockCanvasAssignment:
        """Add an assignment to a course."""
        if course_id not in self._courses:
            self.add_course(course_id)

        assignment = MockCanvasAssignment(
            id=assignment_id,
            name=name,
            points_possible=points_possible,
        )
        self._courses[course_id]._assignments[assignment_id] = assignment
        return assignment

    def add_submission(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int,
        workflow_state: str = "submitted",
        score: float | None = None,
        body: str | None = None,
        attachments: list[dict] | None = None,
    ) -> MockCanvasSubmission:
        """Add a submission to an assignment."""
        if course_id not in self._courses:
            self.add_course(course_id)
        if assignment_id not in self._courses[course_id]._assignments:
            self.add_assignment(course_id, assignment_id)

        submission = MockCanvasSubmission(
            user_id=user_id,
            workflow_state=workflow_state,
            score=score,
            submission_history=[{
                "workflow_state": workflow_state,
                "score": score,
                "body": body,
                "attachments": attachments,
            }],
        )
        self._courses[course_id]._assignments[assignment_id]._submissions.append(submission)
        return submission


# Canvas exception mocks
class MockCanvasException(Exception):
    """Mock base Canvas exception."""
    def __init__(self, message: str = "Canvas error", status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class MockResourceDoesNotExist(MockCanvasException):
    """Mock resource not found exception."""
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=404)


class MockRateLimitExceeded(MockCanvasException):
    """Mock rate limit exception."""
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(message, status_code=429)


class MockUnauthorized(MockCanvasException):
    """Mock unauthorized exception."""
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, status_code=401)


# Factory functions for common response patterns
def make_canvas_submission_response(
    user_id: int,
    workflow_state: str = "submitted",
    score: float | None = None,
    body: str | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    """Create a submission response dict matching Canvas API format."""
    return {
        "user_id": user_id,
        "workflow_state": workflow_state,
        "score": score,
        "body": body,
        "attachments": attachments,
        "submission_history": [{
            "workflow_state": workflow_state,
            "score": score,
            "body": body,
            "attachments": attachments,
        }],
    }


def make_canvas_assignment_response(
    assignment_id: int,
    name: str = "Test Assignment",
    points_possible: float = 100.0,
    course_id: int = 456,
) -> dict:
    """Create an assignment response dict matching Canvas API format."""
    return {
        "id": assignment_id,
        "name": name,
        "points_possible": points_possible,
        "course_id": course_id,
    }
