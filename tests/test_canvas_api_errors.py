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
from types import SimpleNamespace
import requests

from lms_interface import canvas_interface
from lms_interface.canvas_interface import (
    CanvasInterface,
    CanvasCourse,
    CanvasAssignment,
    _is_retryable_canvas_exception,
    _canvas_exception_status,
    _format_canvas_exception,
    _compute_retry_delay_seconds,
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

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_call_canvas_with_retry_does_not_retry_auth_errors(self, status_code):
        """Authentication failures should fail immediately with no retry sleep."""
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
            exc = canvasapi.exceptions.CanvasException("Unauthorized")
            exc.status_code = status_code
            raise exc

        with patch.object(canvas_interface.time, "sleep") as mock_sleep:
            result = course._call_canvas_with_retry(
                label="test",
                func=auth_error_func,
                max_upload_retries=5,
                retry_backoff_base=0.01,
                retry_backoff_max=0.05,
                backoff_controller=None,
            )

        assert result is False
        assert call_count[0] == 1
        mock_sleep.assert_not_called()

    def test_call_canvas_with_retry_uses_jittered_sleep(self):
        """Retry delay should include jitter when enabled."""
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

        def flaky_once():
            call_count[0] += 1
            if call_count[0] == 1:
                import canvasapi.exceptions
                raise canvasapi.exceptions.CanvasException("Server error")
            return "ok"

        with patch.object(canvas_interface, '_canvas_exception_status', return_value=500):
            with patch.object(canvas_interface, '_is_retryable_canvas_exception', return_value=True):
                with patch.object(canvas_interface.random, 'uniform', return_value=0.1):
                    with patch.object(canvas_interface.time, 'sleep') as mock_sleep:
                        result = course._call_canvas_with_retry(
                            label="test",
                            func=flaky_once,
                            max_upload_retries=3,
                            retry_backoff_base=1.0,
                            retry_backoff_max=10.0,
                            backoff_controller=None,
                            retry_backoff_jitter_ratio=0.2,
                        )

        assert result is True
        assert call_count[0] == 2
        # base delay 1.0 with +0.1 jitter should sleep 1.1
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args.args[0] == pytest.approx(1.1, rel=1e-6)

    def test_call_canvas_with_retry_caps_total_retry_duration(self):
        """Retry loop should stop once total retry duration cap is reached."""
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

        def always_fails():
            call_count[0] += 1
            import canvasapi.exceptions
            raise canvasapi.exceptions.CanvasException("Server error")

        fake_clock = {"t": 0.0}

        def fake_monotonic():
            return fake_clock["t"]

        def fake_sleep(seconds):
            fake_clock["t"] += seconds

        with patch.object(canvas_interface, '_canvas_exception_status', return_value=500):
            with patch.object(canvas_interface, '_is_retryable_canvas_exception', return_value=True):
                with patch.object(canvas_interface.time, 'monotonic', side_effect=fake_monotonic):
                    with patch.object(canvas_interface.time, 'sleep', side_effect=fake_sleep):
                        result = course._call_canvas_with_retry(
                            label="test",
                            func=always_fails,
                            max_upload_retries=10,
                            retry_backoff_base=0.1,
                            retry_backoff_max=0.1,
                            backoff_controller=None,
                            retry_backoff_jitter_ratio=0.0,
                            retry_total_timeout_seconds=0.15,
                        )

        assert result is False
        # attempt 1 at t=0.0, sleep 0.1; attempt 2 at t=0.1, sleep 0.05; then cap reached
        assert call_count[0] == 2


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

    def test_get_assignment_raises_when_canvas_assignment_metadata_incomplete(self):
        """Canvas maintenance/partial outages can return assignment objects missing required fields."""
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
        )

        mock_canvasapi_course = MagicMock()
        mock_canvasapi_course.id = 123
        mock_canvasapi_course.name = "Test Course"

        # Simulate partial metadata object with no `name`.
        incomplete_assignment = SimpleNamespace(id=555)
        mock_canvasapi_course.get_assignment.return_value = incomplete_assignment

        course = CanvasCourse(
            canvas_interface=interface,
            canvasapi_course=mock_canvasapi_course,
        )

        with pytest.raises(ValueError, match="incomplete metadata"):
            course.get_assignment(555)


class TestCanvasAssignmentPushFeedback:
    """Tests for push_feedback temp file handling."""

    def test_push_feedback_uses_system_temp_not_repo_dir(self, monkeypatch):
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
        )

        mock_canvasapi_course = MagicMock()
        mock_canvasapi_course.course = SimpleNamespace(id=123)

        mock_submission = MagicMock()
        mock_submission.score = None
        mock_submission.submission_comments = []

        mock_canvasapi_assignment = MagicMock()
        mock_canvasapi_assignment.id = 456
        mock_canvasapi_assignment.get_submission.return_value = mock_submission

        assignment = CanvasAssignment(
            canvasapi_interface=interface,
            canvasapi_course=mock_canvasapi_course,
            canvasapi_assignment=mock_canvasapi_assignment,
        )

        captured_kwargs = {}
        removed_paths = []

        class FakeTmpFile:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                self.name = "/tmp/autograder_feedback_upload_test.txt"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, _buffer):
                return None

            def flush(self):
                return None

            def fileno(self):
                return 1

        monkeypatch.setattr(
            canvas_interface.tempfile,
            "NamedTemporaryFile",
            lambda **kwargs: FakeTmpFile(**kwargs),
        )
        monkeypatch.setattr(canvas_interface.os, "fsync", lambda _fd: None)
        monkeypatch.setattr(canvas_interface.os, "remove",
                            lambda path: removed_paths.append(path))

        ok = assignment.push_feedback(user_id=42, score=10.0, comments="hello")

        assert ok is True
        assert captured_kwargs["prefix"] == "autograder_feedback_upload_"
        assert "dir" not in captured_kwargs
        mock_submission.upload_comment.assert_called_once_with(
            "/tmp/autograder_feedback_upload_test.txt"
        )
        assert removed_paths == ["/tmp/autograder_feedback_upload_test.txt"]

    def test_push_feedback_recovers_from_timeout_fetching_previous_submission(self):
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
        )

        mock_canvasapi_course = MagicMock()
        mock_canvasapi_course.course = SimpleNamespace(id=123)

        mock_submission = MagicMock()
        mock_submission.score = None
        mock_submission.submission_comments = []

        mock_canvasapi_assignment = MagicMock()
        mock_canvasapi_assignment.id = 456
        mock_canvasapi_assignment.get_submission.side_effect = [
            requests.exceptions.Timeout("timed out"),
            mock_submission,
        ]

        assignment = CanvasAssignment(
            canvasapi_interface=interface,
            canvasapi_course=mock_canvasapi_course,
            canvasapi_assignment=mock_canvasapi_assignment,
        )

        ok = assignment.push_feedback(user_id=42, score=10.0, comments="")

        assert ok is True
        assert mock_canvasapi_assignment.get_submission.call_count == 2
        mock_canvasapi_assignment.submissions_bulk_update.assert_called_once_with(
            grade_data={"submission[posted_grade]": 10.0},
            student_ids=[42],
        )
        mock_submission.edit.assert_called_once_with(
            submission={"posted_grade": 10.0},
        )

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_push_feedback_returns_false_on_auth_error_during_bulk_update(self, status_code):
        interface = CanvasInterface(
            canvas_url="https://canvas.example.edu",
            canvas_key="token",
        )

        mock_canvasapi_course = MagicMock()
        mock_canvasapi_course.course = SimpleNamespace(id=123)

        mock_submission = MagicMock()
        mock_submission.score = None
        mock_submission.submission_comments = []

        mock_canvasapi_assignment = MagicMock()
        mock_canvasapi_assignment.id = 456
        mock_canvasapi_assignment.get_submission.return_value = mock_submission

        import canvasapi.exceptions
        auth_exc = canvasapi.exceptions.CanvasException("Unauthorized")
        auth_exc.status_code = status_code
        mock_canvasapi_assignment.submissions_bulk_update.side_effect = auth_exc

        assignment = CanvasAssignment(
            canvasapi_interface=interface,
            canvasapi_course=mock_canvasapi_course,
            canvasapi_assignment=mock_canvasapi_assignment,
        )

        ok = assignment.push_feedback(user_id=42, score=10.0, comments="")

        assert ok is False
        mock_canvasapi_assignment.get_submission.assert_called_once_with(42)
        mock_submission.edit.assert_not_called()


class TestCanvasQuestionUploadErrorHandling:
    """Tests for partial-failure behavior in question bulk uploads."""

    def test_upload_question_payloads_continues_after_single_failure(self):
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

        mock_quiz = MagicMock()
        mock_quiz.id = 987

        payloads = [
            {"question_name": "q1"},
            {"question_name": "q2"},
            {"question_name": "q3"},
        ]
        events = []
        labels_seen = []

        def fake_retry(label, func, **_kwargs):
            labels_seen.append(label)
            func()
            return label != "q2"

        with patch.object(course, "_call_canvas_with_retry", side_effect=fake_retry):
            course._upload_question_payloads(
                canvas_quiz=mock_quiz,
                payloads=payloads,
                max_workers=1,
                progress_callback=events.append,
                show_progress_bar=False,
                total_questions=len(payloads),
            )

        assert labels_seen == ["q1", "q2", "q3"]
        assert mock_quiz.create_question.call_count == 3
        assert events[0]["event"] == "start"
        assert events[-1]["event"] == "complete"
        assert events[-1]["completed"] == 3
        assert events[-1]["succeeded"] == 2
        assert events[-1]["failed"] == 1


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


class TestRetryDelayCalculation:
    """Tests for retry delay calculation with jitter."""

    def test_compute_retry_delay_without_jitter(self):
        delay = _compute_retry_delay_seconds(
            3,
            retry_backoff_base=1.0,
            retry_backoff_max=10.0,
            retry_backoff_jitter_ratio=0.0,
        )
        assert delay == pytest.approx(4.0)

    def test_compute_retry_delay_with_jitter_and_cap(self):
        with patch.object(canvas_interface.random, 'uniform', return_value=2.0):
            delay = _compute_retry_delay_seconds(
                4,
                retry_backoff_base=1.0,
                retry_backoff_max=5.0,
                retry_backoff_jitter_ratio=0.5,
            )
        # base delay would be 5.0 after cap; jitter tries to push above max, should stay capped.
        assert delay == pytest.approx(5.0)
