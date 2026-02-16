"""
Mock classes and factories for LLM (Anthropic/OpenAI) testing.

These mocks allow testing the ai_helper module and text grading
without making actual API calls.
"""

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MockTextBlock:
    """Mock Anthropic TextBlock."""
    text: str
    type: str = "text"


@dataclass
class MockAnthropicUsage:
    """Mock Anthropic usage object."""
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class MockAnthropicMessage:
    """Mock Anthropic message response."""
    content: list[MockTextBlock]
    id: str = "msg_test123"
    model: str = "claude-3-haiku-20240307"
    role: str = "assistant"
    stop_reason: str = "end_turn"
    type: str = "message"
    usage: MockAnthropicUsage = field(default_factory=MockAnthropicUsage)


class MockAnthropicMessages:
    """Mock Anthropic messages API."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        """
        Args:
            responses: List of response strings to return in order.
            error: Exception to raise on create() call.
        """
        self.responses = responses or ['{"result": "ok"}']
        self.error = error
        self.call_count = 0
        self.last_call_kwargs: dict[str, Any] = {}

    def create(self, **kwargs) -> MockAnthropicMessage:
        self.last_call_kwargs = kwargs
        self.call_count += 1

        if self.error:
            raise self.error

        # Cycle through responses
        response_idx = (self.call_count - 1) % len(self.responses)
        response_text = self.responses[response_idx]

        return MockAnthropicMessage(
            content=[MockTextBlock(text=response_text)]
        )


class MockAnthropicClient:
    """Mock Anthropic client for testing."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        """
        Args:
            responses: List of JSON response strings.
            error: Exception to raise on API calls.
        """
        self.messages = MockAnthropicMessages(responses=responses, error=error)


@dataclass
class MockOpenAIChoice:
    """Mock OpenAI choice object."""
    message: "MockOpenAIMessage"
    finish_reason: str = "stop"
    index: int = 0


@dataclass
class MockOpenAIMessage:
    """Mock OpenAI message object."""
    content: str
    role: str = "assistant"


@dataclass
class MockOpenAIUsage:
    """Mock OpenAI usage object."""
    prompt_tokens: int = 100
    completion_tokens: int = 50
    total_tokens: int = 150


@dataclass
class MockOpenAICompletion:
    """Mock OpenAI chat completion response."""
    choices: list[MockOpenAIChoice]
    id: str = "chatcmpl-test123"
    model: str = "gpt-4"
    created: int = 1234567890
    usage: MockOpenAIUsage = field(default_factory=MockOpenAIUsage)


class MockOpenAIChat:
    """Mock OpenAI chat completions API."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        self.responses = responses or ['{"result": "ok"}']
        self.error = error
        self.call_count = 0
        self.last_call_kwargs: dict[str, Any] = {}

    def create(self, **kwargs) -> MockOpenAICompletion:
        self.last_call_kwargs = kwargs
        self.call_count += 1

        if self.error:
            raise self.error

        response_idx = (self.call_count - 1) % len(self.responses)
        response_text = self.responses[response_idx]

        return MockOpenAICompletion(
            choices=[MockOpenAIChoice(
                message=MockOpenAIMessage(content=response_text)
            )]
        )


class MockOpenAICompletions:
    """Wrapper to match OpenAI client.chat.completions structure."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        self.completions = MockOpenAIChat(responses=responses, error=error)


class MockOpenAIClient:
    """Mock OpenAI client for testing."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None):
        self._chat = MockOpenAICompletions(responses=responses, error=error)

    @property
    def chat(self):
        return self._chat


def make_anthropic_response(content: str | dict) -> str:
    """
    Create a properly formatted Anthropic response string.

    Args:
        content: Either a string or dict to return. Dicts are JSON-encoded.
    """
    if isinstance(content, dict):
        return json.dumps(content)
    return content


def make_openai_response(content: str | dict) -> str:
    """
    Create a properly formatted OpenAI response string.

    Args:
        content: Either a string or dict to return. Dicts are JSON-encoded.
    """
    if isinstance(content, dict):
        return json.dumps(content)
    return content


def make_grading_result(
    student_id: int = 12345,
    engagement_score: int = 3,
    length_score: int = 2,
    relevance_score: int = 2,
    explanation_quality_score: int = 2,
    total_grade: int = 9,
    accurate_word_count: int = 350,
    topics_needing_review: list[str] | None = None,
    feedback: str = "Good work on this submission.",
) -> dict:
    """
    Create a grading result dict matching TextSubmissionGrader's expected format.
    """
    return {
        "student_id": student_id,
        "engagement_score": engagement_score,
        "length_score": length_score,
        "relevance_score": relevance_score,
        "explanation_quality_score": explanation_quality_score,
        "total_grade": total_grade,
        "accurate_word_count": accurate_word_count,
        "topics_needing_review": topics_needing_review or [],
        "feedback": feedback,
    }


# Common error scenarios for testing
class MockRateLimitError(Exception):
    """Mock rate limit error."""
    def __init__(self, message: str = "Rate limit exceeded"):
        self.message = message
        self.status_code = 429
        super().__init__(message)


class MockAPIError(Exception):
    """Mock generic API error."""
    def __init__(self, message: str = "API error", status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class MockAuthenticationError(Exception):
    """Mock authentication error."""
    def __init__(self, message: str = "Invalid API key"):
        self.message = message
        self.status_code = 401
        super().__init__(message)
