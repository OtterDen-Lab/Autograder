"""
Report generation and result summarization for the autograder.
"""

import argparse
import json
import logging
import os
from datetime import datetime
from typing import Dict, List

log = logging.getLogger(__name__)


def collect_push_failure_lines(results: List[Dict]) -> tuple[int, List[str]]:
    """
    Collect push failure information from grading results.

    Args:
        results: List of grading result dictionaries

    Returns:
        Tuple of (total_failed_pushes, list of failure description lines)
    """
    lines = []
    total_failed_pushes = 0
    for result in results:
        summary = result.get("finalize_summary") or {}
        failed_count = int(summary.get("push_failed", 0) or 0)
        if failed_count <= 0:
            continue

        total_failed_pushes += failed_count
        assignment_label = (result.get('assignment_name')
                            or f"ID {result.get('assignment_id')}")
        course_label = result.get('course_name') or "Unknown Course"
        failed_students = summary.get("push_failed_students") or []
        failed_students_preview = ", ".join(failed_students[:5])
        if len(failed_students) > 5:
            failed_students_preview += ", ..."
        if failed_students_preview:
            lines.append(
                f"- {course_label} / {assignment_label}: {failed_count} push failure(s) [{failed_students_preview}]"
            )
        else:
            lines.append(
                f"- {course_label} / {assignment_label}: {failed_count} push failure(s)"
            )

    return total_failed_pushes, lines


def collect_push_skipped_ungraded_lines(results: List[Dict]) -> tuple[int, List[str]]:
    """
    Collect skipped-ungraded push information from grading results.

    Args:
        results: List of grading result dictionaries

    Returns:
        Tuple of (total_skipped_ungraded_pushes, list of description lines)
    """
    lines = []
    total_skipped_ungraded = 0
    for result in results:
        summary = result.get("finalize_summary") or {}
        skipped_count = int(summary.get("push_skipped_ungraded", 0) or 0)
        if skipped_count <= 0:
            continue

        total_skipped_ungraded += skipped_count
        assignment_label = (result.get('assignment_name')
                            or f"ID {result.get('assignment_id')}")
        course_label = result.get('course_name') or "Unknown Course"
        skipped_students = summary.get("push_skipped_ungraded_students") or []
        skipped_preview = ", ".join(skipped_students[:5])
        if len(skipped_students) > 5:
            skipped_preview += ", ..."
        if skipped_preview:
            lines.append(
                f"- {course_label} / {assignment_label}: {skipped_count} ungraded skip(s) [{skipped_preview}]"
            )
        else:
            lines.append(
                f"- {course_label} / {assignment_label}: {skipped_count} ungraded skip(s)"
            )

    return total_skipped_ungraded, lines


def summarize_stage_contracts(results: List[Dict]) -> Dict:
    """
    Aggregate stage timing and counts from all results.

    Args:
        results: List of grading result dictionaries

    Returns:
        Summary dictionary with aggregated stage metrics
    """
    summary = {
        "prepare": {
            "count": 0,
            "total_duration_ms": 0,
            "total_submission_count": 0,
        },
        "grade": {
            "count": 0,
            "total_duration_ms": 0,
            "total_submission_count": 0,
            "total_graded_count": 0,
        },
        "publish": {
            "count": 0,
            "total_duration_ms": 0,
            "total_push_attempted": 0,
            "total_push_succeeded": 0,
            "total_push_failed": 0,
            "total_push_skipped": 0,
            "total_push_skipped_ungraded": 0,
        },
    }

    for result in results:
        stage_contract = result.get("stage_contract") or {}
        prepare = stage_contract.get("prepare")
        grade = stage_contract.get("grade")
        publish = stage_contract.get("publish")

        if isinstance(prepare, dict):
            summary["prepare"]["count"] += 1
            summary["prepare"]["total_duration_ms"] += int(
                prepare.get("duration_ms", 0) or 0)
            summary["prepare"]["total_submission_count"] += int(
                prepare.get("submission_count", 0) or 0)

        if isinstance(grade, dict):
            summary["grade"]["count"] += 1
            summary["grade"]["total_duration_ms"] += int(
                grade.get("duration_ms", 0) or 0)
            summary["grade"]["total_submission_count"] += int(
                grade.get("submission_count", 0) or 0)
            summary["grade"]["total_graded_count"] += int(
                grade.get("graded_count", 0) or 0)

        if isinstance(publish, dict):
            summary["publish"]["count"] += 1
            summary["publish"]["total_duration_ms"] += int(
                publish.get("duration_ms", 0) or 0)
            finalize_summary = publish.get("finalize_summary") or {}
            if isinstance(finalize_summary, dict):
                summary["publish"]["total_push_attempted"] += int(
                    finalize_summary.get("push_attempted", 0) or 0)
                summary["publish"]["total_push_succeeded"] += int(
                    finalize_summary.get("push_succeeded", 0) or 0)
                summary["publish"]["total_push_failed"] += int(
                    finalize_summary.get("push_failed", 0) or 0)
                summary["publish"]["total_push_skipped"] += int(
                    finalize_summary.get("push_skipped", 0) or 0)
                summary["publish"]["total_push_skipped_ungraded"] += int(
                    finalize_summary.get("push_skipped_ungraded", 0) or 0)

    for stage in ("prepare", "grade", "publish"):
        count = int(summary[stage]["count"])
        total = int(summary[stage]["total_duration_ms"])
        summary[stage]["avg_duration_ms"] = int(total / count) if count else 0

    return summary


def print_results_summary(results: List[Dict]) -> None:
    """
    Print summary of grading results to the log.

    Args:
        results: List of grading result dictionaries
    """
    successful = sum(1 for r in results if r['success'])
    failed = len(results) - successful

    log.info(f"Grading completed: {successful} successful, {failed} failed")

    if failed > 0:
        log.error("The following assignments failed:")
        for result in results:
            if not result['success']:
                error_type = result.get("error_type")
                if error_type:
                    log.error(
                        f"  Assignment {result['assignment_id']} [{error_type}]: {result['error']}"
                    )
                else:
                    log.error(
                        f"  Assignment {result['assignment_id']}: {result['error']}"
                    )

    push_failed_total, push_failure_lines = collect_push_failure_lines(results)
    skipped_ungraded_total, skipped_ungraded_lines = (
        collect_push_skipped_ungraded_lines(results))
    if push_failed_total > 0:
        log.error(
            f"Detected {push_failed_total} per-student push failure(s) across successful assignments. "
            "This run will return a non-zero exit code."
        )
        for line in push_failure_lines:
            log.error(line)
    if skipped_ungraded_total > 0:
        log.warning(
            f"Detected {skipped_ungraded_total} per-student ungraded skip(s) across successful assignments."
        )
        for line in skipped_ungraded_lines:
            log.warning(line)


def _format_seconds(duration_ms: int) -> str:
    """Format milliseconds as seconds with 3 decimal places."""
    duration_ms = int(duration_ms or 0)
    whole = duration_ms // 1000
    remainder = duration_ms % 1000
    return f"{whole}.{remainder:03d}s"


def print_stage_timing_summary(results: List[Dict]) -> None:
    """
    Print detailed stage timing summary to the log.

    Args:
        results: List of grading result dictionaries
    """
    stage_summary = summarize_stage_contracts(results)
    prepare = stage_summary.get("prepare", {})
    grade = stage_summary.get("grade", {})
    publish = stage_summary.get("publish", {})

    log.info("Aggregate stage timing summary (s):")
    log.info(
        f"  Prepare: count={prepare.get('count', 0)}, total={_format_seconds(prepare.get('total_duration_ms', 0))}, avg={_format_seconds(prepare.get('avg_duration_ms', 0))}, submissions={prepare.get('total_submission_count', 0)}"
    )
    log.info(
        f"  Grade: count={grade.get('count', 0)}, total={_format_seconds(grade.get('total_duration_ms', 0))}, avg={_format_seconds(grade.get('avg_duration_ms', 0))}, submissions={grade.get('total_submission_count', 0)}, graded={grade.get('total_graded_count', 0)}"
    )
    log.info(
        f"  Publish: count={publish.get('count', 0)}, total={_format_seconds(publish.get('total_duration_ms', 0))}, avg={_format_seconds(publish.get('avg_duration_ms', 0))}, push_attempted={publish.get('total_push_attempted', 0)}, push_succeeded={publish.get('total_push_succeeded', 0)}, push_failed={publish.get('total_push_failed', 0)}, push_skipped={publish.get('total_push_skipped', 0)}, push_skipped_ungraded={publish.get('total_push_skipped_ungraded', 0)}"
    )

    log.info("Per-assignment stage timing summary (s):")
    for result in results:
        if not result.get("success"):
            continue
        stage_contract = result.get("stage_contract") or {}
        prepare_result = stage_contract.get("prepare") or {}
        grade_result = stage_contract.get("grade") or {}
        publish_result = stage_contract.get("publish") or {}
        finalize_summary = publish_result.get("finalize_summary") or {}

        assignment_label = (result.get("assignment_name")
                            or f"ID {result.get('assignment_id')}")
        course_label = result.get("course_name") or "Unknown Course"

        prepare_ms = int(prepare_result.get("duration_ms", 0) or 0)
        prepare_submissions = int(prepare_result.get("submission_count", 0) or 0)

        grade_ms = int(grade_result.get("duration_ms", 0) or 0)
        graded_count = int(grade_result.get("graded_count", 0) or 0)

        publish_ms = int(publish_result.get("duration_ms", 0) or 0)
        publish_state = ("finalized" if publish_result.get("finalized", False)
                         else f"skipped:{publish_result.get('skipped_reason')}")
        push_enabled = finalize_summary.get("push_enabled", False)
        push_attempted = int(finalize_summary.get("push_attempted", 0) or 0)
        push_failed = int(finalize_summary.get("push_failed", 0) or 0)
        push_skipped_ungraded = int(
            finalize_summary.get("push_skipped_ungraded", 0) or 0)

        log.info(
            f"  {course_label} / {assignment_label}: prepare={_format_seconds(prepare_ms)} (submissions={prepare_submissions}), "
            f"grade={_format_seconds(grade_ms)} (graded={graded_count}), publish={_format_seconds(publish_ms)} ({publish_state}, push_enabled={push_enabled}, "
            f"push_attempted={push_attempted}, push_failed={push_failed}, push_skipped_ungraded={push_skipped_ungraded})"
        )


def write_run_report(results: List[Dict], args: argparse.Namespace) -> None:
    """
    Write a JSON report of the grading run to a file.

    Args:
        results: List of grading result dictionaries
        args: Command line arguments (for report path)
    """
    if not args.report:
        return

    report_dir = os.path.dirname(os.path.abspath(args.report))
    if report_dir and not os.path.exists(report_dir):
        os.makedirs(report_dir, exist_ok=True)

    successful = sum(1 for r in results if r['success'])
    failed = len(results) - successful
    push_failed_total, push_failure_lines = collect_push_failure_lines(results)
    skipped_ungraded_total, skipped_ungraded_lines = (
        collect_push_skipped_ungraded_lines(results))
    stage_contract_summary = summarize_stage_contracts(results)

    report_payload = {
        "run_started_at": datetime.now().isoformat(timespec="seconds"),
        "yaml_path": args.yaml,
        "successful": successful,
        "failed": failed,
        "summary": {
            "assignment_failures": failed,
            "push_failures_total": push_failed_total,
            "push_failures": push_failure_lines,
            "push_skipped_ungraded_total": skipped_ungraded_total,
            "push_skipped_ungraded": skipped_ungraded_lines,
            "stage_contracts": stage_contract_summary,
        },
        "results": results,
    }

    with open(args.report, "w", encoding="utf-8") as report_file:
        json.dump(report_payload, report_file, indent=2)
