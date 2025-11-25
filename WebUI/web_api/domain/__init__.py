"""
Domain models for the web grading system.

These are business entities that represent database rows as Python objects.
They are plain dataclasses with no automatic persistence - changes must be
explicitly saved via repository methods.
"""

from .common import SessionStatus
from .session import GradingSession
from .submission import Submission
from .problem import Problem

__all__ = [
  "SessionStatus",
  "GradingSession",
  "Submission",
  "Problem",
]
