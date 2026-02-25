#!/usr/bin/env python3
"""
Check whether the pinned LMSInterface source is behind upstream.

This is an advisory check by default. It emits GitHub Actions warnings when stale.
"""

import argparse
import json
import os
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = Path("scripts/lms_release_source.toml")
GITHUB_API_ROOT = "https://api.github.com"
RAW_GITHUB_ROOT = "https://raw.githubusercontent.com"
USER_AGENT = "autograder-lms-freshness-check"


def _warn(message: str) -> None:
  print(f"::warning::{message}")


def _http_get_text(url: str) -> str:
  request = urllib.request.Request(
    url,
    headers={
      "Accept": "application/vnd.github+json",
      "User-Agent": USER_AGENT,
    },
  )

  github_token = os.getenv("GITHUB_TOKEN", "").strip()
  if github_token:
    request.add_header("Authorization", f"Bearer {github_token}")

  with urllib.request.urlopen(request, timeout=20) as response:
    return response.read().decode("utf-8")


def _load_source_config(path: Path) -> dict[str, str]:
  data = tomllib.loads(path.read_text())
  section = data.get("lms_interface", {})
  repository = str(section.get("repository", "")).strip()
  ref = str(section.get("ref", "")).strip()
  expected_version = str(section.get("expected_version", "")).strip()

  if not repository:
    raise ValueError(f"Missing lms_interface.repository in {path}")
  if not ref:
    raise ValueError(f"Missing lms_interface.ref in {path}")

  return {
    "repository": repository,
    "ref": ref,
    "expected_version": expected_version,
  }


def _latest_release_tag(repository: str) -> str:
  payload = _http_get_text(f"{GITHUB_API_ROOT}/repos/{repository}/releases/latest")
  parsed = json.loads(payload)
  tag = str(parsed.get("tag_name", "")).strip()
  if not tag:
    raise ValueError(f"Latest release for {repository} has no tag_name")
  return tag


def _pyproject_version(repository: str, ref: str) -> str:
  raw_pyproject = _http_get_text(
    f"{RAW_GITHUB_ROOT}/{repository}/{ref}/pyproject.toml"
  )
  data = tomllib.loads(raw_pyproject)
  version = str(data.get("project", {}).get("version", "")).strip()
  if not version:
    raise ValueError(f"project.version missing in {repository}@{ref} pyproject.toml")
  return version


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Check pinned LMSInterface vendoring source against upstream latest release"
  )
  parser.add_argument(
    "--config",
    type=Path,
    default=DEFAULT_CONFIG,
    help=f"Path to vendoring source config (default: {DEFAULT_CONFIG})",
  )
  parser.add_argument(
    "--fail-on-stale",
    action="store_true",
    help="Exit non-zero when the pinned source is behind upstream latest release",
  )
  parser.add_argument(
    "--fail-on-error",
    action="store_true",
    help="Exit non-zero when upstream checks cannot be performed",
  )
  args = parser.parse_args()

  try:
    config = _load_source_config(args.config)
  except Exception as exc:
    print(f"Error loading config: {exc}")
    return 1

  repository = config["repository"]
  pinned_ref = config["ref"]
  expected_version = config["expected_version"]

  print(f"Pinned LMS repository: {repository}")
  print(f"Pinned LMS ref:        {pinned_ref}")

  try:
    pinned_version = _pyproject_version(repository, pinned_ref)
  except Exception as exc:
    message = (
      f"Unable to read pinned LMS pyproject for {repository}@{pinned_ref}: {exc}"
    )
    _warn(message)
    return 1 if args.fail_on_error else 0

  print(f"Pinned LMS version:    {pinned_version}")

  if expected_version and expected_version != pinned_version:
    print(
      "Error: scripts/lms_release_source.toml expected_version does not match "
      f"{repository}@{pinned_ref} pyproject version ({expected_version} != {pinned_version})"
    )
    return 1

  try:
    latest_tag = _latest_release_tag(repository)
    latest_version = _pyproject_version(repository, latest_tag)
  except Exception as exc:
    message = f"Unable to check latest LMS release for {repository}: {exc}"
    _warn(message)
    return 1 if args.fail_on_error else 0

  print(f"Latest LMS tag:        {latest_tag}")
  print(f"Latest LMS version:    {latest_version}")

  is_stale = (pinned_ref != latest_tag) or (pinned_version != latest_version)
  if not is_stale:
    print("Pinned LMS source is up to date with upstream latest release.")
    return 0

  message = (
    f"Pinned LMS source {repository}@{pinned_ref} (version {pinned_version}) "
    f"is behind latest {latest_tag} (version {latest_version}). "
    "Update scripts/lms_release_source.toml when appropriate."
  )
  _warn(message)
  if args.fail_on_stale:
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
