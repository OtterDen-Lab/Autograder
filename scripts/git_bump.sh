#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  git bump [patch|minor|major] [-m "commit message"] [--no-commit] [--dry-run] [--skip-tests] [--verbose]

Behavior:
  1. Bump version via `uv version --bump <kind>`
  2. Vendor LMSInterface via `python scripts/vendor_lms_interface.py`
  3. Stage `pyproject.toml`, `uv.lock`, `lms_interface/`, and managed tooling scripts
  4. Run test command (unless --skip-tests)
  5. Commit (unless --no-commit)

Notes:
  - Requires a clean index and working tree (tracked files).
  - Uses normal `git commit -m ...` (no pathspec commit).
  - Uses quiet vendoring output by default; pass --verbose for full logs.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "+ $*"
    return 0
  fi
  "$@"
}

BUMP_KIND="patch"
COMMIT_MESSAGE=""
NO_COMMIT="0"
DRY_RUN="0"
VERBOSE="0"
SKIP_TESTS="0"
TEST_COMMAND='uv run pytest -q'

while [[ $# -gt 0 ]]; do
  case "$1" in
    patch|minor|major)
      BUMP_KIND="$1"
      shift
      ;;
    -m|--message)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --message"
      COMMIT_MESSAGE="$1"
      shift
      ;;
    --no-commit)
      NO_COMMIT="1"
      shift
      ;;
    --dry-run)
      DRY_RUN="1"
      shift
      ;;
    --verbose)
      VERBOSE="1"
      shift
      ;;
    --skip-tests)
      SKIP_TESTS="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ -n "$(git diff --name-only)" ]] || [[ -n "$(git diff --cached --name-only)" ]]; then
  die "Working tree has tracked changes. Commit or stash them before running git bump."
fi

run uv version --bump "$BUMP_KIND"
if [[ "$VERBOSE" == "1" ]]; then
  run python scripts/vendor_lms_interface.py
else
  run python scripts/vendor_lms_interface.py --quiet
fi
run git add pyproject.toml uv.lock lms_interface \
  scripts/check_version_bump_vendoring.sh \
  scripts/git_bump.sh \
  scripts/install_git_hooks.sh \
  scripts/lms_vendor_tooling.toml \
  .githooks/pre-commit

if [[ "$SKIP_TESTS" != "1" ]] && [[ -n "$TEST_COMMAND" ]]; then
  echo "Running tests: $TEST_COMMAND"
  run bash -lc "$TEST_COMMAND"
fi

if [[ "$NO_COMMIT" == "1" ]]; then
  echo "Staged version bump and vendored LMSInterface updates (no commit created)."
  exit 0
fi

if [[ -z "$COMMIT_MESSAGE" ]]; then
  version="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)"
  COMMIT_MESSAGE="Bump version to ${version} and vendor LMSInterface"
fi

run env AUTOGRADER_SKIP_PRECOMMIT_VENDOR=1 git commit -m "$COMMIT_MESSAGE"
