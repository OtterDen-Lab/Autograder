"""
Text Submission Grading Package

This package provides extensible text submission grading with a 3-phase approach:
1. Aggregate Analysis - Identify core topics, misconceptions, and student questions
2. Individual Grading - Grade each submission for engagement, relevance, and quality
3. Report Generation - Generate comprehensive insights and recommendations

Main Classes:
- BaseTextSubmissionGrader: Extensible base class for text grading
- WeeklyStudyNotesGrader: Concrete implementation for weekly study notes
- TextSubmissionGrader: Backward-compatible alias

Supporting Classes:
- SubmissionPIIRedactor: PII redaction for submissions
- ScoreCalculator: Score normalization and calculation
- RubricGenerator: Student-facing rubric feedback
- BatchProcessor: Coordinates the grading workflow
- AggregateAnalyzer: Phase 1 aggregate analysis
- IndividualGradingProcessor: Phase 2 individual grading
- QuestionConsolidator: Groups similar student questions
- IndividualSubmissionAnalyzer: AI grading for single submission
- ReportCompiler: Compiles report data
- ReportPresenter: Displays report output

Prompt Functions:
- get_aggregate_analysis_prompt: Phase 1 prompt
- get_individual_grading_prompt: Phase 2 prompt
- get_question_consolidation_prompt: Question grouping prompt

To create a custom text grader:
1. Subclass BaseTextSubmissionGrader
2. Override prompt methods (_build_*_prompt) for different grading criteria
3. Override hook methods for customization
4. Register with @GraderRegistry.register("YourGrader")

Example:
    from Autograder.graders.text_submission import BaseTextSubmissionGrader
    from Autograder.registry import GraderRegistry

    @GraderRegistry.register("MyCustomTextGrader")
    class MyCustomTextGrader(BaseTextSubmissionGrader):
        def _build_individual_grading_prompt(self, submission_text, core_topics):
            # Custom grading rubric/criteria
            return f"Grade this submission: {submission_text}"

        def add_manual_topics_hook(self, ai_topics):
            # Always include course-specific topics
            return ai_topics + ["My Required Topic"]
"""

# PII redaction
from .pii import SubmissionPIIRedactor

# Scoring and rubric
from .scoring import (
    ScoreCalculator,
    RubricGenerator,
    DEFAULT_WORD_THRESHOLD,
    DEFAULT_RUBRIC_TOTAL,
    ENGAGEMENT_POINTS,
    LENGTH_POINTS,
    RELEVANCE_POINTS,
    EXPLANATION_QUALITY_POINTS,
)

# Prompts
from .prompts import (
    get_aggregate_analysis_prompt,
    get_individual_grading_prompt,
    get_question_consolidation_prompt,
    DEFAULT_MAX_TOPICS,
    DEFAULT_MAX_WORDS,
    DEFAULT_MAX_CHARACTERS,
)

# Processors
from .processors import (
    BatchProcessor,
    AggregateAnalyzer,
    IndividualGradingProcessor,
    QuestionConsolidator,
    IndividualSubmissionAnalyzer,
)

# Reports
from .reports import ReportCompiler, ReportPresenter

# Base grader class
from .base import BaseTextSubmissionGrader

__all__ = [
    # Main classes
    "BaseTextSubmissionGrader",
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
