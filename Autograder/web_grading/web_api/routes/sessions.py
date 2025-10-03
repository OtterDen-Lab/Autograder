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
from lms_interface.canvas_interface import CanvasInterface

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
        row_dict = dict(row)

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
            total_exams=row_dict.get("total_exams", 0),
            processed_exams=row_dict.get("processed_exams", 0),
            matched_exams=row_dict.get("matched_exams", 0),
            processing_message=row_dict.get("processing_message"),
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

        row_dict = dict(row)
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
            total_exams=row_dict.get("total_exams", 0),
            processed_exams=row_dict.get("processed_exams", 0),
            matched_exams=row_dict.get("matched_exams", 0),
            processing_message=row_dict.get("processing_message"),
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
            row_dict = dict(row)
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
                total_exams=row_dict.get("total_exams", 0),
                processed_exams=row_dict.get("processed_exams", 0),
                matched_exams=row_dict.get("matched_exams", 0),
                processing_message=row_dict.get("processing_message"),
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


@router.get("/{session_id}/problem-numbers")
async def get_problem_numbers(session_id: int):
    """Get list of distinct problem numbers for a session"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT DISTINCT problem_number
            FROM problems
            WHERE session_id = ?
            ORDER BY problem_number
        """, (session_id,))

        problem_numbers = [row["problem_number"] for row in cursor.fetchall()]

        return {"problem_numbers": problem_numbers}


@router.get("/{session_id}/student-scores")
async def get_student_scores(session_id: int):
    """Get aggregated scores for all students in a session"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                s.id,
                s.student_name,
                s.canvas_user_id,
                COUNT(p.id) as total_problems,
                SUM(CASE WHEN p.graded = 1 THEN 1 ELSE 0 END) as graded_problems,
                SUM(CASE WHEN p.graded = 1 THEN p.score ELSE 0 END) as total_score
            FROM submissions s
            LEFT JOIN problems p ON p.submission_id = s.id
            WHERE s.session_id = ?
            GROUP BY s.id
            ORDER BY s.student_name
        """, (session_id,))

        students = []
        for row in cursor.fetchall():
            students.append({
                "student_name": row["student_name"],
                "canvas_user_id": row["canvas_user_id"],
                "total_problems": row["total_problems"],
                "graded_problems": row["graded_problems"],
                "total_score": row["total_score"],
                "is_complete": row["graded_problems"] == row["total_problems"]
            })

        return {"students": students}


@router.get("/{session_id}/canvas-info")
async def get_canvas_info(session_id: int):
    """Get Canvas course and assignment information for verification before finalization"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT course_id, assignment_id, course_name, assignment_name
            FROM grading_sessions
            WHERE id = ?
        """, (session_id,))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")

    # Initialize Canvas interface (hardcoded to dev for now)
    use_prod = False
    canvas = CanvasInterface(prod=use_prod)

    # Get course and assignment to construct URL
    course = canvas.get_course(row["course_id"])
    assignment = course.get_assignment(row["assignment_id"])

    # Get base URL from Canvas interface
    # Remove trailing slash and /api/v1 if present
    base_url = str(canvas.canvas._Canvas__requester.base_url)
    if base_url.endswith('/api/v1'):
        base_url = base_url[:-7]
    base_url = base_url.rstrip('/')

    # Construct Canvas URL
    canvas_url = f"{base_url}/courses/{row['course_id']}/assignments/{row['assignment_id']}"

    return {
        "course_id": row["course_id"],
        "course_name": row["course_name"],
        "assignment_id": row["assignment_id"],
        "assignment_name": row["assignment_name"],
        "canvas_url": canvas_url,
        "environment": "production" if use_prod else "development"
    }


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
