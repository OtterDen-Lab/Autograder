"""
Domain model for grading sessions.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .common import SessionStatus


@dataclass
class GradingSession:
  """
  Domain model for grading_sessions table.

  This is a plain data class - changes are not automatically persisted.
  To save changes, call repository.update(session).

  Attributes:
    id: Primary key
    assignment_id: Canvas assignment ID
    assignment_name: Display name for assignment
    course_id: Canvas course ID
    course_name: Display name for course
    status: Current status of the grading session
    created_at: When session was created
    updated_at: Last update timestamp
    canvas_points: Max points for assignment in Canvas
    metadata: JSON metadata (file_paths, split_points, etc.)
    total_exams: Total number of exams uploaded
    processed_exams: Number of exams processed
    matched_exams: Number of exams matched to students
    processing_message: Status message for UI
    use_prod_canvas: Whether to use production Canvas environment
  """
  id: int
  assignment_id: int
  assignment_name: str
  course_id: int
  course_name: Optional[str]
  status: SessionStatus
  created_at: datetime
  updated_at: datetime
  canvas_points: Optional[float] = None
  metadata: Optional[dict] = None
  total_exams: int = 0
  processed_exams: int = 0
  matched_exams: int = 0
  processing_message: Optional[str] = None
  use_prod_canvas: bool = False

  # Business logic methods (operate in-memory only)

  def is_ready_for_grading(self) -> bool:
    """Check if session can be graded."""
    return self.status.is_ready_for_grading()

  def is_complete(self) -> bool:
    """Check if session is in a complete state."""
    return self.status.is_complete()

  def is_processing(self) -> bool:
    """Check if session is actively processing."""
    return self.status.is_processing()

  def mark_processing(self, message: str) -> None:
    """
    Mark session as processing (in-memory).
    Caller must persist via repository.update().
    """
    self.status = SessionStatus.PREPROCESSING
    self.processing_message = message

  def mark_ready(self) -> None:
    """
    Mark session as ready for grading (in-memory).
    Caller must persist via repository.update().
    """
    self.status = SessionStatus.READY
    self.processing_message = "Ready for grading"

  def mark_error(self, message: str) -> None:
    """
    Mark session as error (in-memory).
    Caller must persist via repository.update().
    """
    self.status = SessionStatus.ERROR
    self.processing_message = message

  def get_progress_percentage(self) -> float:
    """Calculate processing progress as percentage."""
    if self.total_exams == 0:
      return 0.0
    return (self.processed_exams / self.total_exams) * 100.0

  def get_match_percentage(self) -> float:
    """Calculate matching progress as percentage."""
    if self.processed_exams == 0:
      return 0.0
    return (self.matched_exams / self.processed_exams) * 100.0
