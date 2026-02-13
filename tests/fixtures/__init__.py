"""
Shared test fixtures for the Autograder test suite.

This module provides reusable factories and mocks for:
- Student and Submission objects
- Canvas API responses
- LLM (Anthropic/OpenAI) responses
- Docker container mocks
"""

from .factories import (
    make_student,
    make_submission,
    make_text_submission,
    make_file_submission,
    make_feedback,
)

from .llm_mocks import (
    MockAnthropicClient,
    MockOpenAIClient,
    make_anthropic_response,
    make_openai_response,
    make_grading_result,
)

from .canvas_mocks import (
    MockCanvasApi,
    make_canvas_submission_response,
    make_canvas_assignment_response,
)

__all__ = [
    # Factories
    "make_student",
    "make_submission",
    "make_text_submission",
    "make_file_submission",
    "make_feedback",
    # LLM mocks
    "MockAnthropicClient",
    "MockOpenAIClient",
    "make_anthropic_response",
    "make_openai_response",
    "make_grading_result",
    # Canvas mocks
    "MockCanvasApi",
    "make_canvas_submission_response",
    "make_canvas_assignment_response",
]
