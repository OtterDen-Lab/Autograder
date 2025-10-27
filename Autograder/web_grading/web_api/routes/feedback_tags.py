"""
Feedback tags endpoints for reusable grading comments.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from ..database import get_db_connection

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
async def get_feedback_tags(session_id: int, problem_number: int) -> List[FeedbackTag]:
    """
    Get all feedback tags for a specific session and problem number.
    Returns tags sorted by use_count (most used first), then by short_name.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, session_id, problem_number, short_name, comment_text,
                   created_at, use_count
            FROM feedback_tags
            WHERE session_id = ? AND problem_number = ?
            ORDER BY use_count DESC, short_name ASC
        """, (session_id, problem_number))

        tags = []
        for row in cursor.fetchall():
            tags.append(FeedbackTag(
                id=row["id"],
                session_id=row["session_id"],
                problem_number=row["problem_number"],
                short_name=row["short_name"],
                comment_text=row["comment_text"],
                created_at=row["created_at"],
                use_count=row["use_count"]
            ))

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
            status_code=400,
            detail="Short name must be between 1 and 30 characters"
        )

    if not tag.comment_text or len(tag.comment_text) > 500:
        raise HTTPException(
            status_code=400,
            detail="Comment text must be between 1 and 500 characters"
        )

    with get_db_connection() as conn:
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO feedback_tags (session_id, problem_number, short_name, comment_text)
                VALUES (?, ?, ?, ?)
            """, (tag.session_id, tag.problem_number, tag.short_name, tag.comment_text))

            tag_id = cursor.lastrowid

            # Fetch the created tag
            cursor.execute("""
                SELECT id, session_id, problem_number, short_name, comment_text,
                       created_at, use_count
                FROM feedback_tags
                WHERE id = ?
            """, (tag_id,))

            row = cursor.fetchone()
            return FeedbackTag(
                id=row["id"],
                session_id=row["session_id"],
                problem_number=row["problem_number"],
                short_name=row["short_name"],
                comment_text=row["comment_text"],
                created_at=row["created_at"],
                use_count=row["use_count"]
            )

        except Exception as e:
            # Handle duplicate short_name constraint
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(
                    status_code=409,
                    detail=f"A tag with the name '{tag.short_name}' already exists for this problem"
                )
            raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{tag_id}")
async def delete_feedback_tag(tag_id: int):
    """
    Delete a feedback tag.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM feedback_tags WHERE id = ?", (tag_id,))

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Tag not found")

        return {"success": True}


@router.post("/{tag_id}/use")
async def increment_tag_usage(tag_id: int):
    """
    Increment the use_count for a tag.
    Called when a tag is applied to a grade.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE feedback_tags
            SET use_count = use_count + 1
            WHERE id = ?
        """, (tag_id,))

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Tag not found")

        return {"success": True}
