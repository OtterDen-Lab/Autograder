"""Installed distribution version helpers."""

from importlib.metadata import PackageNotFoundError, version


DISTRIBUTION_NAME = "Otter-Autograder"


def get_package_version() -> str:
  """Return the version of the installed Autograder distribution.

  The release process already updates ``project.version`` in ``pyproject.toml``.
  Hatch embeds that value in package metadata, making this automatically track
  the version that was actually installed on the grading host.
  """
  try:
    return version(DISTRIBUTION_NAME)
  except PackageNotFoundError:
    return "unknown"
