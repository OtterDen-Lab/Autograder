"""PostgreSQL assignment leases and per-student grading cooldowns.

Apply the schema explicitly with: python -m Autograder.coordination migrate
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import threading
import uuid
from importlib.resources import files
from datetime import timedelta


log = logging.getLogger(__name__)


class CoordinationError(RuntimeError):
  pass


def cooldown_seconds(value):
  """Cooldowns are expressed in seconds; omission disables success tracking."""
  if value is None:
    return None
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ValueError("grading_cooldown must be a nonnegative number of seconds")
  if not math.isfinite(value) or value < 0:
    raise ValueError("grading_cooldown must be a nonnegative number of seconds")
  return float(value)


def _student_key(value):
  # SQL_ASCII connections return text as bytes. Existing schemas may use
  # numeric IDs; normalize both sides before testing membership.
  return value.decode("ascii") if isinstance(value, bytes) else str(value)


class PostgresCoordinator:
  def __init__(self, dsn, lease_seconds=300):
    self.dsn = dsn
    self.lease_seconds = lease_seconds

  def _query(self, sql, params=()):
    # Keep transactions short, and use independent connections for the heartbeat.
    # Never include connection errors in logs: they can contain credentials.
    try:
      import psycopg
      with psycopg.connect(self.dsn, connect_timeout=5,
                          options='-c statement_timeout=10000 -c lock_timeout=5000') as conn:
        with conn.cursor() as cursor:
          cursor.execute(sql, params)
          return cursor.fetchall() if cursor.description else []
    except Exception as error:
      # SQLSTATE identifies the failure without revealing server messages, SQL
      # parameter values, or credentials from connection exceptions.
      state = getattr(error, "sqlstate", None)
      hints = {
        "42P01": "a coordination table is missing; rerun the coordination schema migration",
        "42703": "a coordination column is missing; rerun the coordination schema migration",
        "42501": "permission denied; check the runtime role's schema and table privileges",
      }
      hint = hints.get(state, "check connectivity, credentials, and schema migration")
      code = f" (SQLSTATE {state})" if isinstance(state, str) and len(state) == 5 and state.isalnum() else ""
      raise CoordinationError(f"PostgreSQL coordination failed{code}; {hint}") from None

  def migrate(self):
    self._query(files('Autograder').joinpath('migrations/001_coordination.sql').read_text())

  def panopto_scan_window(self, course_id, assignment_id):
    """Return (DB scan-start, safe cutoff), or no cutoff on first/stale scan."""
    rows = self._query("""
      SELECT clock_timestamp(),
        (SELECT max(last_succeeded_at) FROM autograder_student_successes WHERE course_id = %s),
        (SELECT completed_through FROM autograder_panopto_checkpoints
         WHERE course_id = %s AND assignment_id = %s),
        (SELECT last_full_scan_at FROM autograder_panopto_checkpoints
         WHERE course_id = %s AND assignment_id = %s)
    """, (str(course_id), str(course_id), str(assignment_id),
          str(course_id), str(assignment_id)))
    now, last_push, checkpoint, last_full = rows[0]
    if (last_push is None or checkpoint is None or last_full is None or last_push > now
        or checkpoint > now or last_full > now or now - last_full >= timedelta(days=7)):
      return now, None
    window = min(2 * (now - last_push), timedelta(days=7))
    # Never advance past the last completed assignment scan; retain overlap
    # for delayed Panopto updates and equal timestamp boundaries.
    return now, min(now - window, checkpoint - timedelta(minutes=15))

  def complete_panopto_scan(self, course_id, assignment_id, started_at, full_scan):
    self._query("""
      INSERT INTO autograder_panopto_checkpoints (course_id, assignment_id, completed_through, last_full_scan_at)
      VALUES (%s, %s, %s, %s)
      ON CONFLICT (course_id, assignment_id) DO UPDATE
      SET completed_through = greatest(autograder_panopto_checkpoints.completed_through,
                                       excluded.completed_through),
          last_full_scan_at = CASE WHEN excluded.last_full_scan_at IS NOT NULL
            THEN greatest(autograder_panopto_checkpoints.last_full_scan_at, excluded.last_full_scan_at)
            ELSE autograder_panopto_checkpoints.last_full_scan_at END
    """, (str(course_id), str(assignment_id), started_at,
          started_at if full_scan else None))

  def claim(self, course_id, assignment_id):
    token = str(uuid.uuid4())
    rows = self._query("""
      INSERT INTO autograder_assignment_leases
        (course_id, assignment_id, token, expires_at)
      VALUES (%s, %s, %s, clock_timestamp() + %s * interval '1 second')
      ON CONFLICT (course_id, assignment_id) DO UPDATE
      SET token = EXCLUDED.token, expires_at = EXCLUDED.expires_at
      WHERE autograder_assignment_leases.expires_at <= clock_timestamp()
      RETURNING token
    """, (str(course_id), str(assignment_id), token, self.lease_seconds))
    if not rows:
      return None
    return AssignmentLease(self, str(course_id), str(assignment_id), token)


class AssignmentLease:
  def __init__(self, store, course_id, assignment_id, token):
    self.store = store
    self.key = (course_id, assignment_id, token)
    self._stop = threading.Event()
    self._lost = threading.Event()
    self._thread = None

  def start(self):
    self._thread = threading.Thread(target=self._heartbeat, daemon=True)
    self._thread.start()

  def _heartbeat(self):
    while not self._stop.wait(self.store.lease_seconds / 3):
      try:
        self.check()
      except CoordinationError:
        self._lost.set()
        return

  def check(self):
    if self._lost.is_set():
      raise CoordinationError("Assignment lease was lost; refusing to publish")
    try:
      rows = self.store._query("""
        UPDATE autograder_assignment_leases
        SET expires_at = clock_timestamp() + %s * interval '1 second'
        WHERE course_id = %s AND assignment_id = %s AND token = %s
          AND expires_at > clock_timestamp()
        RETURNING token
      """, (self.store.lease_seconds, *self.key))
      if not rows:
        raise CoordinationError("Assignment lease expired or changed ownership")
    except CoordinationError:
      self._lost.set()
      raise

  def filter_submissions(self, assignment, cooldown):
    self.check()
    assignment.cooldown_deferred_count = 0
    if cooldown is None:
      return
    rows = self.store._query("""
      SELECT student_id FROM autograder_student_successes
      WHERE course_id = %s AND assignment_id = %s
        AND last_succeeded_at > clock_timestamp() - %s * interval '1 second'
    """, (*self.key[:2], cooldown))
    cooling = {_student_key(row[0]) for row in rows}
    before = len(assignment.submissions)
    assignment.submissions = [s for s in assignment.submissions
                              if _student_key(s.student.user_id) not in cooling]
    assignment.cooldown_deferred_count = before - len(assignment.submissions)
    log.info("Cooldown filtering (course %s, assignment %s, %s seconds): "
             "%d recent success records; kept %d submissions; skipped %d students",
             *self.key[:2], cooldown, len(rows), len(assignment.submissions),
             before - len(assignment.submissions))
    # Text graders consume this parallel batch representation.
    if hasattr(assignment, 'submission_data'):
      eligible = {id(s) for s in assignment.submissions}
      assignment.submission_data = [row for row in assignment.submission_data
                                   if id(row['submission_obj']) in eligible]

  def succeeded(self, student_id):
    self.check()
    rows = self.store._query("""
      INSERT INTO autograder_student_successes
        (course_id, assignment_id, student_id, last_succeeded_at)
      SELECT course_id, assignment_id, %s, clock_timestamp()
      FROM autograder_assignment_leases
      WHERE course_id = %s AND assignment_id = %s AND token = %s
        AND expires_at > clock_timestamp()
      ON CONFLICT (course_id, assignment_id, student_id) DO UPDATE
      SET last_succeeded_at = EXCLUDED.last_succeeded_at
      RETURNING student_id
    """, (str(student_id), *self.key))
    if not rows:
      self._lost.set()
      raise CoordinationError("Lease lost while recording successful Canvas update")
    log.debug("Recorded successful Canvas update for cooldown tracking "
              "(course %s, assignment %s)", *self.key[:2])

  def close(self):
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=16)
    self.store._query("""
      DELETE FROM autograder_assignment_leases
      WHERE course_id = %s AND assignment_id = %s AND token = %s
    """, self.key)


def coordinator_from_environment():
  dsn = os.getenv('AUTOGRADER_DATABASE_URL')
  return PostgresCoordinator(dsn) if dsn else None


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('command', choices=['migrate'])
  parser.parse_args()
  coordinator = coordinator_from_environment()
  if coordinator is None:
    parser.error('AUTOGRADER_DATABASE_URL is required')
  coordinator.migrate()


if __name__ == '__main__':
  main()
