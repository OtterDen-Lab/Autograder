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

from .docker_mocks import (
    MockDockerClient,
    MockDockerContainer,
    MockDockerCommandResult,
    MockDockerImage,
    MockDockerModule,
)

from .llm_responses import (
    # Aggregate analysis
    VALID_AGGREGATE_ANALYSIS,
    AGGREGATE_ANALYSIS_MINIMAL,
    AGGREGATE_ANALYSIS_EMPTY_TOPICS,
    # Individual grading
    VALID_INDIVIDUAL_GRADING_HIGH,
    VALID_INDIVIDUAL_GRADING_MEDIUM,
    VALID_INDIVIDUAL_GRADING_LOW,
    VALID_INDIVIDUAL_GRADING_NEEDS_SUPPORT,
    INDIVIDUAL_GRADING_MINIMAL,
    # Question consolidation
    VALID_QUESTION_CONSOLIDATION,
    QUESTION_CONSOLIDATION_EMPTY,
    QUESTION_CONSOLIDATION_SINGLE,
    # Malformed responses
    MALFORMED_JSON_STRING,
    MALFORMED_JSON_UNCLOSED,
    MALFORMED_WRONG_TYPE_ROOT,
    MALFORMED_WRONG_TYPE_SCORES,
    EMPTY_TEXT_RESPONSE,
    CONTENT_FILTER_REFUSAL_TEXT,
    PROVIDER_UNAVAILABLE_ERROR,
    # Partial responses
    PARTIAL_AGGREGATE_ONLY_TOPICS,
    PARTIAL_INDIVIDUAL_ONLY_SCORES,
    # Edge cases
    EDGE_CASE_MAX_SCORES,
    EDGE_CASE_MIN_SCORES,
    EDGE_CASE_OVERFLOW_SCORES,
    EDGE_CASE_NEGATIVE_SCORES,
    EDGE_CASE_UNICODE,
    # Usage info
    USAGE_INFO_ANTHROPIC,
    USAGE_INFO_OPENAI,
    USAGE_INFO_OLLAMA,
    # Factory functions
    wrap_in_json_text,
    make_individual_grading,
    make_aggregate_analysis,
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
    # Docker mocks
    "MockDockerClient",
    "MockDockerContainer",
    "MockDockerCommandResult",
    "MockDockerImage",
    "MockDockerModule",
    # LLM response fixtures
    "VALID_AGGREGATE_ANALYSIS",
    "AGGREGATE_ANALYSIS_MINIMAL",
    "AGGREGATE_ANALYSIS_EMPTY_TOPICS",
    "VALID_INDIVIDUAL_GRADING_HIGH",
    "VALID_INDIVIDUAL_GRADING_MEDIUM",
    "VALID_INDIVIDUAL_GRADING_LOW",
    "VALID_INDIVIDUAL_GRADING_NEEDS_SUPPORT",
    "INDIVIDUAL_GRADING_MINIMAL",
    "VALID_QUESTION_CONSOLIDATION",
    "QUESTION_CONSOLIDATION_EMPTY",
    "QUESTION_CONSOLIDATION_SINGLE",
    "MALFORMED_JSON_STRING",
    "MALFORMED_JSON_UNCLOSED",
    "MALFORMED_WRONG_TYPE_ROOT",
    "MALFORMED_WRONG_TYPE_SCORES",
    "EMPTY_TEXT_RESPONSE",
    "CONTENT_FILTER_REFUSAL_TEXT",
    "PROVIDER_UNAVAILABLE_ERROR",
    "PARTIAL_AGGREGATE_ONLY_TOPICS",
    "PARTIAL_INDIVIDUAL_ONLY_SCORES",
    "EDGE_CASE_MAX_SCORES",
    "EDGE_CASE_MIN_SCORES",
    "EDGE_CASE_OVERFLOW_SCORES",
    "EDGE_CASE_NEGATIVE_SCORES",
    "EDGE_CASE_UNICODE",
    "USAGE_INFO_ANTHROPIC",
    "USAGE_INFO_OPENAI",
    "USAGE_INFO_OLLAMA",
    "wrap_in_json_text",
    "make_individual_grading",
    "make_aggregate_analysis",
]
