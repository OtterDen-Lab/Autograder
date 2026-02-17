"""
LMS integration for Otter-Autograder

Vendored from LMSInterface v0.4.4 (2026-02-17)
"""

__version__ = "0.4.4"
__vendored_from__ = "LMSInterface"
__vendored_date__ = "2026-02-17"

try:
  from importlib.metadata import PackageNotFoundError, version
except Exception:  # pragma: no cover - fallback for older Python
  PackageNotFoundError = Exception  # type: ignore
  version = None  # type: ignore

if "__version__" not in globals():
  try:
    if version is None:
      raise PackageNotFoundError
    __version__ = version("lms-interface")
  except PackageNotFoundError:
    __version__ = "vendored"
