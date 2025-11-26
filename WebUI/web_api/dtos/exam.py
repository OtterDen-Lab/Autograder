"""
DTOs for exam processing operations.

These represent the output of ExamProcessor.process_exams() before
the data is persisted to the database.
"""
from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict


class ProblemDTO(BaseModel):
  """
  A problem extracted from an exam PDF.

  This represents a problem BEFORE it's saved to the database.
  After processing, it will be converted to a domain.Problem and saved.
  """
  problem_number: int = Field(..., description="Problem number (1, 2, 3, etc.)")
  image_base64: str = Field(..., description="Base64 encoded PNG image")
  region_coords: dict = Field(..., description="PDF region coordinates")

  # Blank detection fields
  is_blank: bool = Field(default=False, description="Whether problem appears blank")
  blank_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence score 0-1")
  blank_method: Optional[str] = Field(default=None, description="Detection method used")
  blank_reasoning: Optional[str] = Field(default=None, description="Explanation of blank detection")

  # Max points (may be extracted from QR code or set manually)
  max_points: Optional[float] = Field(default=None, description="Maximum points for this problem")

  # Validation
  @validator('blank_confidence')
  def validate_blank_confidence(cls, v, values):
    """Ensure blank problems have reasonable confidence"""
    if values.get('is_blank') and v < 0.5:
      # Allow it but could warn
      pass
    return v

  # Helper methods for cross-submission processing
  def mark_blank(self, confidence: float, method: str, reasoning: str) -> None:
    """Mark this problem as blank with given confidence and reasoning"""
    self.is_blank = True
    self.blank_confidence = confidence
    self.blank_method = method
    self.blank_reasoning = reasoning

  def mark_not_blank(self) -> None:
    """Mark this problem as not blank"""
    self.is_blank = False
    self.blank_confidence = 0.0
    self.blank_method = None
    self.blank_reasoning = None

  def set_max_points(self, points: float) -> None:
    """Set maximum points for this problem"""
    self.max_points = points

  class Config:
    # Allow mutation for in-place modifications during processing
    validate_assignment = True
    # Keep dict keys when dumping to JSON
    use_enum_values = True


class SubmissionDTO(BaseModel):
  """
  A submission extracted from an exam PDF.

  This represents a submission BEFORE it's saved to the database.
  After user confirmation, it will be converted to a domain.Submission and saved.
  """
  document_id: int = Field(..., description="Unique document identifier")
  approximate_name: str = Field(..., description="Extracted student name (may be approximate)")
  name_image_data: str = Field(..., description="Base64 image of name region")

  # Student matching (initially None, filled after user confirmation)
  student_name: Optional[str] = Field(default=None, description="Confirmed student name")
  canvas_user_id: Optional[int] = Field(default=None, description="Confirmed Canvas user ID")
  suggested_canvas_user_id: Optional[int] = Field(default=None, description="AI-suggested Canvas user ID")

  # Problem shuffling (for randomized exams)
  page_mappings: List[int] = Field(default_factory=list, description="Page ordering for shuffled problems")

  # Problems in this submission
  problems: List[ProblemDTO] = Field(..., description="List of problems in this submission")

  # PDF data
  pdf_data: Optional[str] = Field(default=None, description="Base64 encoded original PDF")

  # File tracking
  file_hash: Optional[str] = Field(default=None, description="SHA256 hash for duplicate detection")
  original_filename: str = Field(..., description="Original uploaded filename")

  # Helper methods
  def get_problem(self, problem_number: int) -> Optional[ProblemDTO]:
    """Get a specific problem by number"""
    for problem in self.problems:
      if problem.problem_number == problem_number:
        return problem
    return None

  def get_blank_count(self) -> int:
    """Count how many problems are marked blank"""
    return sum(1 for p in self.problems if p.is_blank)

  def get_blank_percentage(self) -> float:
    """Get percentage of problems marked blank"""
    if not self.problems:
      return 0.0
    return (self.get_blank_count() / len(self.problems)) * 100.0

  def is_matched(self) -> bool:
    """Check if submission has been matched to a student"""
    return self.canvas_user_id is not None

  def match_to_student(self, student_name: str, canvas_user_id: int) -> None:
    """Match this submission to a student"""
    self.student_name = student_name
    self.canvas_user_id = canvas_user_id

  class Config:
    # Allow mutation for in-place modifications during processing
    validate_assignment = True
    use_enum_values = True
