"""
Data contracts for grading stage results.

These dataclasses capture the outcome of each grading stage for
reporting and analysis purposes.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class PrepareStageResult:
    """Result of the prepare stage (fetching submissions)."""
    needed_preparation: bool
    submission_count: int
    has_submissions: bool
    skipped_reason: str | None = None
    duration_ms: int = 0


@dataclass
class GradeStageResult:
    """Result of the grade stage (running graders)."""
    submission_count: int
    graded_count: int
    duration_ms: int = 0


@dataclass
class PublishStageResult:
    """Result of the publish stage (pushing feedback to LMS)."""
    finalized: bool
    finalize_summary: Dict | None = None
    skipped_reason: str | None = None
    duration_ms: int = 0
