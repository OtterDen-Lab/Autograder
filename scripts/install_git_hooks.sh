#!/usr/bin/env bash
set -euo pipefail

git config core.hooksPath .githooks
git config alias.bump '!f(){ repo_root="$(git rev-parse --show-toplevel)"; bash "$repo_root/scripts/git_bump.sh" "$@"; }; f'

echo "Installed repository hooks via core.hooksPath=.githooks"
echo "Installed repository alias: git bump <patch|minor|major>"
