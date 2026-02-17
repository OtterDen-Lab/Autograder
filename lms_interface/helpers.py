#!env python

import argparse
import logging
from datetime import datetime, timezone
from typing import Any
from typing import List

import canvasapi

from lms_interface.canvas_interface import (
  CanvasAssignment,
  CanvasCourse,
  CanvasInterface,
)

# Configure logging to actually output
logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

def delete_empty_folders(canvas_course: CanvasCourse):
  log.info("delete_empty_folders")
  num_processed = 0
  for f in canvas_course.get_folders():
    try:
      f.delete()
      log.info(f"({num_processed}) : \"{f}\" deleted")
    except canvasapi.exceptions.BadRequest:
      log.info(f"({num_processed}) : \"{f}\" (not deleted) ({len(list(f.get_files()))})")
    num_processed += 1
  
def get_closed_assignments(interface: CanvasCourse) -> List[canvasapi.assignment.Assignment]:
  closed_assignments: List[canvasapi.assignment.Assignment] = []
  for assignment in interface.get_assignments(
      include=["all_dates"],
      order_by="name"
  ):
    if not assignment.published:
      continue
    if assignment.lock_at is not None:
      # Then it's the easy case because there's no overrides
      if datetime.fromisoformat(assignment.lock_at) < datetime.now(timezone.utc):
        # Then the assignment is past due
        closed_assignments.append(assignment)
        continue
    elif assignment.all_dates is not None:
      
      # First we need to figure out what the latest time this assignment could be available is
      # todo: This could be done on a per-student basis
      last_lock_datetime = None
      for dates_dict in assignment.all_dates:
        if dates_dict["lock_at"] is not None:
          lock_datetime = datetime.fromisoformat(dates_dict["lock_at"])
          if (last_lock_datetime is None) or (lock_datetime >= last_lock_datetime):
            last_lock_datetime = lock_datetime
      
      # If we have found a valid lock time, and it's in the past then we lock
      if last_lock_datetime is not None and last_lock_datetime <= datetime.now(timezone.utc):
        closed_assignments.append(assignment)
        continue
    
    else:
      log.warning(f"Cannot find any lock dates for assignment {assignment.name}!")
  
  return closed_assignments

def get_unsubmitted_submissions(interface: CanvasCourse, assignment: canvasapi.assignment.Assignment) -> List[
  canvasapi.submission.Submission]:
  submissions: List[canvasapi.submission.Submission] = list(
    filter(
      lambda s: s.submitted_at is None and s.percentage_score is None and not s.excused,
      assignment.get_submissions()
    )
  )
  return submissions

def clear_out_missing(interface: CanvasCourse):
  assignments = get_closed_assignments(interface)
  for assignment in assignments:
    missing_submissions = get_unsubmitted_submissions(interface, assignment)
    if not missing_submissions:
      continue
    log.info(
      f"Assignment: ({assignment.quiz_id if hasattr(assignment, 'quiz_id') else assignment.id}) {assignment.name} {assignment.published}"
    )
    for submission in missing_submissions:
      log.info(
        f"{submission.user_id} ({interface.get_username(submission.user_id)}) : {submission.workflow_state} : {submission.missing} : {submission.score} : {submission.grader_id} : {submission.graded_at}"
      )
      submission.edit(submission={"late_policy_status": "missing"})
    log.info("")


def _parse_canvas_datetime(value: Any) -> datetime | None:
  if value is None:
    return None
  if not isinstance(value, str):
    return None
  normalized = value.strip()
  if not normalized:
    return None
  if normalized.endswith("Z"):
    normalized = normalized[:-1] + "+00:00"
  try:
    dt = datetime.fromisoformat(normalized)
  except ValueError:
    return None
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(timezone.utc)


def _resolve_submission_due_at(
    assignment: canvasapi.assignment.Assignment,
    submission: canvasapi.submission.Submission
) -> datetime | None:
  # Most specific: submission-specific cached due date from Canvas.
  for value in (
      getattr(submission, "cached_due_date", None),
      getattr(submission, "due_at", None),
  ):
    parsed = _parse_canvas_datetime(value)
    if parsed is not None:
      return parsed

  # Sometimes the submission includes nested assignment metadata.
  submission_assignment = getattr(submission, "assignment", None)
  if isinstance(submission_assignment, dict):
    parsed = _parse_canvas_datetime(submission_assignment.get("due_at"))
    if parsed is not None:
      return parsed
  elif submission_assignment is not None:
    parsed = _parse_canvas_datetime(getattr(submission_assignment, "due_at", None))
    if parsed is not None:
      return parsed

  # Fallback to assignment-level due date.
  return _parse_canvas_datetime(getattr(assignment, "due_at", None))


def _submission_content_signals(submission: canvasapi.submission.Submission) -> list[str]:
  signals: list[str] = []

  submission_type = str(getattr(submission, "submission_type", "") or "").strip().lower()
  if submission_type and submission_type not in {"none", "on_paper", "not_graded"}:
    signals.append(f"submission_type={submission_type}")

  attachments = getattr(submission, "attachments", None)
  if isinstance(attachments, list) and len(attachments) > 0:
    signals.append(f"attachments={len(attachments)}")

  body = getattr(submission, "body", None)
  if isinstance(body, str) and body.strip():
    signals.append("body")

  url = getattr(submission, "url", None)
  if isinstance(url, str) and url.strip():
    signals.append("url")

  media_comment_id = getattr(submission, "media_comment_id", None)
  if media_comment_id not in (None, "", 0):
    signals.append("media_comment")

  return signals


def _normalize_late_policy_status(value: Any) -> str:
  if value is None:
    return "none"
  return str(value).strip().lower()


def _normalize_grade(value: Any) -> str:
  if value is None:
    return ""
  return str(value).strip().lower()


def _placeholder_grade_needs_clear(submission: canvasapi.submission.Submission) -> bool:
  grade = _normalize_grade(getattr(submission, "grade", None))
  posted_grade = _normalize_grade(getattr(submission, "posted_grade", None))
  entered_grade = _normalize_grade(getattr(submission, "entered_grade", None))
  score = getattr(submission, "score", None)

  grade_tokens = {grade, posted_grade, entered_grade}
  if any(token in {"incomplete", "fail", "f"} for token in grade_tokens):
    return True
  if any(token in {"0", "0.0", "0.00"} for token in grade_tokens):
    return True

  try:
    if score is not None and float(score) == 0.0:
      return True
  except Exception:
    pass

  return False


def cleanup_missing_by_due_date(
    canvas_course: CanvasCourse,
    *,
    dry_run: bool = False,
    include_unpublished: bool = False,
    assignment_id: int | None = None,
    limit: int | None = None,
    force_clear_stale_missing: bool = True,
    clear_placeholder_grade: bool = False,
    now: datetime | None = None
) -> dict[str, int]:
  """
  Normalize late policy status for *unsubmitted* work:
    - Due date passed  -> `missing`
    - Due date future  -> `none`

  Uses Assignment.get_submission(user_id) for each student/assignment pair.
  """
  if now is None:
    now = datetime.now(timezone.utc)
  elif now.tzinfo is None:
    now = now.replace(tzinfo=timezone.utc)
  else:
    now = now.astimezone(timezone.utc)

  stats = {
    "assignments_considered": 0,
    "students_available": 0,
    "student_limit": limit if limit is not None else 0,
    "students_considered": 0,
    "submissions_checked": 0,
    "unsubmitted_considered": 0,
    "desired_missing": 0,
    "desired_none": 0,
    "current_missing": 0,
    "current_none": 0,
    "current_other": 0,
    "missing_flag_true": 0,
    "missing_flag_false": 0,
    "submitted_with_content": 0,
    "placeholder_without_content": 0,
    "placeholder_grade_needs_clear": 0,
    "placeholder_grade_clear_attempted": 0,
    "placeholder_grade_clear_succeeded": 0,
    "placeholder_grade_clear_failed": 0,
    "updated_to_missing": 0,
    "updated_to_none": 0,
    "forced_none_due_stale_missing_flag": 0,
    "unchanged": 0,
    "unchanged_missing": 0,
    "unchanged_none": 0,
    "unchanged_other": 0,
    "skipped_excused": 0,
    "skipped_submitted": 0,
    "skipped_no_due_date": 0,
    "errors": 0,
  }

  students = list(canvas_course.get_students(include_names=False))
  stats["students_available"] = len(students)
  if limit is not None:
    students = students[:limit]
  assignments = list(canvas_course.get_assignments(include=["all_dates"], order_by="name"))

  total_assignments = len(assignments)
  for assignment_index, assignment in enumerate(assignments, start=1):
    if assignment_id is not None and assignment.id != assignment_id:
      continue
    if not include_unpublished and not getattr(assignment, "published", True):
      continue
    stats["assignments_considered"] += 1
    assignment_updates_to_missing = 0
    assignment_updates_to_none = 0
    assignment_unchanged = 0
    assignment_skipped_excused = 0
    assignment_skipped_submitted = 0
    assignment_skipped_no_due_date = 0
    assignment_errors = 0
    assignment_unsubmitted = 0

    assignment_name = getattr(assignment, "name", f"assignment_{assignment.id}")
    log.info(
      f"[{assignment_index}/{total_assignments}] Checking assignment "
      f"{assignment.id} ({assignment_name})"
    )

    for student in students:
      user_id = student.user_id
      stats["students_considered"] += 1
      try:
        submission = assignment.get_submission(user_id)
      except Exception as e:
        stats["errors"] += 1
        assignment_errors += 1
        log.warning(
          f"Skipping assignment={assignment.id} user={user_id}: could not fetch submission ({e})"
        )
        continue

      stats["submissions_checked"] += 1

      if bool(getattr(submission, "excused", False)):
        stats["skipped_excused"] += 1
        assignment_skipped_excused += 1
        continue

      content_signals = _submission_content_signals(submission)
      if content_signals:
        stats["skipped_submitted"] += 1
        stats["submitted_with_content"] += 1
        assignment_skipped_submitted += 1
        continue

      workflow_state = str(getattr(submission, "workflow_state", "") or "").strip().lower()
      submitted_at = getattr(submission, "submitted_at", None)
      if workflow_state in {"submitted", "graded", "pending_review"} or submitted_at is not None:
        stats["placeholder_without_content"] += 1
        log.debug(
          f"Treating placeholder as unsubmitted for assignment={assignment.id} user={user_id}: "
          f"workflow_state={workflow_state!r}, submitted_at={submitted_at!r}, "
          f"submission_type={getattr(submission, 'submission_type', None)!r}"
        )

      stats["unsubmitted_considered"] += 1
      assignment_unsubmitted += 1

      due_at = _resolve_submission_due_at(assignment, submission)
      if due_at is None:
        stats["skipped_no_due_date"] += 1
        assignment_skipped_no_due_date += 1
        continue

      desired_status = "missing" if due_at <= now else "none"
      if desired_status == "missing":
        stats["desired_missing"] += 1
      else:
        stats["desired_none"] += 1

      current_status = _normalize_late_policy_status(
        getattr(submission, "late_policy_status", None))
      missing_flag = bool(getattr(submission, "missing", False))
      if missing_flag:
        stats["missing_flag_true"] += 1
      else:
        stats["missing_flag_false"] += 1
      if current_status == "missing":
        stats["current_missing"] += 1
      elif current_status == "none":
        stats["current_none"] += 1
      else:
        stats["current_other"] += 1

      # Canvas can report stale `missing=True` even when late_policy_status is already
      # `none`; writing `none` again is a cheap nudge to recompute server-side state.
      if (force_clear_stale_missing and desired_status == "none"
          and current_status == "none" and missing_flag):
        log.debug(
          f"assignment={assignment.id} user={user_id} "
          f"forcing late_policy_status none (stale missing flag true)"
        )
        if not dry_run:
          submission.edit(submission={"late_policy_status": "none"})
        stats["updated_to_none"] += 1
        stats["forced_none_due_stale_missing_flag"] += 1
        assignment_updates_to_none += 1
        continue

      if (clear_placeholder_grade and desired_status == "none"
          and _placeholder_grade_needs_clear(submission)):
        stats["placeholder_grade_needs_clear"] += 1
        stats["placeholder_grade_clear_attempted"] += 1
        log.debug(
          f"assignment={assignment.id} user={user_id} "
          f"clearing placeholder grade "
          f"(grade={getattr(submission, 'grade', None)!r}, "
          f"posted_grade={getattr(submission, 'posted_grade', None)!r}, "
          f"entered_grade={getattr(submission, 'entered_grade', None)!r}, "
          f"score={getattr(submission, 'score', None)!r})"
        )
        if not dry_run:
          cleared = False
          for payload in ("", None):
            try:
              submission.edit(submission={"posted_grade": payload, "late_policy_status": "none"})
              cleared = True
              break
            except Exception as e:
              log.warning(
                f"assignment={assignment.id} user={user_id} "
                f"failed clearing placeholder grade with payload={payload!r}: {e}"
              )
          if cleared:
            stats["placeholder_grade_clear_succeeded"] += 1
          else:
            stats["placeholder_grade_clear_failed"] += 1
            stats["errors"] += 1
        else:
          stats["placeholder_grade_clear_succeeded"] += 1
        continue

      if current_status == desired_status:
        stats["unchanged"] += 1
        assignment_unchanged += 1
        if current_status == "missing":
          stats["unchanged_missing"] += 1
        elif current_status == "none":
          stats["unchanged_none"] += 1
        else:
          stats["unchanged_other"] += 1
        continue

      log.debug(
        f"assignment={assignment.id} user={user_id} "
        f"due_at={due_at.isoformat()} now={now.isoformat()} "
        f"late_policy_status: {current_status} -> {desired_status}"
      )
      if not dry_run:
        submission.edit(submission={"late_policy_status": desired_status})

      if desired_status == "missing":
        stats["updated_to_missing"] += 1
        assignment_updates_to_missing += 1
      else:
        stats["updated_to_none"] += 1
        assignment_updates_to_none += 1

    log.info(
      f"[{assignment_index}/{total_assignments}] assignment={assignment.id} summary: "
      f"unsubmitted={assignment_unsubmitted}, "
      f"updated_to_missing={assignment_updates_to_missing}, "
      f"updated_to_none={assignment_updates_to_none}, "
      f"unchanged={assignment_unchanged}, "
      f"skipped_excused={assignment_skipped_excused}, "
      f"skipped_submitted={assignment_skipped_submitted}, "
      f"skipped_no_due_date={assignment_skipped_no_due_date}, "
      f"errors={assignment_errors}"
    )

  return stats

def deprecate_assignment(canvas_course: CanvasCourse, assignment_id) -> List[canvasapi.assignment.Assignment]:
  
  log.debug(canvas_course.__dict__)
  
  # for assignment in canvas_course.get_assignments():
  #   print(assignment)
  
  canvas_assignment: CanvasAssignment = canvas_course.get_assignment(assignment_id=assignment_id)
  
  canvas_assignment.assignment.edit(
    assignment={
      "name": f"{canvas_assignment.assignment.name} (deprecated)",
      "due_at": f"{datetime.now(timezone.utc).isoformat()}",
      "lock_at": f"{datetime.now(timezone.utc).isoformat()}"
    }
  )

def mark_future_assignments_as_ungraded(canvas_course: CanvasCourse):
  
  for assignment in canvas_course.get_assignments(
      include=["all_dates"],
      order_by="name"
  ):
    if assignment.unlock_at is not None:
      if datetime.fromisoformat(assignment.unlock_at) > datetime.now(timezone.utc):
        log.debug(assignment)
        for submission in assignment.get_submissions():
          submission.mark_unread()


def main():

  # Mapping of short CLI names to helper functions
  HELPERS = {
    "clean-folders": ("delete_empty_folders", False),
    "closed": ("get_closed_assignments", False),
    "unsubmitted": ("get_unsubmitted_submissions", True),
    "missing": ("clear_out_missing", False),
    "cleanup-missing": ("cleanup_missing_by_due_date", False),
    "deprecate": ("deprecate_assignment", True),
    "ungraded": ("mark_future_assignments_as_ungraded", False),
  }

  parser = argparse.ArgumentParser(
    description="Canvas helper utilities for common course management tasks"
  )

  parser.add_argument(
    "helper",
    choices=HELPERS.keys(),
    help="Helper function to run"
  )

  parser.add_argument(
    "--course-id",
    type=int,
    required=True,
    help="Canvas course ID"
  )

  parser.add_argument(
    "--assignment-id",
    type=int,
    help="Canvas assignment ID (required for deprecate/unsubmitted, optional for cleanup-missing)"
  )
  parser.add_argument(
    "--limit",
    type=int,
    help="Limit processing to first N students (optional, cleanup-missing only)"
  )

  parser.add_argument(
    "--prod",
    action="store_true",
    help="Use production Canvas instance instead of development"
  )
  parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Report updates without writing changes to Canvas"
  )
  parser.add_argument(
    "--include-unpublished",
    action="store_true",
    help="Also process unpublished assignments"
  )
  parser.add_argument(
    "--debug",
    action="store_true",
    help="Enable verbose per-student debug logging"
  )
  parser.add_argument(
    "--no-force-clear-stale-missing",
    action="store_true",
    help="Do not force-write late_policy_status=none when Canvas reports missing=True with future due date"
  )
  parser.add_argument(
    "--clear-placeholder-grade",
    action="store_true",
    help="Attempt to clear stale placeholder grades (e.g., Incomplete/0) on future-due, no-content submissions"
  )

  args = parser.parse_args()
  if args.debug:
    log.setLevel(logging.DEBUG)

  # Get helper function name and whether it requires assignment_id
  helper_func_name, requires_assignment = HELPERS[args.helper]

  # Validate assignment_id requirement
  if requires_assignment and not args.assignment_id:
    parser.error(f"--assignment-id is required for '{args.helper}'")
  if args.limit is not None and args.limit < 1:
    parser.error("--limit must be >= 1")

  # Initialize Canvas interface and course
  canvas_interface = CanvasInterface(prod=args.prod)
  canvas_course = canvas_interface.get_course(args.course_id)

  # Run the requested helper
  if helper_func_name == "delete_empty_folders":
    delete_empty_folders(canvas_course)

  elif helper_func_name == "get_closed_assignments":
    assignments = get_closed_assignments(canvas_course)
    log.info(f"Found {len(assignments)} closed assignments:")
    for assignment in assignments:
      log.info(f"  - {assignment.name} (ID: {assignment.id})")

  elif helper_func_name == "get_unsubmitted_submissions":
    assignment = canvas_course.get_assignment(args.assignment_id)
    submissions = get_unsubmitted_submissions(canvas_course, assignment.assignment)
    log.info(f"Found {len(submissions)} unsubmitted submissions:")
    for submission in submissions:
      log.info(f"  - User {submission.user_id}")

  elif helper_func_name == "clear_out_missing":
    clear_out_missing(canvas_course)

  elif helper_func_name == "cleanup_missing_by_due_date":
    stats = cleanup_missing_by_due_date(
      canvas_course,
      dry_run=args.dry_run,
      include_unpublished=args.include_unpublished,
      assignment_id=args.assignment_id,
      limit=args.limit,
      force_clear_stale_missing=not args.no_force_clear_stale_missing,
      clear_placeholder_grade=args.clear_placeholder_grade
    )
    log.info(f"cleanup-missing summary (dry_run={args.dry_run}):")
    for key, value in stats.items():
      log.info(f"  {key}: {value}")

  elif helper_func_name == "deprecate_assignment":
    deprecate_assignment(canvas_course, args.assignment_id)
    log.info(f"Assignment {args.assignment_id} has been deprecated")

  elif helper_func_name == "mark_future_assignments_as_ungraded":
    mark_future_assignments_as_ungraded(canvas_course)


if __name__ == "__main__":
  main()
