#!/usr/bin/env python3
"""Vendor LMSInterface into the release workspace and patch packaging metadata."""

import argparse
import os
import re
import shutil
import tomllib
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

DEFAULT_CONFIG = Path("scripts/lms_release_source.toml")
DEFAULT_PYPROJECT = Path("pyproject.toml")
USER_AGENT = "autograder-release-vendor"


def _load_config(path: Path) -> dict[str, str]:
  data = tomllib.loads(path.read_text())
  section = data.get("lms_interface", {})

  repository = str(section.get("repository", "")).strip()
  ref = str(section.get("ref", "")).strip()
  expected_version = str(section.get("expected_version", "")).strip()

  if not repository:
    raise ValueError(f"Missing lms_interface.repository in {path}")
  if not ref:
    raise ValueError(f"Missing lms_interface.ref in {path}")
  if not expected_version:
    expected_version = ref.removeprefix("v")

  return {
    "repository": repository,
    "ref": ref,
    "expected_version": expected_version,
  }


def _download_wheel(url: str, output: Path) -> None:
  request = urllib.request.Request(
    url,
    headers={"User-Agent": USER_AGENT},
  )

  github_token = os.getenv("GITHUB_TOKEN", "").strip()
  if github_token:
    request.add_header("Authorization", f"Bearer {github_token}")

  with urllib.request.urlopen(request, timeout=30) as response:
    output.write_bytes(response.read())


def _extract_lms_package(wheel_path: Path, destination: Path) -> None:
  if destination.exists():
    shutil.rmtree(destination)
  destination.mkdir(parents=True, exist_ok=True)

  extracted = 0
  with zipfile.ZipFile(wheel_path) as zf:
    for member in zf.namelist():
      if not member.startswith("lms_interface/") or member.endswith("/"):
        continue

      relative = Path(member).relative_to("lms_interface")
      if ".." in relative.parts:
        raise ValueError(f"Invalid path in wheel member: {member}")
      target = destination / relative
      target.parent.mkdir(parents=True, exist_ok=True)

      with zf.open(member) as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)
      extracted += 1

  if extracted == 0:
    raise ValueError(f"No lms_interface package files found in {wheel_path}")


def _update_inline_array_in_section(
  text: str,
  *,
  section: str,
  key: str,
  required_value: str,
) -> str:
  lines = text.splitlines(keepends=True)

  section_start = None
  for idx, line in enumerate(lines):
    if line.strip() == f"[{section}]":
      section_start = idx
      break
  if section_start is None:
    raise ValueError(f"Section [{section}] not found in pyproject.toml")

  section_end = len(lines)
  for idx in range(section_start + 1, len(lines)):
    stripped = lines[idx].strip()
    if stripped.startswith("[") and stripped.endswith("]"):
      section_end = idx
      break

  target_idx = None
  for idx in range(section_start + 1, section_end):
    if lines[idx].lstrip().startswith(f"{key} ="):
      target_idx = idx
      break
  if target_idx is None:
    raise ValueError(f"Key '{key}' not found in section [{section}]")

  prefix, raw_value = lines[target_idx].split("=", 1)
  raw_value = raw_value.strip()
  if not (raw_value.startswith("[") and raw_value.endswith("]")):
    raise ValueError(
      f"Expected inline list for {key} in section [{section}], got: {raw_value}"
    )

  items = re.findall(r'"([^"]+)"', raw_value)
  if required_value not in items:
    items.append(required_value)

  leading = prefix.split(key)[0]
  list_text = ", ".join(f'"{item}"' for item in items)
  lines[target_idx] = f"{leading}{key} = [{list_text}]\n"

  return "".join(lines)


def _patch_pyproject(pyproject_path: Path) -> None:
  original = pyproject_path.read_text()
  lines = original.splitlines(keepends=True)

  filtered_lines = [line for line in lines if '"lms-interface @ ' not in line]
  if len(filtered_lines) == len(lines):
    raise ValueError("Did not find direct lms-interface dependency in pyproject.toml")

  patched = "".join(filtered_lines)
  patched = _update_inline_array_in_section(
    patched,
    section="tool.hatch.build.targets.wheel",
    key="packages",
    required_value="lms_interface",
  )
  patched = _update_inline_array_in_section(
    patched,
    section="tool.hatch.build.targets.sdist",
    key="include",
    required_value="lms_interface/**",
  )

  pyproject_path.write_text(patched)


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Vendor lms_interface from pinned source and patch pyproject for release"
  )
  parser.add_argument(
    "--config",
    type=Path,
    default=DEFAULT_CONFIG,
    help=f"Path to vendoring source config (default: {DEFAULT_CONFIG})",
  )
  parser.add_argument(
    "--pyproject",
    type=Path,
    default=DEFAULT_PYPROJECT,
    help=f"Path to pyproject.toml (default: {DEFAULT_PYPROJECT})",
  )
  args = parser.parse_args()

  config = _load_config(args.config)
  repository = config["repository"]
  ref = config["ref"]
  version = config["expected_version"]

  wheel_name = f"lms_interface-{version}-py3-none-any.whl"
  wheel_url = f"https://github.com/{repository}/releases/download/{ref}/{wheel_name}"

  print(f"Vendoring LMSInterface from {repository}@{ref} ({version})")
  print(f"Downloading wheel: {wheel_url}")

  with TemporaryDirectory() as tmp:
    wheel_path = Path(tmp) / wheel_name
    _download_wheel(wheel_url, wheel_path)
    _extract_lms_package(wheel_path, Path("lms_interface"))

  print("Patching pyproject.toml for release packaging")
  _patch_pyproject(args.pyproject)

  print("Vendoring and pyproject patch complete")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
