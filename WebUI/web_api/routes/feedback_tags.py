"""
Feedback tags endpoints for reusable grading comments.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import sqlite3

from ..repositories import FeedbackTagRepository

router = APIRouter()


class FeedbackTag(BaseModel):
  id: Optional[int] = None
  session_id: int
  problem_number: int
  short_name: str
  comment_text: str
  created_at: Optional[str] = None
  use_count: int = 0


class CreateTagRequest(BaseModel):
  session_id: int
  problem_number: int
  short_name: str
  comment_text: str


@router.get("/{session_id}/{problem_number}")
async def get_feedback_tags(session_id: int,
                            problem_number: int) -> List[FeedbackTag]:
  """
    Get all feedback tags for a specific session and problem number.
    Returns tags sorted by use_count (most used first), then by short_name.
    """
  repo = FeedbackTagRepository()
  domain_tags = repo.get_for_problem(session_id, problem_number)

  tags = []
  for tag in domain_tags:
    tags.append(
      FeedbackTag(id=tag.id,
                  session_id=tag.session_id,
                  problem_number=tag.problem_number,
                  short_name=tag.short_name,
                  comment_text=tag.comment_text,
                  created_at=tag.created_at.isoformat() if tag.created_at else None,
                  use_count=tag.use_count))

  return tags


@router.post("")
async def create_feedback_tag(tag: CreateTagRequest) -> FeedbackTag:
  """
    Create a new feedback tag.
    Returns the created tag with its ID.
    """
  # Validate inputs
  if not tag.short_name or len(tag.short_name) > 30:
    raise HTTPException(
      status_code=400, detail="Short name must be between 1 and 30 characters")

  if not tag.comment_text or len(tag.comment_text) > 500:
    raise HTTPException(
      status_code=400,
      detail="Comment text must be between 1 and 500 characters")

  repo = FeedbackTagRepository()

  try:
    created_tag = repo.create(tag.session_id, tag.problem_number,
                              tag.short_name, tag.comment_text)

    return FeedbackTag(id=created_tag.id,
                       session_id=created_tag.session_id,
                       problem_number=created_tag.problem_number,
                       short_name=created_tag.short_name,
                       comment_text=created_tag.comment_text,
                       created_at=created_tag.created_at.isoformat() if created_tag.created_at else None,
                       use_count=created_tag.use_count)

  except sqlite3.IntegrityError as e:
    # Handle duplicate short_name constraint
    if "UNIQUE constraint failed" in str(e):
      raise HTTPException(
        status_code=409,
        detail=
        f"A tag with the name '{tag.short_name}' already exists for this problem"
      )
    raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{tag_id}")
async def delete_feedback_tag(tag_id: int):
  """
    Delete a feedback tag.
    """
  repo = FeedbackTagRepository()

  if not repo.delete(tag_id):
    raise HTTPException(status_code=404, detail="Tag not found")

  return {"success": True}


@router.post("/{tag_id}/use")
async def increment_tag_usage(tag_id: int):
  """
    Increment the use_count for a tag.
    Called when a tag is applied to a grade.
    """
  repo = FeedbackTagRepository()

  if not repo.increment_use_count(tag_id):
    raise HTTPException(status_code=404, detail="Tag not found")

  return {"success": True}
