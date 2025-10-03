"""
Problem grading endpoints.
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime
from typing import Optional

from ..models import ProblemResponse, GradeSubmission
from ..database import get_db_connection, update_problem_stats

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
            current_index=current_index,
            total_count=total_count,
        )


@router.post("/{problem_id}/grade")
async def grade_problem(problem_id: int, grade: GradeSubmission):
    """Submit a grade for a problem"""
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

        # Update statistics
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
        )
