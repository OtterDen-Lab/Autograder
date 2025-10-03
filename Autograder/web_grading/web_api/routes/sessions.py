"""
Session management endpoints.
"""
from fastapi import APIRouter, HTTPException
from typing import List
import json
from datetime import datetime

from ..models import (
    SessionCreate,
    SessionResponse,
    SessionStatsResponse,
    ProblemStatsResponse,
)
from ..database import get_db_connection

router = APIRouter()


@router.post("", response_model=SessionResponse)
async def create_session(session: SessionCreate):
    """Create a new grading session"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO grading_sessions
            (assignment_id, assignment_name, course_id, course_name, status, canvas_points)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            session.assignment_id,
            session.assignment_name,
            session.course_id,
            session.course_name,
            "preprocessing",
            session.canvas_points,
        ))

        session_id = cursor.lastrowid

        # Fetch created session
        cursor.execute("SELECT * FROM grading_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()

        return SessionResponse(
            id=row["id"],
            assignment_id=row["assignment_id"],
            assignment_name=row["assignment_name"],
            course_id=row["course_id"],
            course_name=row["course_name"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            canvas_points=row["canvas_points"],
        )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: int):
    """Get session details"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM grading_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Session not found")

        return SessionResponse(
            id=row["id"],
            assignment_id=row["assignment_id"],
            assignment_name=row["assignment_name"],
            course_id=row["course_id"],
            course_name=row["course_name"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            canvas_points=row["canvas_points"],
        )


@router.get("", response_model=List[SessionResponse])
async def list_sessions():
    """List all grading sessions"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM grading_sessions
            ORDER BY created_at DESC
        """)

        sessions = []
        for row in cursor.fetchall():
            sessions.append(SessionResponse(
                id=row["id"],
                assignment_id=row["assignment_id"],
                assignment_name=row["assignment_name"],
                course_id=row["course_id"],
                course_name=row["course_name"],
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                canvas_points=row["canvas_points"],
            ))

        return sessions


@router.get("/{session_id}/stats", response_model=SessionStatsResponse)
async def get_session_stats(session_id: int):
    """Get grading statistics for a session"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get overall stats
        cursor.execute("""
            SELECT
                COUNT(DISTINCT submission_id) as total_submissions,
                COUNT(*) as total_problems,
                SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as problems_graded
            FROM problems
            WHERE session_id = ?
        """, (session_id,))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")

        total_submissions = row["total_submissions"] or 0
        total_problems = row["total_problems"] or 0
        problems_graded = row["problems_graded"] or 0
        problems_remaining = total_problems - problems_graded
        progress = (problems_graded / total_problems * 100) if total_problems > 0 else 0

        # Get per-problem stats
        cursor.execute("""
            SELECT * FROM problem_stats
            WHERE session_id = ?
            ORDER BY problem_number
        """, (session_id,))

        problem_stats = []
        for stat_row in cursor.fetchall():
            problem_stats.append(ProblemStatsResponse(
                problem_number=stat_row["problem_number"],
                avg_score=stat_row["avg_score"],
                num_graded=stat_row["num_graded"],
                num_total=stat_row["num_total"],
            ))

        return SessionStatsResponse(
            session_id=session_id,
            total_submissions=total_submissions,
            total_problems=total_problems,
            problems_graded=problems_graded,
            problems_remaining=problems_remaining,
            progress_percentage=progress,
            problem_stats=problem_stats,
        )


@router.delete("/{session_id}")
async def delete_session(session_id: int):
    """Delete a grading session and all associated data"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Delete in order due to foreign keys
        cursor.execute("DELETE FROM problems WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM problem_stats WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM submissions WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM grading_sessions WHERE id = ?", (session_id,))

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Session not found")

        return {"status": "deleted", "session_id": session_id}
