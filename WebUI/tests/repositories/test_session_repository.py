"""
Unit tests for SessionRepository.
"""
import pytest
import sqlite3
from datetime import datetime

from web_api.repositories import SessionRepository
from web_api.domain import GradingSession, SessionStatus
from web_api.database import create_schema


@pytest.fixture
def test_db():
  """Create in-memory test database with schema."""
  conn = sqlite3.connect(":memory:")
  conn.row_factory = sqlite3.Row
  cursor = conn.cursor()
  create_schema(cursor)
  conn.commit()
  yield conn
  conn.close()


@pytest.fixture
def sample_session():
  """Create a sample GradingSession for testing."""
  return GradingSession(
    id=0,  # Will be populated on create
    assignment_id=12345,
    assignment_name="Midterm Exam",
    course_id=67890,
    course_name="CST334 - Operating Systems",
    status=SessionStatus.PREPROCESSING,
    created_at=datetime.now(),
    updated_at=datetime.now(),
    canvas_points=100.0,
    metadata={"test_key": "test_value"},
    total_exams=0,
    processed_exams=0,
    matched_exams=0,
    processing_message="Initializing",
    use_prod_canvas=False
  )


class TestSessionRepositoryCreate:
  """Tests for creating sessions."""

  def test_create_session(self, test_db, sample_session):
    """Test creating a new session."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)

    assert created.id > 0, "Session should have auto-generated ID"
    assert created.assignment_id == sample_session.assignment_id
    assert created.assignment_name == sample_session.assignment_name
    assert created.course_id == sample_session.course_id
    assert created.status == SessionStatus.PREPROCESSING
    assert created.canvas_points == 100.0
    assert created.metadata == {"test_key": "test_value"}

  def test_create_session_with_null_metadata(self, test_db):
    """Test creating session without metadata."""
    repo = SessionRepository(test_db)

    session = GradingSession(
      id=0,
      assignment_id=123,
      assignment_name="Test",
      course_id=456,
      course_name=None,
      status=SessionStatus.READY,
      created_at=datetime.now(),
      updated_at=datetime.now(),
      metadata=None  # No metadata
    )

    created = repo.create(session)

    assert created.id > 0
    assert created.metadata is None

  def test_create_session_use_prod_canvas(self, test_db, sample_session):
    """Test creating session with prod Canvas flag."""
    repo = SessionRepository(test_db)

    sample_session.use_prod_canvas = True
    created = repo.create(sample_session)

    assert created.use_prod_canvas is True


class TestSessionRepositoryRead:
  """Tests for reading sessions."""

  def test_get_by_id_existing(self, test_db, sample_session):
    """Test retrieving existing session by ID."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)
    retrieved = repo.get_by_id(created.id)

    assert retrieved is not None
    assert retrieved.id == created.id
    assert retrieved.assignment_id == created.assignment_id
    assert retrieved.status == created.status

  def test_get_by_id_nonexistent(self, test_db):
    """Test retrieving non-existent session returns None."""
    repo = SessionRepository(test_db)

    retrieved = repo.get_by_id(99999)

    assert retrieved is None

  def test_exists_true(self, test_db, sample_session):
    """Test exists() returns True for existing session."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)
    assert repo.exists(created.id) is True

  def test_exists_false(self, test_db):
    """Test exists() returns False for non-existent session."""
    repo = SessionRepository(test_db)

    assert repo.exists(99999) is False

  def test_list_all_empty(self, test_db):
    """Test list_all() with no sessions."""
    repo = SessionRepository(test_db)

    sessions = repo.list_all()

    assert sessions == []

  def test_list_all_multiple(self, test_db):
    """Test list_all() with multiple sessions."""
    repo = SessionRepository(test_db)

    # Create 3 sessions
    for i in range(3):
      session = GradingSession(
        id=0,
        assignment_id=100 + i,
        assignment_name=f"Assignment {i}",
        course_id=200,
        course_name="Test Course",
        status=SessionStatus.READY,
        created_at=datetime.now(),
        updated_at=datetime.now()
      )
      repo.create(session)

    sessions = repo.list_all()

    assert len(sessions) == 3
    # Note: SQLite timestamps may be identical for quick inserts,
    # so order may not be guaranteed. Just verify all are present.
    assignment_ids = {s.assignment_id for s in sessions}
    assert assignment_ids == {100, 101, 102}


class TestSessionRepositoryUpdate:
  """Tests for updating sessions."""

  def test_update_session(self, test_db, sample_session):
    """Test updating all fields of a session."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)

    # Modify fields
    created.assignment_name = "Updated Name"
    created.status = SessionStatus.READY
    created.canvas_points = 150.0
    created.total_exams = 10
    created.metadata = {"new_key": "new_value"}

    repo.update(created)

    # Retrieve and verify
    retrieved = repo.get_by_id(created.id)
    assert retrieved.assignment_name == "Updated Name"
    assert retrieved.status == SessionStatus.READY
    assert retrieved.canvas_points == 150.0
    assert retrieved.total_exams == 10
    assert retrieved.metadata == {"new_key": "new_value"}

  def test_update_status(self, test_db, sample_session):
    """Test updating just status field."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)

    repo.update_status(created.id, SessionStatus.READY, "Ready for grading")

    retrieved = repo.get_by_id(created.id)
    assert retrieved.status == SessionStatus.READY
    assert retrieved.processing_message == "Ready for grading"

  def test_update_status_without_message(self, test_db, sample_session):
    """Test updating status without changing message."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)
    original_message = created.processing_message

    repo.update_status(created.id, SessionStatus.GRADING)

    retrieved = repo.get_by_id(created.id)
    assert retrieved.status == SessionStatus.GRADING
    assert retrieved.processing_message == original_message

  def test_update_progress_all_fields(self, test_db, sample_session):
    """Test updating all progress counters."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)

    repo.update_progress(created.id, total=100, processed=50, matched=40)

    retrieved = repo.get_by_id(created.id)
    assert retrieved.total_exams == 100
    assert retrieved.processed_exams == 50
    assert retrieved.matched_exams == 40

  def test_update_progress_partial(self, test_db, sample_session):
    """Test updating only some progress fields."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)
    created.total_exams = 100
    created.processed_exams = 50
    created.matched_exams = 30
    repo.update(created)

    # Update only processed
    repo.update_progress(created.id, processed=75)

    retrieved = repo.get_by_id(created.id)
    assert retrieved.total_exams == 100  # Unchanged
    assert retrieved.processed_exams == 75  # Updated
    assert retrieved.matched_exams == 30  # Unchanged

  def test_update_metadata(self, test_db, sample_session):
    """Test updating just metadata field."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)

    new_metadata = {
      "file_paths": ["/path/1", "/path/2"],
      "split_points": {"1": [0.5, 0.75]}  # JSON converts int keys to strings
    }
    repo.update_metadata(created.id, new_metadata)

    retrieved = repo.get_by_id(created.id)
    assert retrieved.metadata == new_metadata

  def test_get_metadata(self, test_db, sample_session):
    """Test retrieving just metadata."""
    repo = SessionRepository(test_db)

    sample_session.metadata = {"key": "value", "nested": {"a": 1}}
    created = repo.create(sample_session)

    metadata = repo.get_metadata(created.id)

    assert metadata == {"key": "value", "nested": {"a": 1}}


class TestSessionRepositoryDelete:
  """Tests for deleting sessions."""

  def test_delete_existing(self, test_db, sample_session):
    """Test deleting an existing session."""
    repo = SessionRepository(test_db)

    created = repo.create(sample_session)
    deleted = repo.delete(created.id)

    assert deleted is True
    assert repo.get_by_id(created.id) is None

  def test_delete_nonexistent(self, test_db):
    """Test deleting non-existent session returns False."""
    repo = SessionRepository(test_db)

    deleted = repo.delete(99999)

    assert deleted is False


class TestSessionRepositoryTransactions:
  """Tests for transaction control."""

  def test_standalone_operations(self, test_db, sample_session):
    """Test repository without external connection (creates own)."""
    repo = SessionRepository()  # No connection passed

    # This should work even though test_db is separate
    # Repository creates its own connection
    created = repo.create(sample_session)

    assert created.id > 0

  def test_shared_connection(self, test_db, sample_session):
    """Test multiple operations in single transaction."""
    # Create two repos sharing connection
    repo1 = SessionRepository(test_db)
    repo2 = SessionRepository(test_db)

    # Both should see each other's changes within transaction
    session1 = sample_session
    session1.assignment_name = "Session 1"
    created1 = repo1.create(session1)

    session2 = GradingSession(
      id=0,
      assignment_id=99999,
      assignment_name="Session 2",
      course_id=67890,
      course_name="Test",
      status=SessionStatus.READY,
      created_at=datetime.now(),
      updated_at=datetime.now()
    )
    created2 = repo2.create(session2)

    # Both should be visible
    assert repo1.get_by_id(created1.id) is not None
    assert repo2.get_by_id(created2.id) is not None

    test_db.commit()  # Manually commit


class TestGradingSessionBusinessLogic:
  """Tests for business logic methods on GradingSession."""

  def test_is_ready_for_grading(self):
    """Test is_ready_for_grading() method."""
    session = GradingSession(
      id=1,
      assignment_id=123,
      assignment_name="Test",
      course_id=456,
      course_name="Test",
      status=SessionStatus.READY,
      created_at=datetime.now(),
      updated_at=datetime.now()
    )

    assert session.is_ready_for_grading() is True

    session.status = SessionStatus.PREPROCESSING
    assert session.is_ready_for_grading() is False

  def test_mark_processing(self):
    """Test mark_processing() business logic method."""
    session = GradingSession(
      id=1,
      assignment_id=123,
      assignment_name="Test",
      course_id=456,
      course_name="Test",
      status=SessionStatus.READY,
      created_at=datetime.now(),
      updated_at=datetime.now()
    )

    session.mark_processing("Uploading files")

    assert session.status == SessionStatus.PREPROCESSING
    assert session.processing_message == "Uploading files"

  def test_get_progress_percentage(self):
    """Test progress percentage calculation."""
    session = GradingSession(
      id=1,
      assignment_id=123,
      assignment_name="Test",
      course_id=456,
      course_name="Test",
      status=SessionStatus.PREPROCESSING,
      created_at=datetime.now(),
      updated_at=datetime.now(),
      total_exams=100,
      processed_exams=50
    )

    assert session.get_progress_percentage() == 50.0

  def test_get_match_percentage(self):
    """Test match percentage calculation."""
    session = GradingSession(
      id=1,
      assignment_id=123,
      assignment_name="Test",
      course_id=456,
      course_name="Test",
      status=SessionStatus.NAME_MATCHING_NEEDED,
      created_at=datetime.now(),
      updated_at=datetime.now(),
      processed_exams=100,
      matched_exams=80
    )

    assert session.get_match_percentage() == 80.0
