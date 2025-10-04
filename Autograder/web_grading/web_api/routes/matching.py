"""
Name matching endpoints for unmatched submissions.
"""
from fastapi import APIRouter, HTTPException
from typing import List
import json

from ..models import NameMatchRequest
from ..database import get_db_connection
from lms_interface.canvas_interface import CanvasInterface

router = APIRouter()


@router.get("/{session_id}/submissions")
async def get_all_submissions(session_id: int):
    """Get all submissions for a session (unmatched first, then matched)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get all submissions, unmatched first
        cursor.execute("""
            SELECT id, document_id, approximate_name, name_image_data, student_name, canvas_user_id
            FROM submissions
            WHERE session_id = ?
            ORDER BY
                CASE WHEN canvas_user_id IS NULL THEN 0 ELSE 1 END,
                document_id
        """, (session_id,))

        submissions = []
        for row in cursor.fetchall():
            submissions.append({
                "id": row["id"],
                "document_id": row["document_id"],
                "approximate_name": row["approximate_name"] or "(no name detected)",
                "name_image_data": row["name_image_data"],
                "student_name": row["student_name"],
                "canvas_user_id": row["canvas_user_id"],
                "is_matched": row["canvas_user_id"] is not None
            })

        return {"submissions": submissions}


@router.get("/{session_id}/students")
async def get_all_students(session_id: int):
    """Get all Canvas students with match status (unmatched first, then matched)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get session info
        cursor.execute("SELECT course_id, assignment_id FROM grading_sessions WHERE id = ?", (session_id,))
        session = cursor.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Get Canvas students
        canvas_interface = CanvasInterface(prod=False)
        course = canvas_interface.get_course(session["course_id"])
        assignment = course.get_assignment(session["assignment_id"])
        all_students = assignment.get_students()

        # Get already matched user IDs
        cursor.execute("""
            SELECT DISTINCT canvas_user_id
            FROM submissions
            WHERE session_id = ? AND canvas_user_id IS NOT NULL
        """, (session_id,))
        matched_ids = {row["canvas_user_id"] for row in cursor.fetchall()}

        # Create list with all students, marked as matched or not
        students = [
            {
                "user_id": s.user_id,
                "name": s.name,
                "is_matched": s.user_id in matched_ids
            }
            for s in all_students
        ]

        # Sort: unmatched first, then alphabetically within each group
        students.sort(key=lambda s: (s["is_matched"], s["name"]))

        return {"students": students}


@router.post("/{session_id}/match")
async def match_submission(session_id: int, match: NameMatchRequest):
    """Manually match a submission to a Canvas student (allows reassignment)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify the submission exists and belongs to this session
        cursor.execute("""
            SELECT id FROM submissions
            WHERE id = ? AND session_id = ?
        """, (match.submission_id, session_id))

        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Submission not found")

        # Get student name from Canvas
        cursor.execute("SELECT course_id, assignment_id FROM grading_sessions WHERE id = ?", (session_id,))
        session = cursor.fetchone()

        canvas_interface = CanvasInterface(prod=False)
        course = canvas_interface.get_course(session["course_id"])
        assignment = course.get_assignment(session["assignment_id"])
        students = assignment.get_students()

        student = next((s for s in students if s.user_id == match.canvas_user_id), None)
        if not student:
            raise HTTPException(status_code=404, detail="Student not found in Canvas")

        # Check if this student is already matched to another submission
        cursor.execute("""
            SELECT id, document_id FROM submissions
            WHERE session_id = ? AND canvas_user_id = ? AND id != ?
        """, (session_id, match.canvas_user_id, match.submission_id))

        previous_match = cursor.fetchone()
        previous_submission_id = previous_match["id"] if previous_match else None

        # If student was previously matched to a different submission, unassign them
        if previous_submission_id:
            cursor.execute("""
                UPDATE submissions
                SET canvas_user_id = NULL,
                    student_name = NULL
                WHERE id = ?
            """, (previous_submission_id,))

        # Update submission with new match
        cursor.execute("""
            UPDATE submissions
            SET canvas_user_id = ?,
                student_name = ?
            WHERE id = ?
        """, (match.canvas_user_id, student.name, match.submission_id))

        # Check if all submissions are now matched
        cursor.execute("""
            SELECT COUNT(*) as unmatched_count
            FROM submissions
            WHERE session_id = ? AND canvas_user_id IS NULL
        """, (session_id,))

        unmatched_count = cursor.fetchone()["unmatched_count"]

        # Update session status if all matched
        if unmatched_count == 0:
            cursor.execute("""
                UPDATE grading_sessions
                SET status = 'ready', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (session_id,))

        return {
            "status": "matched",
            "student_name": student.name,
            "remaining_unmatched": unmatched_count,
            "reassigned_from": previous_submission_id
        }
