"""
Problem grading endpoints.
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime
from typing import Optional
import sys
from pathlib import Path

from ..models import ProblemResponse, GradeSubmission
from ..database import get_db_connection, update_problem_stats

# Add parent to path for AI helper import
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
import Autograder.ai_helper as ai_helper

router = APIRouter()


@router.get("/{session_id}/{problem_number}/next", response_model=ProblemResponse)
async def get_next_problem(session_id: int, problem_number: int):
    """Get next ungraded problem for a specific problem number"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get next ungraded problem
        cursor.execute("""
            SELECT * FROM problems
            WHERE session_id = ? AND problem_number = ? AND graded = 0
            ORDER BY RANDOM()
            LIMIT 1
        """, (session_id, problem_number))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No ungraded problems found for problem {problem_number}"
            )

        # Get counts for context
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as graded
            FROM problems
            WHERE session_id = ? AND problem_number = ?
        """, (session_id, problem_number))

        count_row = cursor.fetchone()
        total_count = count_row["total"]
        graded_count = count_row["graded"]
        current_index = graded_count + 1

        return ProblemResponse(
            id=row["id"],
            problem_number=row["problem_number"],
            submission_id=row["submission_id"],
            image_data=row["image_data"],
            score=row["score"],
            feedback=row["feedback"],
            graded=bool(row["graded"]),
            max_points=row["max_points"],
            current_index=current_index,
            total_count=total_count,
            is_blank=bool(row["is_blank"]),
            blank_confidence=row["blank_confidence"] or 0.0,
            blank_method=row["blank_method"],
            blank_reasoning=row["blank_reasoning"]
        )


@router.get("/{session_id}/{problem_number}/previous", response_model=ProblemResponse)
async def get_previous_problem(session_id: int, problem_number: int):
    """Get most recently graded problem for a specific problem number"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get most recently graded problem
        cursor.execute("""
            SELECT * FROM problems
            WHERE session_id = ? AND problem_number = ? AND graded = 1
            ORDER BY graded_at DESC
            LIMIT 1
        """, (session_id, problem_number))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No graded problems found for problem {problem_number}"
            )

        # Get counts for context
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as graded
            FROM problems
            WHERE session_id = ? AND problem_number = ?
        """, (session_id, problem_number))

        count_row = cursor.fetchone()
        total_count = count_row["total"]
        graded_count = count_row["graded"]
        current_index = graded_count

        return ProblemResponse(
            id=row["id"],
            problem_number=row["problem_number"],
            submission_id=row["submission_id"],
            image_data=row["image_data"],
            score=row["score"],
            feedback=row["feedback"],
            graded=bool(row["graded"]),
            max_points=row["max_points"],
            current_index=current_index,
            total_count=total_count,
            is_blank=bool(row["is_blank"]),
            blank_confidence=row["blank_confidence"] or 0.0,
            blank_method=row["blank_method"],
            blank_reasoning=row["blank_reasoning"]
        )


@router.post("/{problem_id}/grade")
async def grade_problem(problem_id: int, grade: GradeSubmission):
    """Submit a grade for a problem"""
    # Get session_id first
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Update problem
        cursor.execute("""
            UPDATE problems
            SET score = ?, feedback = ?, graded = 1, graded_at = ?
            WHERE id = ?
        """, (grade.score, grade.feedback, datetime.now(), problem_id))

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Problem not found")

        # Get session_id for stats update
        cursor.execute("SELECT session_id FROM problems WHERE id = ?", (problem_id,))
        row = cursor.fetchone()
        session_id = row["session_id"]

    # Update statistics after connection is closed to avoid database lock
    update_problem_stats(session_id)

    return {"status": "graded", "problem_id": problem_id}


@router.get("/{problem_id}", response_model=ProblemResponse)
async def get_problem(problem_id: int):
    """Get a specific problem by ID"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM problems WHERE id = ?", (problem_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Problem not found")

        # Get context counts
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as graded
            FROM problems
            WHERE session_id = ? AND problem_number = ?
        """, (row["session_id"], row["problem_number"]))

        count_row = cursor.fetchone()

        return ProblemResponse(
            id=row["id"],
            problem_number=row["problem_number"],
            submission_id=row["submission_id"],
            image_data=row["image_data"],
            score=row["score"],
            feedback=row["feedback"],
            graded=bool(row["graded"]),
            current_index=count_row["graded"] + 1,
            total_count=count_row["total"],
            is_blank=bool(row["is_blank"]),
            blank_confidence=row["blank_confidence"] or 0.0,
            blank_method=row["blank_method"],
            blank_reasoning=row["blank_reasoning"]
        )


@router.post("/{problem_id}/decipher")
async def decipher_handwriting(problem_id: int, use_premium_model: bool = False):
    """Use AI to transcribe handwritten text from a problem image

    Args:
        problem_id: ID of the problem to transcribe
        use_premium_model: If True, use a more capable (and expensive) model
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT image_data FROM problems WHERE id = ?", (problem_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Problem not found")

        image_base64 = row["image_data"]

    # Query AI to transcribe handwriting
    if use_premium_model:
        query = """Please transcribe all handwritten text from this exam answer with maximum accuracy.

Instructions:
- Transcribe ONLY handwritten text (ignore printed questions/instructions)
- Preserve the structure and organization of the answer exactly
- For unclear text, make your best interpretation and note uncertainty with [possibly: "alternative"]
- Describe any diagrams, drawings, or mathematical figures in detail within [brackets]
- Maintain all mathematical notation, equations, and symbols precisely
- Note any corrections, cross-outs, or marginal notes

Respond with just the transcribed text, being as thorough and accurate as possible."""
    else:
        query = """Please transcribe all handwritten text from this exam answer.

Instructions:
- Transcribe ONLY handwritten text (ignore printed questions/instructions)
- Preserve the structure and organization of the answer
- If text is unclear, use [unclear] notation
- If there are diagrams or drawings, describe them briefly in [brackets]
- Maintain mathematical notation as best as possible

Respond with just the transcribed text."""

    try:
        # Use Anthropic's AI with appropriate model
        ai = ai_helper.AI_Helper__Anthropic()

        # Override model if premium requested
        if use_premium_model:
            # Temporarily override the model in the client
            original_model = None
            response = ai._client.messages.create(
                model="claude-opus-4-20250514",  # Most capable model
                max_tokens=2000,  # More tokens for detailed transcription
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": query},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_base64
                            }
                        }
                    ]
                }]
            )
            transcription = response.content[0].text
        else:
            response, _ = ai.query_ai(
                query,
                attachments=[("png", image_base64)]
            )
            transcription = response

        return {
            "problem_id": problem_id,
            "transcription": transcription.strip(),
            "model": "premium (Opus)" if use_premium_model else "standard (Sonnet)"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")
