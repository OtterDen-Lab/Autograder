#!env python
"""
Text Submission Grader for Weekly Study Notes

This module provides graders for text-based submissions using a 3-phase approach:
1. Aggregate Analysis - Identify core topics, common misconceptions, and student questions
2. Individual Grading - Grade each submission for engagement, relevance, and explanation quality
3. Report Generation - Generate comprehensive insights and recommendations for instruction

Grading Philosophy:
- Students are graded on effort and engagement, not correctness
- A good faith effort typically results in at least 6/10
- Confusion is not penalized; lack of effort is
- Verbosity is acceptable if the student is genuinely engaging with the material

This file provides backwards compatibility by re-exporting from the
text_submission package. For new code, import directly from:
    from Autograder.graders.text_submission import BaseTextSubmissionGrader

To create a custom text grader, subclass BaseTextSubmissionGrader and override:
- _build_aggregate_analysis_prompt(): Different aggregate analysis criteria
- _build_individual_grading_prompt(): Different grading rubric/criteria
- _build_question_consolidation_prompt(): Different question handling
- add_manual_topics_hook(): Add/modify topics after AI analysis
- output_report_hook(): Custom report delivery
"""

from Autograder.registry import GraderRegistry

# Import from the text_submission package for backwards compatibility
from Autograder.graders.text_submission import (
    # Base class
    BaseTextSubmissionGrader,
    # PII
    SubmissionPIIRedactor,
    # Scoring
    ScoreCalculator,
    RubricGenerator,
    # Processors
    BatchProcessor,
    AggregateAnalyzer,
    IndividualGradingProcessor,
    QuestionConsolidator,
    IndividualSubmissionAnalyzer,
    # Reports
    ReportCompiler,
    ReportPresenter,
    # Prompts
    get_aggregate_analysis_prompt,
    get_individual_grading_prompt,
    get_question_consolidation_prompt,
    # Constants
    DEFAULT_MAX_TOPICS,
    DEFAULT_WORD_THRESHOLD,
    DEFAULT_RUBRIC_TOTAL,
    DEFAULT_MAX_WORDS,
    DEFAULT_MAX_CHARACTERS,
    ENGAGEMENT_POINTS,
    LENGTH_POINTS,
    RELEVANCE_POINTS,
    EXPLANATION_QUALITY_POINTS,
)


@GraderRegistry.register("WeeklyStudyNotesGrader")
class WeeklyStudyNotesGrader(BaseTextSubmissionGrader):
    """
    Concrete weekly-study-notes implementation of the text grading pipeline.

    This grader is optimized for weekly study notes where students:
    - List topics covered in class
    - Explain what each topic is and why it matters
    - Note anything unclear

    The grading rubric focuses on engagement, relevance, and explanation quality
    rather than correctness, encouraging students to process material in their
    own words.
    """
    COMPATIBLE_KINDS = {"TextAssignment"}


@GraderRegistry.register("TextSubmissionGrader")
class TextSubmissionGrader(WeeklyStudyNotesGrader):
    """
    Backward-compatible alias for the weekly study notes grader.

    Use WeeklyStudyNotesGrader for new configurations.
    """
    COMPATIBLE_KINDS = {"TextAssignment"}


# Re-export all for backwards compatibility
__all__ = [
    # Main grader classes
    "BaseTextSubmissionGrader",
    "WeeklyStudyNotesGrader",
    "TextSubmissionGrader",
    # PII
    "SubmissionPIIRedactor",
    # Scoring
    "ScoreCalculator",
    "RubricGenerator",
    # Processors
    "BatchProcessor",
    "AggregateAnalyzer",
    "IndividualGradingProcessor",
    "QuestionConsolidator",
    "IndividualSubmissionAnalyzer",
    # Reports
    "ReportCompiler",
    "ReportPresenter",
    # Prompts
    "get_aggregate_analysis_prompt",
    "get_individual_grading_prompt",
    "get_question_consolidation_prompt",
    # Constants
    "DEFAULT_MAX_TOPICS",
    "DEFAULT_WORD_THRESHOLD",
    "DEFAULT_RUBRIC_TOTAL",
    "DEFAULT_MAX_WORDS",
    "DEFAULT_MAX_CHARACTERS",
    "ENGAGEMENT_POINTS",
    "LENGTH_POINTS",
    "RELEVANCE_POINTS",
    "EXPLANATION_QUALITY_POINTS",
]
