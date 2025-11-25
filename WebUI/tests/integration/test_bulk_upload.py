"""
Integration test for bulk upload transaction.

Tests the complete flow of creating a session and bulk uploading submissions
with problems, ensuring the refactored repository pattern works correctly.
"""
import pytest
import sqlite3
import tempfile
import os

from web_api.repositories import with_transaction
from web_api.domain.session import GradingSession
from web_api.domain.submission import Submission
from web_api.domain.problem import Problem
from web_api.domain.common import SessionStatus


@pytest.fixture
def test_db():
  """Create temporary test database with schema."""
  fd, path = tempfile.mkstemp(suffix='.db')
  os.close(fd)

  # Create schema
  conn = sqlite3.connect(path)
  cursor = conn.cursor()

  # Sessions table
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

  # Submissions table
  cursor.execute("""
    CREATE TABLE submissions (
      id INTEGER PRIMARY KEY,
      session_id INTEGER NOT NULL,
      document_id INTEGER NOT NULL,
      approximate_name TEXT,
      name_image_data TEXT,
      student_name TEXT,
      display_name TEXT,
      canvas_user_id INTEGER,
      page_mappings TEXT,
      total_score REAL,
      graded_at TIMESTAMP,
      file_hash TEXT,
      original_filename TEXT,
      exam_pdf_data TEXT,
      FOREIGN KEY (session_id) REFERENCES grading_sessions(id)
    )
  """)

  # Problems table
  cursor.execute("""
    CREATE TABLE problems (
      id INTEGER PRIMARY KEY,
      session_id INTEGER NOT NULL,
      submission_id INTEGER NOT NULL,
      problem_number INTEGER NOT NULL,
      score REAL,
      feedback TEXT,
      graded INTEGER DEFAULT 0,
      graded_at TIMESTAMP,
      is_blank INTEGER DEFAULT 0,
      blank_confidence REAL DEFAULT 0.0,
      blank_method TEXT,
      blank_reasoning TEXT,
      max_points REAL,
      ai_reasoning TEXT,
      region_coords TEXT,
      qr_encrypted_data TEXT,
      transcription TEXT,
      transcription_model TEXT,
      transcription_cached_at TIMESTAMP,
      FOREIGN KEY (session_id) REFERENCES grading_sessions(id),
      FOREIGN KEY (submission_id) REFERENCES submissions(id)
    )
  """)

  # Problem metadata table
  cursor.execute("""
    CREATE TABLE problem_metadata (
      id INTEGER PRIMARY KEY,
      session_id INTEGER NOT NULL,
      problem_number INTEGER NOT NULL,
      max_points REAL,
      grading_rubric TEXT,
      default_feedback TEXT,
      default_feedback_threshold REAL,
      question_text TEXT,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(session_id, problem_number),
      FOREIGN KEY (session_id) REFERENCES grading_sessions(id)
    )
  """)

  conn.commit()
  conn.close()

  # Provide path to database module
  import web_api.database as db
  original_get_db_path = db.get_db_path
  db.get_db_path = lambda: path

  yield path

  # Cleanup
  db.get_db_path = original_get_db_path
  os.unlink(path)


def test_bulk_upload_transaction(test_db):
  """Test bulk upload transaction with submissions and problems."""

  # Step 1: Create a session
  with with_transaction() as repos:
    session = GradingSession(
      id=0,
      assignment_id=12345,
      assignment_name="Midterm Exam",
      course_id=100,
      course_name="CST334",
      status=SessionStatus.PREPROCESSING,
      created_at=None,  # Will be set by database
      updated_at=None,  # Will be set by database
      total_exams=0,
      processed_exams=0,
      matched_exams=0,
      use_prod_canvas=False
    )
    created_session = repos.sessions.create(session)
    session_id = created_session.id

  assert session_id > 0, "Session should have been created with ID"

  # Step 2: Simulate bulk upload (like uploads.py does)
  # Create 3 submissions with 2 problems each
  with with_transaction() as repos:
    # Step 2a: Prepare submission data
    submissions_to_create = []
    for i in range(3):
      submission = Submission(
        id=0,
        session_id=session_id,
        document_id=i,
        approximate_name=f"Student {i}",
        name_image_data=None,
        student_name=f"Test Student {i}",
        display_name=None,
        canvas_user_id=1000 + i,
        page_mappings={"1": [0, 1], "2": [2, 3]},
        file_hash=f"hash_{i}",
        original_filename=f"exam_{i}.pdf",
        exam_pdf_data=f"fake_pdf_data_{i}"
      )
      # Store problems temporarily (like uploads.py does)
      submission.problems = [
        {
          "problem_number": 1,
          "max_points": 10.0,
          "is_blank": False,
          "blank_confidence": 0.0,
          "page_number": 1,
          "region_y_start": 100,
          "region_y_end": 300,
          "region_height": 200
        },
        {
          "problem_number": 2,
          "max_points": 15.0,
          "is_blank": True,
          "blank_confidence": 0.95,
          "blank_method": "heuristic",
          "blank_reasoning": "No writing detected",
          "page_number": 2,
          "region_y_start": 100,
          "region_y_end": 400,
          "region_height": 300
        }
      ]
      submissions_to_create.append(submission)

    # Step 2b: Bulk create submissions
    created_submissions = repos.submissions.bulk_create(submissions_to_create)

    assert len(created_submissions) == 3, "Should create 3 submissions"
    assert all(s.id > 0 for s in created_submissions), "All submissions should have IDs"

    # Step 2c: Build problems with correct submission_ids
    all_problems = []
    max_points_to_upsert = {}

    for i, created_sub in enumerate(created_submissions):
      for prob_data in submissions_to_create[i].problems:
        problem_number = prob_data["problem_number"]

        # Check metadata (first submission will set it)
        existing_max = repos.metadata.get_max_points(session_id, problem_number)
        if existing_max is not None:
          max_points = existing_max
        else:
          max_points = prob_data["max_points"]
          max_points_to_upsert[problem_number] = max_points

        # Prepare region_coords
        region_coords = {
          "page_number": prob_data["page_number"],
          "region_y_start": prob_data["region_y_start"],
          "region_y_end": prob_data["region_y_end"],
          "region_height": prob_data["region_height"]
        }

        problem = Problem(
          id=0,
          session_id=session_id,
          submission_id=created_sub.id,
          problem_number=problem_number,
          graded=False,
          is_blank=prob_data["is_blank"],
          blank_confidence=prob_data["blank_confidence"],
          blank_method=prob_data.get("blank_method"),
          blank_reasoning=prob_data.get("blank_reasoning"),
          max_points=max_points,
          region_coords=region_coords
        )
        all_problems.append(problem)

    # Step 2d: Bulk create problems
    created_problems = repos.problems.bulk_create(all_problems)

    assert len(created_problems) == 6, "Should create 6 problems (3 submissions × 2 problems)"
    assert all(p.id > 0 for p in created_problems), "All problems should have IDs"

    # Step 2e: Upsert metadata
    for problem_num, max_pts in max_points_to_upsert.items():
      repos.metadata.upsert_max_points(session_id, problem_num, max_pts)

    # Step 2f: Update session status
    repos.sessions.update_status(session_id, SessionStatus.NAME_MATCHING_NEEDED)

  # Step 3: Verify everything was created correctly
  with with_transaction() as repos:
    # Check session
    session = repos.sessions.get_by_id(session_id)
    assert session is not None
    assert session.status == SessionStatus.NAME_MATCHING_NEEDED

    # Check submissions
    submissions = repos.submissions.get_by_session(session_id)
    assert len(submissions) == 3
    assert all(s.session_id == session_id for s in submissions)
    assert [s.document_id for s in submissions] == [0, 1, 2]

    # Check problems
    problems = repos.problems.get_by_session_batch(session_id)
    assert len(problems) == 6
    assert all(p.session_id == session_id for p in problems)

    # Check that problems are linked to correct submissions
    for submission in submissions:
      submission_problems = [p for p in problems if p.submission_id == submission.id]
      assert len(submission_problems) == 2
      assert sorted([p.problem_number for p in submission_problems]) == [1, 2]

    # Check metadata
    max_points_1 = repos.metadata.get_max_points(session_id, 1)
    max_points_2 = repos.metadata.get_max_points(session_id, 2)
    assert max_points_1 == 10.0
    assert max_points_2 == 15.0

    # Check blank detection was preserved
    blank_problems = [p for p in problems if p.is_blank]
    non_blank_problems = [p for p in problems if not p.is_blank]
    assert len(blank_problems) == 3  # One per submission for problem 2
    assert len(non_blank_problems) == 3  # One per submission for problem 1
    assert all(p.blank_confidence == 0.95 for p in blank_problems)


def test_bulk_upload_duplicate_detection(test_db):
  """Test that duplicate detection works with repositories."""

  # Create session
  with with_transaction() as repos:
    session = GradingSession(
      id=0,
      assignment_id=12345,
      assignment_name="Test",
      course_id=100,
      course_name="CST334",
      status=SessionStatus.PREPROCESSING,
      created_at=None,
      updated_at=None
    )
    created_session = repos.sessions.create(session)
    session_id = created_session.id

  # Upload first submission
  with with_transaction() as repos:
    submission = Submission(
      id=0,
      session_id=session_id,
      document_id=0,
      approximate_name=None,
      name_image_data=None,
      student_name="Test Student",
      display_name=None,
      canvas_user_id=None,
      page_mappings={},
      file_hash="abc123",
      original_filename="exam.pdf"
    )
    repos.submissions.bulk_create([submission])

  # Check for duplicates (like uploads.py does)
  from web_api.repositories import SubmissionRepository
  submission_repo = SubmissionRepository()
  existing_hashes = submission_repo.get_existing_hashes(session_id)

  assert "abc123" in existing_hashes
  assert existing_hashes["abc123"] == "exam.pdf"


if __name__ == "__main__":
  pytest.main([__file__, "-v"])
