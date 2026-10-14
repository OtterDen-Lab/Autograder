"""Load composable YAML configuration files for the Autograder."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


class ConfigIncludeError(ValueError):
  """Raised when a YAML configuration include cannot be resolved."""


def _merge_config(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
  """Merge an included configuration with a later configuration.

  Mappings merge recursively, while scalar values and ordinary lists in the
  later file replace earlier values. ``courses`` is intentionally cumulative:
  every included course fragment contributes courses to the final run.
  """
  merged = deepcopy(base)
  for key, value in overlay.items():
    if key == "courses" and key in merged:
      if not isinstance(merged[key], list) or not isinstance(value, list):
        raise ConfigIncludeError("'courses' must be a list in every included config")
      merged[key] = merged[key] + deepcopy(value)
    elif isinstance(merged.get(key), dict) and isinstance(value, dict):
      merged[key] = _merge_config(merged[key], value)
    else:
      merged[key] = deepcopy(value)
  return merged


def _load_config(path: Path, chain: list[Path]) -> dict[str, Any]:
  resolved_path = path.expanduser().resolve()
  if resolved_path in chain:
    cycle = " -> ".join(str(item) for item in [*chain, resolved_path])
    raise ConfigIncludeError(f"cyclic config include: {cycle}")

  try:
    with resolved_path.open(encoding="utf-8") as config_file:
      raw_config = yaml.safe_load(config_file)
  except yaml.YAMLError as e:
    raise ConfigIncludeError(
      f"YAML parse error in '{resolved_path}': {e}") from e
  except OSError as e:
    raise ConfigIncludeError(
      f"unable to read included config '{resolved_path}': {e}") from e

  if not isinstance(raw_config, dict):
    raise ConfigIncludeError(
      f"config file '{resolved_path}' must contain a YAML mapping")

  raw_includes = raw_config.pop("includes", [])
  if raw_includes is None:
    raw_includes = []
  if not isinstance(raw_includes, list) or not all(
      isinstance(item, str) and item.strip() for item in raw_includes):
    raise ConfigIncludeError(
      f"'includes' in '{resolved_path}' must be a list of non-empty paths")

  merged: dict[str, Any] = {}
  for include in raw_includes:
    include_path = Path(include)
    if not include_path.is_absolute():
      include_path = resolved_path.parent / include_path
    merged = _merge_config(merged, _load_config(include_path,
                                                 [*chain, resolved_path]))
  return _merge_config(merged, raw_config)


def load_yaml_config(path: str | Path) -> dict[str, Any]:
  """Load a config and all recursively declared top-level ``includes``.

  Relative include paths resolve relative to the file declaring them. Includes
  are processed in order; mappings in later files override earlier mappings,
  then the including file overrides all of them. Course lists are concatenated.
  """
  return _load_config(Path(path), [])
