"""
Domain model for individual problems within submissions.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Problem:
  """
  Domain model for problems table.

  Represents a single problem instance within a submission.

  Attributes:
    id: Primary key
    session_id: Foreign key to grading_sessions
    submission_id: Foreign key to submissions
    problem_number: Problem identifier (e.g., 1, 2, 3)
    score: Points awarded
    feedback: Grader feedback/comments
    graded: Whether problem has been graded (0 or 1)
    graded_at: When problem was graded
    is_blank: Whether problem appears blank
    blank_confidence: Confidence score for blank detection (0.0-1.0)
    blank_method: Method used for blank detection
    blank_reasoning: Explanation of blank detection
    max_points: Maximum points possible for this problem
    ai_reasoning: AI-generated grading explanation
    region_coords: JSON with page/region coordinates
    qr_encrypted_data: Encrypted data from QR code
    transcription: Handwriting transcription
    transcription_model: Model used for transcription
    transcription_cached_at: When transcription was cached
  """
  id: int
  session_id: int
  submission_id: int
  problem_number: int
  score: Optional[float] = None
  feedback: Optional[str] = None
  graded: bool = False
  graded_at: Optional[datetime] = None
  is_blank: bool = False
  blank_confidence: float = 0.0
  blank_method: Optional[str] = None
  blank_reasoning: Optional[str] = None
  max_points: Optional[float] = None
  ai_reasoning: Optional[str] = None
  region_coords: Optional[dict] = None
  qr_encrypted_data: Optional[str] = None
  transcription: Optional[str] = None
  transcription_model: Optional[str] = None
  transcription_cached_at: Optional[datetime] = None

  # Business logic methods

  def mark_graded(self, score: float, feedback: Optional[str] = None) -> None:
    """
    Mark problem as graded with score and feedback (in-memory).
    Caller must persist via repository.update_grade().
    """
    self.score = score
    self.feedback = feedback
    self.graded = True
    self.graded_at = datetime.now()

  def mark_blank(self, confidence: float, method: str, reasoning: Optional[str] = None) -> None:
    """
    Mark problem as blank (in-memory).
    Caller must persist via repository.update().
    """
    self.is_blank = True
    self.blank_confidence = confidence
    self.blank_method = method
    self.blank_reasoning = reasoning

  def is_perfect_score(self) -> bool:
    """Check if problem received maximum points."""
    if self.score is None or self.max_points is None:
      return False
    return self.score >= self.max_points

  def get_score_percentage(self) -> Optional[float]:
    """Get score as percentage of max_points."""
    if self.score is None or self.max_points is None or self.max_points == 0:
      return None
    return (self.score / self.max_points) * 100.0

  def has_region_coords(self) -> bool:
    """Check if problem has region coordinates for PDF extraction."""
    return self.region_coords is not None

  def is_cross_page(self) -> bool:
    """Check if problem spans multiple pages."""
    if not self.region_coords:
      return False
    return "end_page_number" in self.region_coords
