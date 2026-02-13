#!/usr/bin/env bash
set -euo pipefail

git config core.hooksPath .githooks
echo "Installed repository hooks via core.hooksPath=.githooks"
