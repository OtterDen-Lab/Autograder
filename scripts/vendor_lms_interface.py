#!/usr/bin/env python3
"""
Wrapper to vendor LMSInterface into Autograder using the shared script.

Usage:
    python scripts/vendor_lms_interface.py [--dry-run] [--lms-path PATH]
"""

import argparse
import subprocess
import sys
from pathlib import Path


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

  args = parser.parse_args()

  script_dir = Path(__file__).parent
  repo_root = script_dir.parent
  lms_repo = args.lms_path or (repo_root.parent / "LMSInterface")
  vendor_script = lms_repo / "scripts" / "vendor_into_project.py"

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

  print("Running:", " ".join(cmd))
  result = subprocess.run(cmd, check=False)
  return result.returncode


if __name__ == "__main__":
  raise SystemExit(main())
