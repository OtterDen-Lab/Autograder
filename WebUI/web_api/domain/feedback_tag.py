"""
Domain model for feedback tags.
"""
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class FeedbackTag:
  """
  Reusable grading comment for a specific problem.

  Feedback tags allow graders to create shortcuts for common feedback
  that can be quickly applied during grading.
  """
  id: int
  session_id: int
  problem_number: int
  short_name: str  # Display name (e.g., "partial", "good")
  comment_text: str  # Full feedback text
  created_at: datetime
  use_count: int  # How many times this tag has been used
