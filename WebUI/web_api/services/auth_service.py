"""
Authentication service for user management and session handling.
"""
import bcrypt
import secrets
from datetime import datetime, timedelta
from typing import Optional
import logging

log = logging.getLogger(__name__)


class AuthService:
  """Handles authentication operations"""

  SESSION_DURATION_HOURS = 24  # Sessions expire after 24 hours of inactivity

  @staticmethod
  def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

  @staticmethod
  def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash"""
    try:
      return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception as e:
      log.error(f"Password verification error: {e}")
      return False

  @staticmethod
  def generate_session_id() -> str:
    """Generate secure session ID"""
    return secrets.token_urlsafe(32)

  def create_session(self, user_id: int, ip_address: str, user_agent: str,
                     conn) -> str:
    """Create new auth session"""
    session_id = self.generate_session_id()
    expires_at = datetime.now() + timedelta(hours=self.SESSION_DURATION_HOURS)

    cursor = conn.cursor()
    cursor.execute(
      """
            INSERT INTO auth_sessions
            (id, user_id, expires_at, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, user_id, expires_at, ip_address, user_agent))

    log.info(f"Created session {session_id[:8]}... for user {user_id}")
    return session_id

  def validate_session(self, session_id: str, conn) -> Optional[dict]:
    """Validate session and return user info if valid"""
    if not session_id:
      return None

    cursor = conn.cursor()
    cursor.execute(
      """
            SELECT u.id, u.username, u.email, u.full_name, u.role, u.is_active,
                   s.expires_at, s.last_activity
            FROM auth_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.id = ? AND u.is_active = 1
        """, (session_id, ))

    row = cursor.fetchone()
    if not row:
      return None

    # Check if session expired
    expires_at = datetime.fromisoformat(row[6])
    if datetime.now() > expires_at:
      log.info(f"Session {session_id[:8]}... expired, deleting")
      self.delete_session(session_id, conn)
      return None

    # Update last_activity and extend expiry (sliding window)
    new_expires_at = datetime.now() + timedelta(
      hours=self.SESSION_DURATION_HOURS)
    cursor.execute(
      """
            UPDATE auth_sessions
            SET last_activity = CURRENT_TIMESTAMP,
                expires_at = ?
            WHERE id = ?
        """, (new_expires_at, session_id))

    return {
      "user_id": row[0],
      "username": row[1],
      "email": row[2],
      "full_name": row[3],
      "role": row[4]
    }

  def delete_session(self, session_id: str, conn):
    """Delete session (logout)"""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auth_sessions WHERE id = ?", (session_id, ))
    log.info(f"Deleted session {session_id[:8]}...")

  def cleanup_expired_sessions(self, conn):
    """Remove expired sessions (run periodically)"""
    cursor = conn.cursor()
    try:
      cursor.execute("DELETE FROM auth_sessions WHERE expires_at < ?",
                     (datetime.now(), ))
      deleted_count = cursor.rowcount
      if deleted_count > 0:
        log.info(f"Cleaned up {deleted_count} expired sessions")
    except Exception as e:
      # Table might not exist yet if migration hasn't run
      log.debug(f"Could not cleanup sessions (table may not exist yet): {e}")
