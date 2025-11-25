"""
Repository layer for database access.

Provides clean, type-safe access to database entities using the Repository Pattern.
All repositories accept an optional connection parameter for transaction control.
"""

from .session_repository import SessionRepository

__all__ = [
  "SessionRepository",
]


# RepositoryFactory will be added in Phase 2
