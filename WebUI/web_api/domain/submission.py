"""
Domain model for student submissions.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
  from .problem import Problem


@dataclass
class Submission:
  """
  Domain model for submissions table.

  Represents a student's exam submission with associated problems.

  Attributes:
    id: Primary key
    session_id: Foreign key to grading_sessions
    document_id: Document number within session
    approximate_name: AI-extracted name from exam
    name_image_data: Base64-encoded image of name region
    student_name: Confirmed student name (after matching)
    display_name: Display name for UI
    canvas_user_id: Canvas user ID (null if unmatched)
    page_mappings: JSON mapping of pages
    total_score: Sum of all problem scores
    graded_at: When grading completed
    file_hash: SHA256 hash for duplicate detection
    original_filename: Original uploaded filename
    exam_pdf_data: Base64-encoded PDF data (can be large)
    problems: Lazy-loaded list of problems (not from DB)
  """
  id: int
  session_id: int
  document_id: int
  approximate_name: Optional[str]
  name_image_data: Optional[str]
  student_name: Optional[str]
  display_name: Optional[str]
  canvas_user_id: Optional[int]
  page_mappings: dict
  total_score: Optional[float] = None
  graded_at: Optional[datetime] = None
  file_hash: Optional[str] = None
  original_filename: Optional[str] = None
  exam_pdf_data: Optional[str] = None

  # Lazy-loaded relationships (not stored in DB)
  problems: List['Problem'] = field(default_factory=list, repr=False)

  # Business logic methods

  def is_matched(self) -> bool:
    """Check if submission is matched to a Canvas student."""
    return self.canvas_user_id is not None

  def is_graded(self) -> bool:
    """Check if all problems are graded."""
    if not self.problems:
      return False
    return all(p.graded for p in self.problems)

  def get_ungraded_count(self) -> int:
    """Count ungraded problems."""
    if not self.problems:
      return 0
    return sum(1 for p in self.problems if not p.graded)

  def calculate_total_score(self) -> float:
    """Calculate total score from problems (in-memory)."""
    if not self.problems:
      return 0.0
    return sum(p.score or 0.0 for p in self.problems if p.graded)

  def get_display_name_or_fallback(self) -> str:
    """Get best available name for display."""
    return (
      self.display_name
      or self.student_name
      or self.approximate_name
      or f"Document {self.document_id}"
    )
