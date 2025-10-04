"""
Database connection and schema management.
"""
import sqlite3
from pathlib import Path
from typing import Optional
from contextlib import contextmanager
import logging

log = logging.getLogger(__name__)

# Default database path (can be overridden via environment variable)
DEFAULT_DB_PATH = Path.home() / ".autograder" / "grading.db"
CURRENT_SCHEMA_VERSION = 10


def get_db_path() -> Path:
    """Get database path from environment or use default"""
    import os
    db_path = os.getenv("GRADING_DB_PATH", str(DEFAULT_DB_PATH))
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def get_db_connection():
    """Context manager for database connections"""
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row  # Enable column access by name
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """Initialize database with schema"""
    log.info(f"Initializing database at {get_db_path()}")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Check current schema version
        current_version = get_schema_version(cursor)

        if current_version == 0:
            # Create new database
            create_schema(cursor)
        elif current_version < CURRENT_SCHEMA_VERSION:
            # Run migrations
            run_migrations(cursor, current_version)

        log.info(f"Database ready (schema version {CURRENT_SCHEMA_VERSION})")


def get_schema_version(cursor) -> int:
    """Get current schema version"""
    try:
        cursor.execute("SELECT version FROM _schema_version ORDER BY version DESC LIMIT 1")
        result = cursor.fetchone()
        return result[0] if result else 0
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return 0


def create_schema(cursor):
    """Create initial database schema"""

    # Schema version tracking
    cursor.execute("""
        CREATE TABLE _schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Grading sessions
    cursor.execute("""
        CREATE TABLE grading_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER NOT NULL,
            assignment_name TEXT NOT NULL,
            course_id INTEGER NOT NULL,
            course_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL,
            canvas_points REAL,
            metadata TEXT,
            total_exams INTEGER DEFAULT 0,
            processed_exams INTEGER DEFAULT 0,
            matched_exams INTEGER DEFAULT 0,
            processing_message TEXT,
            use_prod_canvas INTEGER DEFAULT 0
        )
    """)

    # Student submissions
    cursor.execute("""
        CREATE TABLE submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            approximate_name TEXT,
            name_image_data TEXT,
            student_name TEXT,
            display_name TEXT,
            canvas_user_id INTEGER,
            page_mappings TEXT NOT NULL,
            total_score REAL,
            graded_at TIMESTAMP,
            file_hash TEXT,
            original_filename TEXT,
            FOREIGN KEY (session_id) REFERENCES grading_sessions(id)
        )
    """)

    # Individual problems
    cursor.execute("""
        CREATE TABLE problems (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            submission_id INTEGER NOT NULL,
            problem_number INTEGER NOT NULL,
            image_data TEXT NOT NULL,
            score REAL,
            feedback TEXT,
            graded INTEGER DEFAULT 0,
            graded_at TIMESTAMP,
            is_blank INTEGER DEFAULT 0,
            blank_confidence REAL DEFAULT 0.0,
            blank_method TEXT,
            blank_reasoning TEXT,
            FOREIGN KEY (session_id) REFERENCES grading_sessions(id),
            FOREIGN KEY (submission_id) REFERENCES submissions(id)
        )
    """)

    # Create indexes for performance
    cursor.execute("""
        CREATE INDEX idx_problems_session_problem
        ON problems(session_id, problem_number)
    """)

    cursor.execute("""
        CREATE INDEX idx_problems_graded
        ON problems(session_id, graded)
    """)

    cursor.execute("""
        CREATE INDEX idx_submissions_session
        ON submissions(session_id)
    """)

    cursor.execute("""
        CREATE INDEX idx_submissions_file_hash
        ON submissions(session_id, file_hash)
    """)

    # Problem statistics (computed view)
    cursor.execute("""
        CREATE TABLE problem_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            problem_number INTEGER NOT NULL,
            avg_score REAL,
            num_graded INTEGER,
            num_total INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES grading_sessions(id),
            UNIQUE(session_id, problem_number)
        )
    """)

    # Record schema version
    cursor.execute("INSERT INTO _schema_version (version) VALUES (?)", (CURRENT_SCHEMA_VERSION,))

    log.info(f"Created database schema version {CURRENT_SCHEMA_VERSION}")


def run_migrations(cursor, from_version: int):
    """Run database migrations from current version to latest"""
    log.info(f"Running migrations from version {from_version} to {CURRENT_SCHEMA_VERSION}")

    if from_version < 2:
        migrate_to_v2(cursor)
        cursor.execute("INSERT INTO _schema_version (version) VALUES (2)")

    if from_version < 3:
        migrate_to_v3(cursor)
        cursor.execute("INSERT INTO _schema_version (version) VALUES (3)")

    if from_version < 4:
        migrate_to_v4(cursor)
        cursor.execute("INSERT INTO _schema_version (version) VALUES (4)")

    if from_version < 5:
        migrate_to_v5(cursor)
        cursor.execute("INSERT INTO _schema_version (version) VALUES (5)")

    if from_version < 6:
        migrate_to_v6(cursor)
        cursor.execute("INSERT INTO _schema_version (version) VALUES (6)")

    if from_version < 7:
        migrate_to_v7(cursor)
        cursor.execute("INSERT INTO _schema_version (version) VALUES (7)")

    if from_version < 8:
        migrate_to_v8(cursor)
        cursor.execute("INSERT INTO _schema_version (version) VALUES (8)")

    if from_version < 9:
        migrate_to_v9(cursor)
        cursor.execute("INSERT INTO _schema_version (version) VALUES (9)")

    if from_version < 10:
        migrate_to_v10(cursor)
        cursor.execute("INSERT INTO _schema_version (version) VALUES (10)")


def migrate_to_v2(cursor):
    """Add progress tracking columns to grading_sessions"""
    log.info("Migrating to schema version 2: adding progress tracking")

    cursor.execute("ALTER TABLE grading_sessions ADD COLUMN total_exams INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE grading_sessions ADD COLUMN processed_exams INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE grading_sessions ADD COLUMN matched_exams INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE grading_sessions ADD COLUMN processing_message TEXT")


def migrate_to_v3(cursor):
    """Add approximate_name column to submissions"""
    log.info("Migrating to schema version 3: adding approximate_name to submissions")

    cursor.execute("ALTER TABLE submissions ADD COLUMN approximate_name TEXT")


def migrate_to_v4(cursor):
    """Add name_image_data column to submissions"""
    log.info("Migrating to schema version 4: adding name_image_data to submissions")

    cursor.execute("ALTER TABLE submissions ADD COLUMN name_image_data TEXT")


def migrate_to_v5(cursor):
    """Add blank detection columns to problems"""
    log.info("Migrating to schema version 5: adding blank detection columns to problems")

    cursor.execute("ALTER TABLE problems ADD COLUMN is_blank INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE problems ADD COLUMN blank_confidence REAL DEFAULT 0.0")
    cursor.execute("ALTER TABLE problems ADD COLUMN blank_method TEXT")
    cursor.execute("ALTER TABLE problems ADD COLUMN blank_reasoning TEXT")


def migrate_to_v6(cursor):
    """Add file hash tracking to submissions"""
    log.info("Migrating to schema version 6: adding file_hash and original_filename to submissions")

    cursor.execute("ALTER TABLE submissions ADD COLUMN file_hash TEXT")
    cursor.execute("ALTER TABLE submissions ADD COLUMN original_filename TEXT")

    # Create index for fast duplicate detection
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_submissions_file_hash ON submissions(session_id, file_hash)")


def migrate_to_v7(cursor):
    """Add Canvas environment setting to sessions"""
    log.info("Migrating to schema version 7: adding use_prod_canvas to grading_sessions")

    cursor.execute("ALTER TABLE grading_sessions ADD COLUMN use_prod_canvas INTEGER DEFAULT 0")


def migrate_to_v8(cursor):
    """Add min/max score tracking to problem_stats"""
    log.info("Migrating to schema version 8: adding min_score and max_score to problem_stats")

    cursor.execute("ALTER TABLE problem_stats ADD COLUMN min_score REAL")
    cursor.execute("ALTER TABLE problem_stats ADD COLUMN max_score REAL")


def migrate_to_v9(cursor):
    """Add max_points column to problems"""
    log.info("Migrating to schema version 9: adding max_points to problems")

    cursor.execute("ALTER TABLE problems ADD COLUMN max_points REAL")


def migrate_to_v10(cursor):
    """Create problem_metadata table for storing max_points per problem number"""
    log.info("Migrating to schema version 10: creating problem_metadata table")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS problem_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            problem_number INTEGER NOT NULL,
            max_points REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES grading_sessions(id),
            UNIQUE(session_id, problem_number)
        )
    """)


def update_problem_stats(session_id: int):
    """Update computed statistics for a session"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get all problem numbers for this session
        cursor.execute("""
            SELECT DISTINCT problem_number
            FROM problems
            WHERE session_id = ?
        """, (session_id,))

        problem_numbers = [row[0] for row in cursor.fetchall()]

        for problem_num in problem_numbers:
            # Calculate statistics
            cursor.execute("""
                SELECT
                    AVG(score) as avg_score,
                    MIN(score) as min_score,
                    MAX(score) as max_score,
                    SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as num_graded,
                    COUNT(*) as num_total
                FROM problems
                WHERE session_id = ? AND problem_number = ? AND graded = 1
            """, (session_id, problem_num))

            row = cursor.fetchone()
            avg_score, min_score, max_score, num_graded, num_total_graded = row[0], row[1], row[2], row[3], row[4]

            # Get total count (including ungraded)
            cursor.execute("""
                SELECT COUNT(*) FROM problems
                WHERE session_id = ? AND problem_number = ?
            """, (session_id, problem_num))
            num_total = cursor.fetchone()[0]

            # Upsert statistics
            cursor.execute("""
                INSERT INTO problem_stats (session_id, problem_number, avg_score, min_score, max_score, num_graded, num_total)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, problem_number)
                DO UPDATE SET
                    avg_score = excluded.avg_score,
                    min_score = excluded.min_score,
                    max_score = excluded.max_score,
                    num_graded = excluded.num_graded,
                    num_total = excluded.num_total,
                    updated_at = CURRENT_TIMESTAMP
            """, (session_id, problem_num, avg_score, min_score, max_score, num_graded, num_total))
