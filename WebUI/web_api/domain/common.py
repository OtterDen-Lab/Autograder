"""
Common types and enums shared across domain models.
"""
from enum import Enum


class SessionStatus(str, Enum):
  """
  Status of a grading session.

  Matches the existing enum in models.py for API compatibility.
  """
  PREPROCESSING = "preprocessing"
  AWAITING_ALIGNMENT = "awaiting_alignment"
  NAME_MATCHING_NEEDED = "name_matching_needed"
  READY = "ready"
  GRADING = "grading"
  FINALIZING = "finalizing"
  FINALIZED = "finalized"
  COMPLETE = "complete"
  ERROR = "error"

  def is_ready_for_grading(self) -> bool:
    """Check if session can be graded."""
    return self == SessionStatus.READY

  def is_complete(self) -> bool:
    """Check if session is in a complete state."""
    return self in (SessionStatus.FINALIZED, SessionStatus.COMPLETE)

  def is_processing(self) -> bool:
    """Check if session is actively processing."""
    return self in (SessionStatus.PREPROCESSING, SessionStatus.GRADING, SessionStatus.FINALIZING)
