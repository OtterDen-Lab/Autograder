"""
Reporting components for the autograder.

This module provides result summaries, Slack notifications, and report generation.
"""

from .contracts import PrepareStageResult, GradeStageResult, PublishStageResult
from .reports import (
    print_results_summary,
    print_stage_timing_summary,
    write_run_report,
    summarize_stage_contracts,
    collect_push_failure_lines,
)
from .slack import send_slack_run_summary, send_slack_test_notification

__all__ = [
    # Contracts
    "PrepareStageResult",
    "GradeStageResult",
    "PublishStageResult",
    # Reports
    "print_results_summary",
    "print_stage_timing_summary",
    "write_run_report",
    "summarize_stage_contracts",
    "collect_push_failure_lines",
    # Slack
    "send_slack_run_summary",
    "send_slack_test_notification",
]
