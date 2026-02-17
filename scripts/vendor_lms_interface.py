#!/usr/bin/env python3
"""
Wrapper to vendor LMSInterface into Autograder using the shared script.

Usage:
    python scripts/vendor_lms_interface.py [--dry-run] [--quiet] [--lms-path PATH]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _resolve_vendor_script(candidate_repo: Path) -> Path:
  return candidate_repo / "scripts" / "vendor_into_project.py"


def _prompt_for_lms_repo(default_repo: Path) -> Path | None:
  print(f"LMSInterface not found at default path: {default_repo}")
  entered = input(
    "Enter path to LMSInterface repo (or press Enter to cancel): "
  ).strip()
  if not entered:
    return None
  return Path(entered).expanduser().resolve()


def _extract_version(log_text: str) -> str | None:
  match = re.search(r"Vendoring lms_interface v([^\n\r ]+)", log_text)
  if match:
    return match.group(1)
  return None


def _apply_autograder_overrides(*, repo_root: Path, dry_run: bool, quiet: bool) -> bool:
  """
  Re-apply Autograder-specific behavior on top of shared vendored LMSInterface.

  These overrides are intentionally local because they encode Autograder policy:
  - Prefer attachment submissions for ProgrammingAssignment when mixed text/files exist.
  - Use Autograder-specific temporary-file prefix for feedback uploads.
  """
  canvas_file = repo_root / "lms_interface" / "canvas_interface.py"
  if not canvas_file.exists():
    print(f"Error: expected vendored file missing: {canvas_file}")
    return False

  content = canvas_file.read_text()
  updated = content

  # Override feedback temp prefix for Autograder observability/tests.
  updated = updated.replace(
    'prefix="lms_interface_feedback_upload_"',
    'prefix="autograder_feedback_upload_"',
  )

  # Inject assignment_kind preference flag once.
  marker = '    test_only = kwargs.get("test", False)\n'
  injection = (
    '    test_only = kwargs.get("test", False)\n'
    '    assignment_kind = kwargs.get("assignment_kind")\n'
    '    prefer_file_submissions = assignment_kind == "ProgrammingAssignment"\n'
  )
  if 'assignment_kind = kwargs.get("assignment_kind")' not in updated:
    if marker not in updated:
      print("Error: could not locate submission preamble marker in canvas_interface.py")
      return False
    updated = updated.replace(marker, injection, 1)

  # Replace submission-type branch to prefer files for ProgrammingAssignment.
  start = "        # Determine submission type based on content\n"
  end = "        # Check if we should only include the most recent\n"
  if start not in updated or end not in updated:
    print("Error: could not locate submission-type block in canvas_interface.py")
    return False
  pre, rest = updated.split(start, 1)
  _, post = rest.split(end, 1)
  replacement_block = (
    "        # Determine submission type based on content\n"
    "        has_attachments = student_submission.get(\"attachments\") is not None and len(student_submission.get(\"attachments\", [])) > 0\n"
    "        has_text_body = student_submission.get(\"body\") is not None and student_submission.get(\"body\").strip() != \"\"\n"
    "\n"
    "        if has_attachments and (prefer_file_submissions or not has_text_body):\n"
    "          if has_text_body and prefer_file_submissions:\n"
    "            log.debug(\n"
    "              f\"Detected mixed content for {student.name}; prioritizing attachments for ProgrammingAssignment\"\n"
    "            )\n"
    "          # File submission\n"
    "          log.debug(f\"Detected file submission for {student.name}\")\n"
    "          submissions.append(\n"
    "            FileSubmission__Canvas(\n"
    "              student=student,\n"
    "              status=Submission.Status.from_string(student_submission[\"workflow_state\"], student_submission['score']),\n"
    "              attachments=student_submission[\"attachments\"],\n"
    "              submission_index=student_submission_index\n"
    "            )\n"
    "          )\n"
    "        elif has_text_body:\n"
    "          # Text submission - create object-like structure from dict\n"
    "          log.debug(f\"Detected text submission for {student.name}\")\n"
    "          class SubmissionObject:\n"
    "            def __init__(self, data):\n"
    "              for key, value in data.items():\n"
    "                setattr(self, key, value)\n"
    "\n"
    "          submissions.append(\n"
    "            TextSubmission__Canvas(\n"
    "              student=student,\n"
    "              status=Submission.Status.from_string(student_submission[\"workflow_state\"], student_submission['score']),\n"
    "              canvas_submission_data=SubmissionObject(student_submission),\n"
    "              submission_index=student_submission_index\n"
    "            )\n"
    "          )\n"
    "        else:\n"
    "          # No submission content found\n"
    "          log.debug(f\"No submission content found for {student.name}\")\n"
    "          continue\n"
    "\n"
  )
  updated = pre + replacement_block + end + post

  if dry_run:
    if updated != content and not quiet:
      print("  [DRY RUN] Would apply Autograder LMSInterface overrides")
    return True

  if updated != content:
    canvas_file.write_text(updated)
    if not quiet:
      print("Applied Autograder LMSInterface overrides")
  elif not quiet:
    print("Autograder LMSInterface overrides already applied")

  return True


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Vendor LMSInterface into Autograder (top-level package)"
  )
  parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Show what would be done without making changes",
  )
  parser.add_argument(
    "--lms-path",
    type=Path,
    help="Path to LMSInterface repository (default: ../LMSInterface)",
  )
  parser.add_argument(
    "--quiet",
    action="store_true",
    help="Suppress detailed output and print a short summary",
  )

  args = parser.parse_args()

  script_dir = Path(__file__).parent
  repo_root = script_dir.parent
  default_repo = (repo_root.parent / "LMSInterface").resolve()
  lms_repo = (args.lms_path.resolve()
              if args.lms_path else default_repo)
  vendor_script = _resolve_vendor_script(lms_repo)

  if not vendor_script.exists():
    if args.lms_path:
      print(f"Error: vendor script not found at {vendor_script}")
      return 1
    if not sys.stdin.isatty():
      print(
        "Error: LMSInterface vendor script not found at default path "
        f"{vendor_script}. Provide --lms-path."
      )
      return 1
    prompted_repo = _prompt_for_lms_repo(default_repo)
    if prompted_repo is None:
      print("Canceled vendoring.")
      return 1
    lms_repo = prompted_repo
    vendor_script = _resolve_vendor_script(lms_repo)
    if not vendor_script.exists():
      print(f"Error: vendor script not found at {vendor_script}")
      return 1

  cmd = [
    sys.executable,
    str(vendor_script),
    str(repo_root),
    "--top-level",
  ]
  if args.dry_run:
    cmd.append("--dry-run")

  if not args.quiet:
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
      return result.returncode
    if not _apply_autograder_overrides(
        repo_root=repo_root,
        dry_run=args.dry_run,
        quiet=args.quiet,
    ):
      return 1
    return 0

  result = subprocess.run(cmd, check=False, capture_output=True, text=True)
  combined = f"{result.stdout}\n{result.stderr}".strip()
  version = _extract_version(combined)
  dry_run_note = " [dry-run]" if args.dry_run else ""
  if result.returncode == 0:
    if version:
      print(
        f"Vendored LMSInterface v{version} from {lms_repo}{dry_run_note}."
      )
    else:
      print(f"Vendored LMSInterface from {lms_repo}{dry_run_note}.")
    if not _apply_autograder_overrides(
        repo_root=repo_root,
        dry_run=args.dry_run,
        quiet=args.quiet,
    ):
      return 1
    return 0

  print("Vendoring failed.")
  if combined:
    print(combined)
  return result.returncode


if __name__ == "__main__":
  raise SystemExit(main())
