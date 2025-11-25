"""
Base repository with connection management and common patterns.
"""
from typing import TypeVar, Generic, Optional, List
from abc import ABC, abstractmethod
import sqlite3
from contextlib import contextmanager

T = TypeVar('T')  # Domain model type


class BaseRepository(ABC, Generic[T]):
  """
  Abstract base repository providing transaction control and common utilities.

  Design principles:
  1. Repositories accept optional connection parameter
  2. If no connection provided, create one internally
  3. Return clean domain objects (not DB-backed)
  4. Explicit save methods (no magic tracking)

  Usage:
    # Standalone (creates own connection)
    repo = SessionRepository()
    session = repo.get_by_id(123)

    # Transactional (shared connection)
    with get_db_connection() as conn:
      repo1 = SessionRepository(conn)
      repo2 = SubmissionRepository(conn)
      # Both use same transaction
  """

  def __init__(self, conn: Optional[sqlite3.Connection] = None):
    """
    Initialize repository with optional connection.

    Args:
      conn: Optional database connection for transaction control.
            If None, each operation creates its own connection.
    """
    self._external_conn = conn

  @contextmanager
  def _get_connection(self):
    """
    Get connection for operation - either external or create new.

    Only commits/rolls back if we created the connection.
    External connections are managed by the caller.

    Yields:
      sqlite3.Connection: Database connection
    """
    if self._external_conn:
      # Use external connection, don't commit/rollback
      # Caller is responsible for transaction management
      yield self._external_conn
    else:
      # Create our own connection, manage transaction
      from ..database import get_db_connection
      with get_db_connection() as conn:
        yield conn

  def with_connection(self, conn: sqlite3.Connection) -> 'BaseRepository[T]':
    """
    Return new repository instance using provided connection.

    Used for transaction control across multiple repositories.

    Args:
      conn: Database connection to use

    Returns:
      New repository instance with shared connection

    Example:
      with get_db_connection() as conn:
        session_repo = SessionRepository().with_connection(conn)
        submission_repo = SubmissionRepository().with_connection(conn)
        # Both use same connection/transaction
    """
    return self.__class__(conn)

  @abstractmethod
  def _row_to_domain(self, row: sqlite3.Row) -> T:
    """
    Convert database row to domain object.

    Subclasses must implement this to transform sqlite3.Row
    into their specific domain model.

    Args:
      row: Database row with column access by name

    Returns:
      Domain object (e.g., GradingSession, Submission, Problem)
    """
    pass

  def _rows_to_domains(self, rows: List[sqlite3.Row]) -> List[T]:
    """
    Convert multiple rows to domain objects.

    Args:
      rows: List of database rows

    Returns:
      List of domain objects
    """
    return [self._row_to_domain(row) for row in rows]

  def _execute_and_fetch_one(self, conn: sqlite3.Connection, sql: str,
                            params: tuple = ()) -> Optional[T]:
    """
    Execute query and return single domain object.

    Helper method to reduce boilerplate in subclasses.

    Args:
      conn: Database connection
      sql: SQL query string
      params: Query parameters

    Returns:
      Domain object or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return self._row_to_domain(row) if row else None

  def _execute_and_fetch_all(self, conn: sqlite3.Connection, sql: str,
                             params: tuple = ()) -> List[T]:
    """
    Execute query and return all domain objects.

    Helper method to reduce boilerplate in subclasses.

    Args:
      conn: Database connection
      sql: SQL query string
      params: Query parameters

    Returns:
      List of domain objects (empty list if none found)
    """
    cursor = conn.cursor()
    cursor.execute(sql, params)
    return self._rows_to_domains(cursor.fetchall())
