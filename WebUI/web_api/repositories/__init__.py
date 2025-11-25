"""
Repository layer for database access.

Provides clean, type-safe access to database entities using the Repository Pattern.
All repositories accept an optional connection parameter for transaction control.
"""
from contextlib import contextmanager

from .session_repository import SessionRepository
from .submission_repository import SubmissionRepository
from .problem_repository import ProblemRepository
from .problem_metadata_repository import ProblemMetadataRepository
from .feedback_tag_repository import FeedbackTagRepository

__all__ = [
  "SessionRepository",
  "SubmissionRepository",
  "ProblemRepository",
  "ProblemMetadataRepository",
  "FeedbackTagRepository",
  "with_transaction",
  "RepositoryFactory",
]


@contextmanager
def with_transaction():
  """
  Context manager for transaction across multiple repositories.

  Provides a RepositoryFactory with shared connection for all repositories.
  All operations commit together when exiting context.

  Usage:
    with with_transaction() as repos:
      session = repos.sessions.get_by_id(123)
      submission = repos.submissions.create(new_sub)
      # All use same connection, commit together

  Yields:
    RepositoryFactory: Factory with property access to all repositories

  Example:
    with with_transaction() as repos:
      # Create session
      session = repos.sessions.create(new_session)

      # Create submissions in bulk
      created_subs = repos.submissions.bulk_create(submissions)

      # Create problems
      repos.problems.bulk_create(problems)

      # Update metadata
      repos.metadata.upsert_max_points(session_id, 1, 10.0)

    # All committed together here
  """
  from ..database import get_db_connection

  with get_db_connection() as conn:
    factory = RepositoryFactory(conn)
    yield factory


class RepositoryFactory:
  """
  Factory for creating repositories with shared connection.

  Makes transaction pattern cleaner:
    with with_transaction() as repos:
      repos.sessions.update(session)
      repos.submissions.create(submission)

  All repositories created via this factory share the same database
  connection, so they participate in the same transaction.
  """

  def __init__(self, conn):
    """
    Initialize factory with database connection.

    Args:
      conn: Database connection to share across repositories
    """
    self._conn = conn
    self._sessions = None
    self._submissions = None
    self._problems = None
    self._metadata = None

  @property
  def sessions(self) -> SessionRepository:
    """Get or create SessionRepository with shared connection."""
    if self._sessions is None:
      self._sessions = SessionRepository(self._conn)
    return self._sessions

  @property
  def submissions(self) -> SubmissionRepository:
    """Get or create SubmissionRepository with shared connection."""
    if self._submissions is None:
      self._submissions = SubmissionRepository(self._conn)
    return self._submissions

  @property
  def problems(self) -> ProblemRepository:
    """Get or create ProblemRepository with shared connection."""
    if self._problems is None:
      self._problems = ProblemRepository(self._conn)
    return self._problems

  @property
  def metadata(self) -> ProblemMetadataRepository:
    """Get or create ProblemMetadataRepository with shared connection."""
    if self._metadata is None:
      self._metadata = ProblemMetadataRepository(self._conn)
    return self._metadata
