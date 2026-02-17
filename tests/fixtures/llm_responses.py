"""
LLM API response fixtures for testing.

Provides realistic mock responses for:
- Aggregate analysis (Phase 1)
- Individual grading (Phase 2)
- Question consolidation (Phase 2.5)

Also includes malformed and edge case responses for error handling tests.
"""

import json
from typing import Any, Dict

# =============================================================================
# AGGREGATE ANALYSIS RESPONSES (Phase 1)
# =============================================================================

VALID_AGGREGATE_ANALYSIS = {
    "common_themes": "Students demonstrated strong understanding of process scheduling concepts, particularly round-robin and priority-based algorithms. Many discussed real-world applications.",
    "commonly_misunderstood_topics": [
        "Deadlock prevention vs avoidance",
        "Virtual memory page replacement algorithms"
    ],
    "misconception_details": "Several students confused deadlock prevention (eliminating one of the four conditions) with deadlock avoidance (using algorithms like Banker's to stay in safe states).",
    "key_insights": "The class shows good engagement with practical examples. Using OS simulator exercises improved understanding of context switching.",
    "teaching_feedback": "Consider adding more worked examples for deadlock scenarios. The virtual memory section may benefit from visual diagrams.",
    "core_topics": [
        "Process Scheduling",
        "Context Switching",
        "Deadlock",
        "Memory Management"
    ],
    "related_topics": [
        "CPU Utilization",
        "Throughput",
        "Response Time",
        "Page Faults"
    ],
    "off_topic_indicators": [
        "personal anecdotes unrelated to course",
        "copy-pasted definitions without explanation"
    ],
    "student_questions": [
        "How does the scheduler handle I/O-bound vs CPU-bound processes?",
        "What happens when all processes are in deadlock?",
        "Is there a way to measure context switch overhead?"
    ]
}

AGGREGATE_ANALYSIS_MINIMAL = {
    "common_themes": "Basic understanding shown.",
    "commonly_misunderstood_topics": [],
    "misconception_details": "",
    "key_insights": "",
    "teaching_feedback": "",
    "core_topics": ["Processes"],
    "related_topics": [],
    "off_topic_indicators": [],
    "student_questions": []
}

AGGREGATE_ANALYSIS_EMPTY_TOPICS = {
    "common_themes": "No clear themes identified.",
    "commonly_misunderstood_topics": [],
    "misconception_details": "",
    "key_insights": "",
    "teaching_feedback": "",
    "core_topics": [],
    "related_topics": [],
    "off_topic_indicators": [],
    "student_questions": []
}

# =============================================================================
# INDIVIDUAL GRADING RESPONSES (Phase 2)
# =============================================================================

VALID_INDIVIDUAL_GRADING_HIGH = {
    "engagement_score": 4,
    "relevance_score": 2,
    "explanation_quality_score": 2,
    "topics_covered": ["Process Scheduling", "Deadlock", "Memory Management"],
    "topics_missing": [],
    "topics_needing_review": [],
    "off_topic_content": "",
    "misconception_notes": "",
    "needs_support": False,
    "support_reason": "",
    "feedback": "Excellent analysis of process scheduling concepts. Your explanation of deadlock prevention strategies was particularly clear and well-reasoned."
}

VALID_INDIVIDUAL_GRADING_MEDIUM = {
    "engagement_score": 3,
    "relevance_score": 1,
    "explanation_quality_score": 1,
    "topics_covered": ["Process Scheduling"],
    "topics_missing": ["Deadlock", "Memory Management"],
    "topics_needing_review": ["Context Switching"],
    "off_topic_content": "",
    "misconception_notes": "Some confusion about preemptive vs non-preemptive scheduling.",
    "needs_support": False,
    "support_reason": "",
    "feedback": "Good start on scheduling concepts. Consider expanding your analysis to include more topics from the week's material."
}

VALID_INDIVIDUAL_GRADING_LOW = {
    "engagement_score": 1,
    "relevance_score": 0,
    "explanation_quality_score": 0,
    "topics_covered": [],
    "topics_missing": ["Process Scheduling", "Deadlock", "Memory Management"],
    "topics_needing_review": [],
    "off_topic_content": "Submission discussed unrelated personal matters.",
    "misconception_notes": "",
    "needs_support": True,
    "support_reason": "Student may need help understanding the assignment requirements.",
    "feedback": "This submission doesn't address the course topics. Please review the assignment instructions and reach out if you need clarification."
}

VALID_INDIVIDUAL_GRADING_NEEDS_SUPPORT = {
    "engagement_score": 2,
    "relevance_score": 1,
    "explanation_quality_score": 0,
    "topics_covered": ["Process Scheduling"],
    "topics_missing": ["Deadlock", "Memory Management"],
    "topics_needing_review": ["Process Scheduling", "Context Switching"],
    "off_topic_content": "",
    "misconception_notes": "Student appears to have significant gaps in understanding core concepts.",
    "needs_support": True,
    "support_reason": "Multiple fundamental misconceptions about process states and scheduling algorithms.",
    "feedback": "There are some areas where additional review would be helpful. Consider attending office hours to discuss process scheduling concepts."
}

INDIVIDUAL_GRADING_MINIMAL = {
    "engagement_score": 3,
    "relevance_score": 1,
    "explanation_quality_score": 1,
    "topics_covered": [],
    "topics_missing": [],
    "topics_needing_review": [],
    "off_topic_content": "",
    "misconception_notes": "",
    "needs_support": False,
    "support_reason": "",
    "feedback": "Acceptable submission."
}

# =============================================================================
# QUESTION CONSOLIDATION RESPONSES (Phase 2.5)
# =============================================================================

VALID_QUESTION_CONSOLIDATION = {
    "consolidated_questions": [
        {
            "canonical_question": "How does the OS scheduler decide which process to run next?",
            "original_questions": [
                "What algorithm does the scheduler use?",
                "How does scheduling work?",
                "When does a context switch happen?"
            ],
            "topic": "Process Scheduling"
        },
        {
            "canonical_question": "What are the four conditions necessary for deadlock?",
            "original_questions": [
                "How does deadlock occur?",
                "What causes deadlock?"
            ],
            "topic": "Deadlock"
        },
        {
            "canonical_question": "How does virtual memory work with limited physical RAM?",
            "original_questions": [
                "What happens when we run out of memory?",
                "How does paging work?"
            ],
            "topic": "Memory Management"
        }
    ]
}

QUESTION_CONSOLIDATION_EMPTY = {
    "consolidated_questions": []
}

QUESTION_CONSOLIDATION_SINGLE = {
    "consolidated_questions": [
        {
            "canonical_question": "What is the difference between a process and a thread?",
            "original_questions": [
                "Processes vs threads?"
            ],
            "topic": "Concurrency"
        }
    ]
}

# =============================================================================
# MALFORMED RESPONSES (for error handling tests)
# =============================================================================

MALFORMED_JSON_STRING = "{ this is not valid json"

MALFORMED_JSON_UNCLOSED = '{"core_topics": ["Process Scheduling"'

MALFORMED_JSON_TRAILING_COMMA = '{"core_topics": ["Process Scheduling",]}'

# Valid JSON but wrong structure
MALFORMED_WRONG_TYPE_ROOT = ["not", "an", "object"]

MALFORMED_WRONG_TYPE_SCORES = {
    "engagement_score": "four",  # Should be int
    "relevance_score": "two",    # Should be int
    "explanation_quality_score": "high",  # Should be int
    "topics_covered": "Process Scheduling",  # Should be list
    "needs_support": "yes",  # Should be bool
    "feedback": 12345  # Should be string
}

# Model returned syntactically valid but unusable outputs
EMPTY_TEXT_RESPONSE = ""
CONTENT_FILTER_REFUSAL_TEXT = (
    "I’m unable to help with this request due to content policy restrictions."
)
PROVIDER_UNAVAILABLE_ERROR = "503 Service Unavailable"

# =============================================================================
# PARTIAL RESPONSES (missing fields)
# =============================================================================

PARTIAL_AGGREGATE_ONLY_TOPICS = {
    "core_topics": ["Process Scheduling", "Memory Management"]
    # Missing all other fields
}

PARTIAL_INDIVIDUAL_ONLY_SCORES = {
    "engagement_score": 3,
    "relevance_score": 1,
    "explanation_quality_score": 1
    # Missing all other fields
}

PARTIAL_QUESTION_MISSING_FIELDS = {
    "consolidated_questions": [
        {
            "canonical_question": "How does scheduling work?"
            # Missing original_questions and topic
        }
    ]
}

# =============================================================================
# EDGE CASE RESPONSES
# =============================================================================

# Scores at boundaries
EDGE_CASE_MAX_SCORES = {
    "engagement_score": 4,
    "relevance_score": 2,
    "explanation_quality_score": 2,
    "topics_covered": [],
    "topics_missing": [],
    "topics_needing_review": [],
    "off_topic_content": "",
    "misconception_notes": "",
    "needs_support": False,
    "support_reason": "",
    "feedback": ""
}

EDGE_CASE_MIN_SCORES = {
    "engagement_score": 0,
    "relevance_score": 0,
    "explanation_quality_score": 0,
    "topics_covered": [],
    "topics_missing": [],
    "topics_needing_review": [],
    "off_topic_content": "",
    "misconception_notes": "",
    "needs_support": False,
    "support_reason": "",
    "feedback": ""
}

# Scores exceeding valid range (should be clamped)
EDGE_CASE_OVERFLOW_SCORES = {
    "engagement_score": 10,  # Max is 4
    "relevance_score": 5,    # Max is 2
    "explanation_quality_score": 100,  # Max is 2
    "topics_covered": [],
    "topics_missing": [],
    "topics_needing_review": [],
    "off_topic_content": "",
    "misconception_notes": "",
    "needs_support": False,
    "support_reason": "",
    "feedback": ""
}

# Negative scores (should be clamped to 0)
EDGE_CASE_NEGATIVE_SCORES = {
    "engagement_score": -1,
    "relevance_score": -5,
    "explanation_quality_score": -100,
    "topics_covered": [],
    "topics_missing": [],
    "topics_needing_review": [],
    "off_topic_content": "",
    "misconception_notes": "",
    "needs_support": False,
    "support_reason": "",
    "feedback": ""
}

# Very long strings
EDGE_CASE_LONG_FEEDBACK = {
    "engagement_score": 3,
    "relevance_score": 1,
    "explanation_quality_score": 1,
    "topics_covered": ["Topic " + str(i) for i in range(50)],
    "topics_missing": [],
    "topics_needing_review": [],
    "off_topic_content": "",
    "misconception_notes": "",
    "needs_support": False,
    "support_reason": "",
    "feedback": "A" * 10000  # Very long feedback
}

# Unicode content
EDGE_CASE_UNICODE = {
    "engagement_score": 3,
    "relevance_score": 1,
    "explanation_quality_score": 1,
    "topics_covered": ["Procesos", "管理", "Speicherverwaltung"],
    "topics_missing": [],
    "topics_needing_review": [],
    "off_topic_content": "",
    "misconception_notes": "学生需要更多练习",
    "needs_support": False,
    "support_reason": "",
    "feedback": "Buen trabajo con los conceptos de planificación. 继续努力！"
}

# =============================================================================
# USAGE INFO FIXTURES
# =============================================================================

USAGE_INFO_ANTHROPIC = {
    "prompt_tokens": 1500,
    "completion_tokens": 500,
    "total_tokens": 2000,
    "provider": "anthropic",
    "model": "claude-sonnet-4-5-20250514"
}

USAGE_INFO_OPENAI = {
    "prompt_tokens": 1200,
    "completion_tokens": 400,
    "total_tokens": 1600,
    "provider": "openai",
    "model": "gpt-4.1-mini"
}

USAGE_INFO_OLLAMA = {
    "prompt_tokens": 1000,
    "completion_tokens": 300,
    "total_tokens": 1300,
    "provider": "ollama",
    "model": "qwen3:14b"
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def wrap_in_json_text(payload: Dict[str, Any], prefix: str = "", suffix: str = "") -> str:
    """Wrap a payload in text as an LLM might return it.

    Simulates how Anthropic returns JSON embedded in text.

    Args:
        payload: The JSON payload
        prefix: Text before the JSON (e.g., "Here's my analysis:")
        suffix: Text after the JSON (e.g., "Let me know if you need more details.")

    Returns:
        String with JSON embedded in surrounding text
    """
    json_str = json.dumps(payload, indent=2)
    parts = [prefix, json_str, suffix]
    return "\n".join(p for p in parts if p)


def make_individual_grading(
    *,
    engagement: int = 3,
    relevance: int = 1,
    explanation: int = 1,
    topics_covered: list = None,
    topics_missing: list = None,
    needs_support: bool = False,
    feedback: str = "Good work."
) -> Dict[str, Any]:
    """Factory function to create individual grading responses.

    Args:
        engagement: Engagement score (0-4)
        relevance: Relevance score (0-2)
        explanation: Explanation quality score (0-2)
        topics_covered: List of covered topics
        topics_missing: List of missing topics
        needs_support: Whether student needs support
        feedback: Feedback text

    Returns:
        Individual grading response dict
    """
    return {
        "engagement_score": engagement,
        "relevance_score": relevance,
        "explanation_quality_score": explanation,
        "topics_covered": topics_covered or [],
        "topics_missing": topics_missing or [],
        "topics_needing_review": [],
        "off_topic_content": "",
        "misconception_notes": "",
        "needs_support": needs_support,
        "support_reason": "Needs additional help" if needs_support else "",
        "feedback": feedback
    }


def make_aggregate_analysis(
    *,
    core_topics: list = None,
    related_topics: list = None,
    themes: str = "",
    questions: list = None
) -> Dict[str, Any]:
    """Factory function to create aggregate analysis responses.

    Args:
        core_topics: List of core topics
        related_topics: List of related topics
        themes: Common themes string
        questions: Student questions

    Returns:
        Aggregate analysis response dict
    """
    return {
        "common_themes": themes,
        "commonly_misunderstood_topics": [],
        "misconception_details": "",
        "key_insights": "",
        "teaching_feedback": "",
        "core_topics": core_topics or [],
        "related_topics": related_topics or [],
        "off_topic_indicators": [],
        "student_questions": questions or []
    }
