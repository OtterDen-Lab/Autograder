"""
Data Transfer Objects (DTOs) for the web grading system.

DTOs represent data in transit between layers - they are NOT domain models.
They are used by services to return structured data before persistence.

Key differences from domain models:
- No database IDs or foreign keys (data not yet persisted)
- Pydantic models for validation and serialization
- May combine data from multiple sources
- Focused on business operations, not storage
"""

from .exam import ProblemDTO, SubmissionDTO

__all__ = [
  "ProblemDTO",
  "SubmissionDTO",
]
