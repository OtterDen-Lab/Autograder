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
    return result.returncode

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
    return 0

  print("Vendoring failed.")
  if combined:
    print(combined)
  return result.returncode


if __name__ == "__main__":
  raise SystemExit(main())
