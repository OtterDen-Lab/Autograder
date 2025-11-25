"""
Pydantic models for request/response validation.
"""
from datetime import datetime
from typing import Optional, List, Union
from pydantic import BaseModel, Field
from enum import Enum


class SessionStatus(str, Enum):
  """Session status states"""
  PREPROCESSING = "preprocessing"
  AWAITING_ALIGNMENT = "awaiting_alignment"
  NAME_MATCHING_NEEDED = "name_matching_needed"
  READY = "ready"
  GRADING = "grading"
  FINALIZING = "finalizing"
  FINALIZED = "finalized"
  COMPLETE = "complete"
  ERROR = "error"


class SessionCreate(BaseModel):
  """Request model for creating a new grading session"""
  course_id: int
  assignment_id: int
  assignment_name: str
  course_name: Optional[str] = None
  canvas_points: Optional[float] = None
  use_prod_canvas: bool = False


class SessionResponse(BaseModel):
  """Response model for session details"""
  id: int
  assignment_id: int
  assignment_name: str
  course_id: int
  course_name: Optional[str]
  status: SessionStatus
  created_at: datetime
  updated_at: datetime
  canvas_points: Optional[float]
  total_exams: int = 0
  processed_exams: int = 0
  matched_exams: int = 0
  processing_message: Optional[str] = None

  class Config:
    from_attributes = True


class SessionStatusUpdate(BaseModel):
  """Real-time status update for SSE"""
  session_id: int
  status: SessionStatus
  progress: Optional[float] = Field(None, ge=0.0, le=1.0)
  message: Optional[str] = None
  current_step: Optional[str] = None


class SessionStatusChange(BaseModel):
  """Simple status change for PATCH endpoint (no session_id needed)"""
  status: SessionStatus


class SubmissionResponse(BaseModel):
  """Response model for submission details"""
  id: int
  session_id: int
  document_id: int
  approximate_name: Optional[str]
  name_image_data: Optional[str]
  student_name: Optional[str]
  display_name: Optional[str]
  canvas_user_id: Optional[int]
  total_score: Optional[float]
  graded_at: Optional[datetime]

  class Config:
    from_attributes = True


class NameMatchRequest(BaseModel):
  """Request model for manual name matching"""
  submission_id: int
  canvas_user_id: int


class ProblemResponse(BaseModel):
  """Response model for problem data"""
  id: int
  problem_number: int
  submission_id: int
  image_data: str  # Base64 encoded PNG
  score: Optional[float]
  feedback: Optional[str]
  graded: bool
  max_points: Optional[float] = None

  # Metadata for grading context
  current_index: int
  total_count: int
  ungraded_blank: int = 0
  ungraded_nonblank: int = 0

  # Blank detection metadata
  is_blank: bool = False
  blank_confidence: float = 0.0
  blank_method: Optional[str] = None
  blank_reasoning: Optional[str] = None

  # AI grading metadata
  ai_reasoning: Optional[str] = None

  # QR code availability flag (for "Show Answer" button)
  has_qr_data: bool = False

  class Config:
    from_attributes = True


class GradeSubmission(BaseModel):
  """Request model for submitting a grade

    Score can be:
    - A numeric value (float) for normal grading
    - A dash string ("-") to mark as blank (sets score to 0 and is_blank flag)
    """
  score: Union[float, str]  # Accept float or "-" for blank
  feedback: Optional[str] = None


class ProblemStatsResponse(BaseModel):
  """Response model for problem statistics"""
  problem_number: int
  avg_score: Optional[float]
  min_score: Optional[float]
  max_score: Optional[float]
  median_score: Optional[float]
  stddev_score: Optional[float]
  mean_normalized: Optional[float]  # Mean normalized to max_points (0-1 scale)
  stddev_normalized: Optional[
    float]  # Stddev normalized to max_points (0-1 scale)
  pct_blank: Optional[float]  # Percentage of submissions marked as blank
  num_blank: int = 0  # Number of graded submissions marked as blank
  num_blank_ungraded: int = 0  # Number of ungraded submissions marked as blank
  num_graded: int
  num_total: int
  max_points: Optional[float]

  class Config:
    from_attributes = True


class SessionStatsResponse(BaseModel):
  """Response model for overall session statistics"""
  session_id: int
  total_submissions: int
  total_problems: int
  problems_graded: int
  problems_remaining: int
  progress_percentage: float
  problem_stats: List[ProblemStatsResponse]

  class Config:
    from_attributes = True


class UploadResponse(BaseModel):
  """Response model for file upload"""
  session_id: int
  files_uploaded: int
  status: str
  message: str
  composites: Optional[dict] = None  # page_num -> base64 image
  page_dimensions: Optional[dict] = None  # page_num -> {width, height}
  num_exams: Optional[int] = None
