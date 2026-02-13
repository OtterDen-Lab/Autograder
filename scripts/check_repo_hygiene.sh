#!/usr/bin/env bash
set -euo pipefail

echo "Running repository hygiene checks..."

forbidden_tracked_regex='^(records/|\.autograder/|.*\.log$)'
forbidden_tracked="$(git ls-files | rg -N "${forbidden_tracked_regex}" || true)"
if [[ -n "${forbidden_tracked}" ]]; then
  echo "ERROR: Forbidden tracked artifacts detected:"
  echo "${forbidden_tracked}"
  echo "Do not commit runtime artifacts (records/, .autograder/, *.log)."
  exit 1
fi

# Keep docs/examples from nudging users toward in-repo records paths.
if rg -n 'records_dir:\s*"\./|records_dir:\s*\./' README.md example_files >/dev/null; then
  echo "ERROR: Found relative records_dir paths in docs/examples."
  echo "Use absolute paths or ~/... for records_dir."
  exit 1
fi

# Ensure gitignore contains protections.
if ! rg -N '^records/$' .gitignore >/dev/null; then
  echo "ERROR: .gitignore must include records/"
  exit 1
fi
if ! rg -N '^\.autograder/$' .gitignore >/dev/null; then
  echo "ERROR: .gitignore must include .autograder/"
  exit 1
fi

echo "Repository hygiene checks passed."
