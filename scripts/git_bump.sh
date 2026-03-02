#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  git bump [patch|minor|major] [-m "commit message"] [--no-commit] [--dry-run] [--skip-tests] [--verbose] [--tag|--no-tag] [--push|--no-push] [--remote <name>]

Behavior:
  1. Vendor LMSInterface via `python scripts/vendor_lms_interface.py`
  2. Run test command (unless --skip-tests)
  3. Bump version via `uv version --bump <kind>`
  4. Stage `pyproject.toml`, `uv.lock`, `lms_interface/`, and managed tooling scripts
  5. Commit (unless --no-commit)
  6. Create tag `v<version>` by default (disable with --no-tag)
  7. Push branch and tag by default (disable with --no-push)

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
CREATE_TAG="1"
PUSH_CHANGES="1"
TAG_EXPLICIT="0"
PUSH_EXPLICIT="0"
REMOTE_NAME="origin"
TEST_COMMAND='uv run python -m pytest -q'

# Run from a temporary copy so vendoring can safely rewrite scripts/git_bump.sh.
if [[ "${LMS_GIT_BUMP_STAGE2:-0}" != "1" ]]; then
  script_tmp="$(mktemp "${TMPDIR:-/tmp}/lms_git_bump.XXXXXX.sh")"
  cp "$0" "$script_tmp"
  chmod +x "$script_tmp"
  exec env LMS_GIT_BUMP_STAGE2=1 LMS_GIT_BUMP_TMP="$script_tmp" bash "$script_tmp" "$@"
fi

if [[ -n "${LMS_GIT_BUMP_TMP:-}" ]]; then
  trap 'rm -f "$LMS_GIT_BUMP_TMP"' EXIT
fi

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
    --tag)
      CREATE_TAG="1"
      TAG_EXPLICIT="1"
      shift
      ;;
    --no-tag)
      CREATE_TAG="0"
      TAG_EXPLICIT="1"
      shift
      ;;
    --push)
      PUSH_CHANGES="1"
      PUSH_EXPLICIT="1"
      shift
      ;;
    --no-push)
      PUSH_CHANGES="0"
      PUSH_EXPLICIT="1"
      shift
      ;;
    --remote)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --remote"
      REMOTE_NAME="$1"
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

if [[ "$VERBOSE" == "1" ]]; then
  run python scripts/vendor_lms_interface.py
else
  run python scripts/vendor_lms_interface.py --quiet
fi

if [[ "$SKIP_TESTS" != "1" ]] && [[ -n "$TEST_COMMAND" ]]; then
  echo "Running tests: $TEST_COMMAND"
  run bash -lc "$TEST_COMMAND"
fi

run uv version --bump "$BUMP_KIND"
version="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)"
run git add pyproject.toml uv.lock lms_interface \
  scripts/check_version_bump_vendoring.sh \
  scripts/git_bump.sh \
  scripts/install_git_hooks.sh \
  scripts/lms_vendor_tooling.toml \
  .githooks/pre-commit

if [[ "$NO_COMMIT" == "1" ]]; then
  if [[ ("$TAG_EXPLICIT" == "1" && "$CREATE_TAG" == "1") || ("$PUSH_EXPLICIT" == "1" && "$PUSH_CHANGES" == "1") ]]; then
    die "--tag and --push require a commit. Remove --no-commit."
  fi
  echo "Staged version bump and vendored LMSInterface updates (no commit created)."
  exit 0
fi

if [[ -z "$COMMIT_MESSAGE" ]]; then
  COMMIT_MESSAGE="Bump to version ${version}"
  run env AUTOGRADER_SKIP_PRECOMMIT_VENDOR=1 git commit -e -m "$COMMIT_MESSAGE"
else
  run env AUTOGRADER_SKIP_PRECOMMIT_VENDOR=1 git commit -m "$COMMIT_MESSAGE"
fi

if [[ "$CREATE_TAG" == "1" ]]; then
  tag_name="v${version}"
  if git rev-parse -q --verify "refs/tags/${tag_name}" >/dev/null; then
    die "Tag ${tag_name} already exists."
  fi
  run git tag "${tag_name}"
fi

if [[ "$PUSH_CHANGES" == "1" ]]; then
  branch_name="$(git rev-parse --abbrev-ref HEAD)"
  run git push "$REMOTE_NAME" "$branch_name"
  if [[ "$CREATE_TAG" == "1" ]]; then
    run git push "$REMOTE_NAME" "$tag_name"
  fi
fi
