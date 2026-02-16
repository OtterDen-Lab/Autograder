"""
Slack notification integration for the autograder.
"""

import argparse
import logging
import os
from typing import Dict, List

import requests

from Autograder.config_models import RunConfig
from .reports import collect_push_failure_lines, collect_push_skipped_ungraded_lines

log = logging.getLogger(__name__)


def send_slack_run_summary(results: List[Dict], args: argparse.Namespace,
                           config: RunConfig) -> None:
    """
    Send a summary of the grading run to Slack.

    Only sends notifications based on the notify_on configuration:
    - "failures": Only notify when there are failures (default)
    - "always": Always notify

    Args:
        results: List of grading result dictionaries
        args: Command line arguments
        config: Run configuration
    """
    reporting_config = config.reporting
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    slack_channel = (args.error_slack_channel
                     or reporting_config.get("slack_channel")
                     or config.error_slack_channel
                     or os.getenv("ERROR_SLACK_CHANNEL"))

    if not slack_token or not slack_channel:
        log.warning(
            "Slack run summary not configured (missing SLACK_BOT_TOKEN or channel)."
        )
        return

    successful = sum(1 for r in results if r['success'])
    failed = len(results) - successful
    push_failed_total, push_failure_lines = collect_push_failure_lines(results)
    push_skipped_ungraded_total, push_skipped_ungraded_lines = (
        collect_push_skipped_ungraded_lines(results))
    notify_on = reporting_config.get("notify_on", "failures").lower()
    if (notify_on == "failures" and failed == 0 and push_failed_total == 0
            and push_skipped_ungraded_total == 0):
        return

    failure_lines = []
    for result in results:
        if not result['success']:
            assignment_label = (result.get('assignment_name')
                                or f"ID {result.get('assignment_id')}")
            course_label = result.get('course_name') or "Unknown Course"
            error_msg = result.get('error', 'Unknown error')
            error_type = result.get('error_type')
            if error_type:
                error_msg = f"[{error_type}] {error_msg}"
            failure_lines.append(
                f"- {course_label} / {assignment_label}: {error_msg}")

    message_lines = [
        f":warning: Grading run completed with {failed} assignment failure(s), {push_failed_total} per-student push failure(s), {push_skipped_ungraded_total} ungraded skip(s) ({successful} assignment(s) succeeded).",
        f"Config: `{args.yaml}`",
    ]
    if failure_lines:
        message_lines.append("Assignment failures:")
        message_lines.extend(failure_lines)
    if push_failure_lines:
        message_lines.append("Per-student push failures:")
        message_lines.extend(push_failure_lines)
    if push_skipped_ungraded_lines:
        message_lines.append("Per-student ungraded skips:")
        message_lines.extend(push_skipped_ungraded_lines)

    try:
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {slack_token}"},
            json={
                "channel": slack_channel,
                "text": "\n".join(message_lines),
                "mrkdwn": True,
                "unfurl_links": False,
                "unfurl_media": False
            },
            timeout=10)

        if not response.json().get('ok'):
            log.warning(
                f"Slack run summary failed: {response.json().get('error')}")
        else:
            log.info("Slack run summary sent successfully")
    except Exception as e:
        log.warning(f"Failed to send Slack run summary: {e}")
