#!/usr/bin/env bash
set -euo pipefail

if [[ "${AUTOGRADER_SKIP_PRECOMMIT_VENDOR:-0}" == "1" ]]; then
  echo "Skipping vendoring check (already handled by git bump)."
  exit 0
fi

# When pyproject version is bumped, refresh vendored LMSInterface automatically.
if ! git diff --cached -- pyproject.toml | grep -Eq '^[+-][[:space:]]*version[[:space:]]*='; then
  exit 0
fi

echo "Version bump detected in pyproject.toml; syncing vendored LMSInterface..."

before_snapshot="$(mktemp)"
after_snapshot="$(mktemp)"
trap 'rm -f "$before_snapshot" "$after_snapshot"' EXIT

git diff --cached -- lms_interface pyproject.toml >"$before_snapshot" || true
python scripts/vendor_lms_interface.py --quiet
git add lms_interface pyproject.toml
git diff --cached -- lms_interface pyproject.toml >"$after_snapshot" || true

if cmp -s "$before_snapshot" "$after_snapshot"; then
  echo "Vendored LMSInterface already up to date."
  exit 0
fi

echo "Updated and staged vendored LMSInterface changes."
echo "Review staged diff, then run commit again."
exit 1
