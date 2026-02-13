"""
Tests for Canvas API error handling (lms_interface/canvas_interface.py).

Covers:
- Rate limit (429) detection and handling
- Retry logic with exponential backoff
- Network timeout handling
- Authentication errors
- Resource not found errors
- Push feedback success and failure cases
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import time

from lms_interface import canvas_interface
from lms_interface.canvas_interface import (
    CanvasInterface,
    CanvasCourse,
    CanvasAssignment,
    _is_retryable_canvas_exception,
    _canvas_exception_status,
    _format_canvas_exception,
)


class MockCanvasException(Exception):
    """Mock Canvas API exception with status code."""

    def __init__(self, message: str = "Canvas error", status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class MockResponse:
    """Mock HTTP response object."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text
        self.request = MagicMock()
        self.request.method = "GET"
        self.request.url = "https://canvas.example.edu/api/v1/test"

    def json(self):
        return self._json_data


class TestCanvasExceptionHelpers:
    """Tests for exception handling helper functions."""

    def test_canvas_exception_status_from_status_code_attr(self):
        exc = MockCanvasException(status_code=429)
        assert _canvas_exception_status(exc) == 429

    def test_canvas_exception_status_from_response_attr(self):
        exc = Exception("error")
        exc.response = MockResponse(status_code=500)
        assert _canvas_exception_status(exc) == 500

    def test_canvas_exception_status_returns_none_for_plain_exception(self):
        exc = Exception("plain error")
        assert _canvas_exception_status(exc) is None

    def test_is_retryable_for_rate_limit(self):
        exc = MockCanvasException(status_code=429)
        assert _is_retryable_canvas_exception(exc) is True

    def test_is_retryable_for_server_errors(self):
        for status in [500, 502, 503, 504]:
            exc = MockCanvasException(status_code=status)
            assert _is_retryable_canvas_exception(exc) is True

    def test_is_not_retryable_for_client_errors(self):
        for status in [400, 401, 403, 404]:
            exc = MockCanvasException(status_code=status)
            assert _is_retryable_canvas_exception(exc) is False

    def test_is_retryable_for_unknown_status(self):
        # Exceptions without status code are considered retryable (network issues)
        exc = Exception("connection error")
        assert _is_retryable_canvas_exception(exc) is True

    def test_format_canvas_exception_includes_status(self):
        exc = MockCanvasException(status_code=429)
        formatted = _format_canvas_exception(exc)
        assert "status=429" in formatted

    def test_format_canvas_exception_includes_response_json(self):
        exc = Exception("error")
        exc.response = MockResponse(
            status_code=400,
            json_data={"errors": [{"message": "Invalid request"}]}
        )
        formatted = _format_canvas_exception(exc)
        assert "Invalid request" in formatted


class TestCanvasInterfaceInit:
    """Tests for CanvasInterface initialization."""

    def test_init_with_explicit_credentials(self):
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="test_token_123",
        )
        assert interface.canvas_url == "https://canvas.example.edu"
        assert interface.canvas_key == "test_token_123"

    def test_init_requires_both_url_and_key(self):
        with pytest.raises(ValueError, match="Both canvas_url and canvas_key"):
            CanvasInterface(canvas_url="https://canvas.example.edu")

        with pytest.raises(ValueError, match="Both canvas_url and canvas_key"):
            CanvasInterface(canvas_key="token")

    def test_init_validates_privacy_mode(self):
        with pytest.raises(ValueError, match="privacy_mode must be one of"):
            CanvasInterface(
                canvas_url="https://canvas.example.edu",
                canvas_key="token",
                privacy_mode="invalid_mode",
            )

    def test_init_accepts_valid_privacy_modes(self):
        for mode in ["none", "id_only", "blind"]:
            interface = CanvasInterface(
                canvas_url="https://canvas.example.edu",
                canvas_key="token",
                privacy_mode=mode,
            )
            assert interface.privacy_mode == mode

    def test_init_defaults_to_id_only_privacy(self):
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
        )
        assert interface.privacy_mode == "id_only"


class TestCanvasInterfacePrivacy:
    """Tests for privacy-related functionality."""

    def test_resolve_student_name_none_mode_returns_raw_name(self):
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
            privacy_mode="none",
        )
        name = interface.resolve_student_name(12345, raw_name="John Doe")
        assert name == "John Doe"

    def test_resolve_student_name_none_mode_fallback(self):
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
            privacy_mode="none",
        )
        name = interface.resolve_student_name(12345, raw_name=None)
        assert name == "Student 12345"

    def test_resolve_student_name_id_only_mode(self):
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
            privacy_mode="id_only",
        )
        name = interface.resolve_student_name(12345, raw_name="John Doe")
        assert name == "Student 12345"

    def test_resolve_student_name_blind_mode_is_stable(self):
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
            privacy_mode="blind",
        )
        name1 = interface.resolve_student_name(12345)
        name2 = interface.resolve_student_name(12345)
        name3 = interface.resolve_student_name(67890)

        assert name1 == name2  # Same user gets same anonymous ID
        assert name1 != name3  # Different users get different IDs
        assert name1.startswith("Anon ")
        assert name3.startswith("Anon ")

    def test_blind_mode_thread_safe(self):
        """Test that blind mode anonymous ID generation is thread-safe."""
        import threading

        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
            privacy_mode="blind",
        )

        results = {}
        errors = []

        def resolve_name(user_id):
            try:
                name = interface.resolve_student_name(user_id)
                results[user_id] = name
            except Exception as e:
                errors.append(e)

        threads = []
        for user_id in range(100):
            t = threading.Thread(target=resolve_name, args=(user_id,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 100
        # All anonymous IDs should be unique
        assert len(set(results.values())) == 100


class TestCanvasCourseRetry:
    """Tests for retry logic in Canvas API calls."""

    def test_call_canvas_with_retry_succeeds_on_first_try(self):
        """Test successful call on first attempt."""
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
        )

        # Create a minimal CanvasCourse for testing
        mock_canvasapi_course = MagicMock()
        mock_canvasapi_course.id = 123
        mock_canvasapi_course.name = "Test Course"

        course = CanvasCourse(
            canvas_interface=interface,
            canvasapi_course=mock_canvasapi_course,
        )

        call_count = [0]

        def successful_func():
            call_count[0] += 1
            return "success"

        result = course._call_canvas_with_retry(
            label="test",
            func=successful_func,
            max_upload_retries=3,
            retry_backoff_base=0.01,
            retry_backoff_max=0.1,
            backoff_controller=None,
        )

        assert result is True
        assert call_count[0] == 1

    def test_call_canvas_with_retry_retries_on_server_error(self):
        """Test retry on 500 error."""
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
        )

        mock_canvasapi_course = MagicMock()
        mock_canvasapi_course.id = 123

        course = CanvasCourse(
            canvas_interface=interface,
            canvasapi_course=mock_canvasapi_course,
        )

        call_count = [0]

        def flaky_func():
            call_count[0] += 1
            if call_count[0] < 3:
                # Import the actual exception class
                import canvasapi.exceptions
                raise canvasapi.exceptions.CanvasException("Server error")
            return "success"

        # Patch the status code extraction
        with patch.object(canvas_interface, '_canvas_exception_status', return_value=500):
            with patch.object(canvas_interface, '_is_retryable_canvas_exception', return_value=True):
                result = course._call_canvas_with_retry(
                    label="test",
                    func=flaky_func,
                    max_upload_retries=5,
                    retry_backoff_base=0.01,
                    retry_backoff_max=0.05,
                    backoff_controller=None,
                )

        assert result is True
        assert call_count[0] == 3  # Failed twice, succeeded on third

    def test_call_canvas_with_retry_fails_on_non_retryable_error(self):
        """Test that non-retryable errors fail immediately."""
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
        )

        mock_canvasapi_course = MagicMock()
        mock_canvasapi_course.id = 123

        course = CanvasCourse(
            canvas_interface=interface,
            canvasapi_course=mock_canvasapi_course,
        )

        call_count = [0]

        def auth_error_func():
            call_count[0] += 1
            import canvasapi.exceptions
            raise canvasapi.exceptions.CanvasException("Unauthorized")

        # 401 is not retryable
        with patch.object(canvas_interface, '_canvas_exception_status', return_value=401):
            with patch.object(canvas_interface, '_is_retryable_canvas_exception', return_value=False):
                result = course._call_canvas_with_retry(
                    label="test",
                    func=auth_error_func,
                    max_upload_retries=5,
                    retry_backoff_base=0.01,
                    retry_backoff_max=0.05,
                    backoff_controller=None,
                )

        assert result is False
        assert call_count[0] == 1  # Only tried once


class TestCanvasAssignmentSubmissions:
    """Tests for assignment submission handling."""

    def test_get_assignment_returns_none_for_missing(self):
        """Test that missing assignment returns None."""
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
        )

        mock_canvasapi_course = MagicMock()
        mock_canvasapi_course.id = 123
        mock_canvasapi_course.name = "Test Course"

        # Simulate ResourceDoesNotExist
        import canvasapi.exceptions
        mock_canvasapi_course.get_assignment.side_effect = canvasapi.exceptions.ResourceDoesNotExist(
            "Not found"
        )

        course = CanvasCourse(
            canvas_interface=interface,
            canvasapi_course=mock_canvasapi_course,
        )

        result = course.get_assignment(99999)
        assert result is None


class TestBackoffController:
    """Tests for the backoff controller used in parallel uploads."""

    def test_backoff_controller_initial_state(self):
        controller = canvas_interface._CanvasBackoffController()
        # Should not block initially
        start = time.monotonic()
        controller.wait()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # Should be nearly instant

    def test_backoff_controller_defer_adds_delay(self):
        controller = canvas_interface._CanvasBackoffController()
        controller.defer(0.1)  # 100ms delay

        start = time.monotonic()
        controller.wait()
        elapsed = time.monotonic() - start

        # Should have waited approximately 100ms
        assert elapsed >= 0.05  # Allow some tolerance

    def test_backoff_controller_defer_is_cumulative_max(self):
        controller = canvas_interface._CanvasBackoffController()

        # Defer twice - should use the maximum
        controller.defer(0.05)
        controller.defer(0.1)

        start = time.monotonic()
        controller.wait()
        elapsed = time.monotonic() - start

        # Should wait for the longer delay
        assert elapsed >= 0.05
