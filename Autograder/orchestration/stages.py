"""
Grading stage implementations.

Each stage represents a distinct phase of the grading process:
- Prepare: Fetch submissions from LMS
- Grade: Run graders on submissions
- Publish: Push feedback to LMS
"""

import logging
from typing import Dict

from Autograder import exceptions as autograder_exceptions
from Autograder.reporting.contracts import (
    PrepareStageResult,
    GradeStageResult,
    PublishStageResult,
)
from .helpers import is_lms_exception, format_submission_for_log

log = logging.getLogger(__name__)


def run_prepare_stage(grader, grading_assignment, args, settings,
                      do_regrade: bool) -> PrepareStageResult:
    """
    Execute the prepare stage: fetch submissions from LMS.

    Args:
        grader: Grader instance
        grading_assignment: Assignment instance
        args: Command line arguments
        settings: Grader settings
        do_regrade: Whether to regrade already-graded submissions

    Returns:
        PrepareStageResult with submission counts and status
    """
    needed_preparation = grader.assignment_needs_preparation()
    if needed_preparation:
        try:
            grading_assignment.prepare(limit=args.limit,
                                       do_regrade=do_regrade,
                                       test=args.test,
                                       **settings)
        except Exception as e:
            assignment_obj = getattr(grading_assignment, "lms_assignment", None)
            assignment_name = getattr(assignment_obj, "name", "unknown")
            assignment_id = getattr(assignment_obj, "id", "unknown")
            if is_lms_exception(e):
                raise autograder_exceptions.LMSError(
                    f"Failed to fetch submissions from Canvas for assignment "
                    f"'{assignment_name}' (id={assignment_id}). "
                    "Verify Canvas API access, assignment ID, and network connectivity."
                ) from e
            raise

    submission_count = len(grading_assignment.submissions)
    has_submissions = submission_count > 0
    return PrepareStageResult(
        needed_preparation=needed_preparation,
        submission_count=submission_count,
        has_submissions=has_submissions,
        skipped_reason=None if has_submissions else "no_submissions",
    )


def run_grade_stage(grader, grading_assignment, settings, assignment_data,
                    args, do_regrade: bool) -> GradeStageResult:
    """
    Execute the grade stage: run graders on all submissions.

    Args:
        grader: Grader instance
        grading_assignment: Assignment instance
        settings: Grader settings
        assignment_data: Assignment run request data
        args: Command line arguments
        do_regrade: Whether to regrade already-graded submissions

    Returns:
        GradeStageResult with graded counts
    """
    grader.grade_assignment(grading_assignment,
                            **settings,
                            reveal_identity=assignment_data.reveal_identity,
                            privacy_mode=assignment_data.privacy_mode,
                            do_regrade=do_regrade)

    for submission in grading_assignment.submissions:
        log.debug(
            format_submission_for_log(
                submission, reveal_identity=assignment_data.reveal_identity))

    graded_count = sum(1 for s in grading_assignment.submissions
                       if getattr(s, "feedback", None) is not None)
    return GradeStageResult(submission_count=len(grading_assignment.submissions),
                            graded_count=graded_count)


def run_publish_stage(grader, grading_assignment, args, push_grades: bool,
                      assignment_data, record_retention: bool,
                      settings: Dict) -> PublishStageResult:
    """
    Execute the publish stage: push feedback to LMS.

    Args:
        grader: Grader instance
        grading_assignment: Assignment instance
        args: Command line arguments
        push_grades: Whether to push grades to LMS
        assignment_data: Assignment run request data
        record_retention: Whether to save feedback records
        settings: Grader settings (for records_dir)

    Returns:
        PublishStageResult with finalization status
    """
    if not grader.ready_to_finalize:
        return PublishStageResult(finalized=False,
                                  finalize_summary=None,
                                  skipped_reason="grader_not_ready")

    finalize_kwargs = {
        "push": push_grades,
        "idempotency_key": assignment_data.idempotency_key,
        "idempotency_state_dir": assignment_data.idempotency_state_dir,
    }
    if record_retention:
        records_dir = settings.get('records_dir')
        finalize_kwargs.update({
            "record_retention": record_retention,
            "records_dir": records_dir
        })

    finalize_summary = grading_assignment.finalize(**finalize_kwargs)
    return PublishStageResult(finalized=True, finalize_summary=finalize_summary)
