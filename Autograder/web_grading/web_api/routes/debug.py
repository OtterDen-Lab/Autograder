"""
Debug endpoints for testing and troubleshooting.
These endpoints provide alternative views and data access patterns
for development and debugging purposes.
"""
import logging
from fastapi import APIRouter, HTTPException
from typing import List, Optional
from pydantic import BaseModel

from ..database import get_db_connection

router = APIRouter(prefix="/debug", tags=["debug"])
log = logging.getLogger(__name__)


class SubmissionBlankStats(BaseModel):
    """Statistics about blank detection for a submission"""
    submission_id: int
    student_name: Optional[str]
    display_name: Optional[str]
    total_problems: int
    blank_detected: int
    blank_percentage: float
    graded_problems: int


@router.get("/sessions/{session_id}/submissions-by-blank-rate")
async def get_submissions_by_blank_rate(session_id: int) -> List[SubmissionBlankStats]:
    """
    Get all submissions sorted by percentage of problems detected as blank.
    Useful for debugging blank detection algorithm.

    Args:
        session_id: The grading session ID

    Returns:
        List of submissions with blank detection stats, sorted by blank_percentage descending
    """
    log.info(f"Getting submissions by blank rate for session {session_id}")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Get blank stats for each submission
        cursor.execute("""
            SELECT
                s.id as submission_id,
                s.student_name,
                s.display_name,
                COUNT(p.id) as total_problems,
                SUM(CASE WHEN p.is_blank = 1 THEN 1 ELSE 0 END) as blank_detected,
                SUM(CASE WHEN p.graded = 1 THEN 1 ELSE 0 END) as graded_problems,
                CAST(SUM(CASE WHEN p.is_blank = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(p.id) * 100 as blank_percentage
            FROM submissions s
            LEFT JOIN problems p ON p.submission_id = s.id
            WHERE s.session_id = ?
            GROUP BY s.id, s.student_name, s.display_name
            ORDER BY blank_percentage DESC
        """, (session_id,))

        results = []
        for row in cursor.fetchall():
            results.append(SubmissionBlankStats(
                submission_id=row["submission_id"],
                student_name=row["student_name"],
                display_name=row["display_name"],
                total_problems=row["total_problems"],
                blank_detected=row["blank_detected"] or 0,
                blank_percentage=row["blank_percentage"] or 0.0,
                graded_problems=row["graded_problems"] or 0,
            ))

        log.info(f"Found {len(results)} submissions for session {session_id}")
        return results


@router.get("/sessions/{session_id}/problem-blank-distribution")
async def get_problem_blank_distribution(session_id: int):
    """
    Get the distribution of blank detection across all problems.
    Shows how many submissions were marked blank for each problem number.

    Args:
        session_id: The grading session ID

    Returns:
        Dict with problem numbers as keys and blank counts as values
    """
    log.info(f"Getting problem blank distribution for session {session_id}")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        cursor.execute("""
            SELECT
                p.problem_number,
                COUNT(*) as total_submissions,
                SUM(CASE WHEN p.is_blank = 1 THEN 1 ELSE 0 END) as blank_count,
                CAST(SUM(CASE WHEN p.is_blank = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) * 100 as blank_percentage
            FROM problems p
            JOIN submissions s ON p.submission_id = s.id
            WHERE s.session_id = ?
            GROUP BY p.problem_number
            ORDER BY p.problem_number
        """, (session_id,))

        distribution = {}
        for row in cursor.fetchall():
            distribution[row["problem_number"]] = {
                "total": row["total_submissions"],
                "blank": row["blank_count"] or 0,
                "percentage": row["blank_percentage"] or 0.0
            }

        return distribution


@router.get("/sessions/{session_id}/problems/{problem_number}/submissions-by-ink")
async def get_submissions_by_ink_for_problem(session_id: int, problem_number: int):
    """
    Get all submissions for a specific problem, sorted by black pixel ratio (ink density).
    Useful for visually inspecting blank detection across all students for one problem.

    Args:
        session_id: The grading session ID
        problem_number: The problem number to inspect

    Returns:
        List of submissions sorted by black_pixel_ratio (ascending - least ink first)
    """
    import base64
    import json
    import fitz
    from ..services.exam_processor import ExamProcessor

    log.info(f"Getting submissions for session {session_id}, problem {problem_number}, sorted by ink")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Get all problems for this problem number
        # Parse the blank_reasoning to extract the actual black_pixel_ratio used by algorithm
        cursor.execute("""
            SELECT
                p.id as problem_id,
                p.submission_id,
                p.problem_number,
                p.region_coords,
                p.is_blank,
                p.blank_confidence,
                p.blank_method,
                p.blank_reasoning,
                p.score,
                p.feedback,
                p.graded,
                s.exam_pdf_data,
                s.student_name,
                s.display_name
            FROM problems p
            JOIN submissions s ON p.submission_id = s.id
            WHERE s.session_id = ? AND p.problem_number = ?
            ORDER BY p.id
        """, (session_id, problem_number))

        problems = cursor.fetchall()

        if not problems:
            return []

        exam_processor = ExamProcessor()
        submissions_with_ink = []

        for problem in problems:
            # Extract image
            region_coords = json.loads(problem["region_coords"])
            pdf_base64 = problem["exam_pdf_data"]

            start_page = region_coords["page_number"]
            start_y = region_coords["region_y_start"]
            end_page = region_coords.get("end_page_number", start_page)
            end_y = region_coords["region_y_end"]

            try:
                problem_image_base64, _ = exam_processor._extract_cross_page_region(
                    fitz.open(stream=base64.b64decode(pdf_base64), filetype="pdf"),
                    start_page, start_y,
                    end_page, end_y,
                    dpi=150
                )

                # Extract black_pixel_ratio from blank_reasoning field
                # Format: "Black ratio: 0.0420, Threshold (gap): 0.0255"
                black_ratio = 0.0
                reasoning = problem["blank_reasoning"]
                if reasoning:
                    import re
                    match = re.search(r'Black ratio:\s*([0-9.]+)', reasoning)
                    if match:
                        black_ratio = float(match.group(1))

            except Exception as e:
                log.error(f"Failed to process problem {problem['problem_id']}: {e}")
                problem_image_base64 = ""
                black_ratio = 0

            submissions_with_ink.append({
                "problem_id": problem["problem_id"],
                "submission_id": problem["submission_id"],
                "problem_number": problem["problem_number"],
                "student_name": problem["student_name"],
                "display_name": problem["display_name"],
                "is_blank": bool(problem["is_blank"]),
                "blank_confidence": problem["blank_confidence"] or 0.0,
                "blank_method": problem["blank_method"],
                "blank_reasoning": problem["blank_reasoning"],
                "score": problem["score"],
                "feedback": problem["feedback"],
                "graded": bool(problem["graded"]),
                "image_data": problem_image_base64,
                "black_pixel_ratio": black_ratio,
            })

        # Sort by black pixel ratio (ascending - least ink first)
        # This uses the ACTUAL ratio calculated by the population algorithm
        submissions_with_ink.sort(key=lambda x: x["black_pixel_ratio"])

        log.info(f"Returning {len(submissions_with_ink)} submissions for problem {problem_number}")
        return submissions_with_ink


@router.post("/sessions/{session_id}/clear-all-blank-flags")
async def clear_all_blank_flags(session_id: int):
    """
    Clear all is_blank flags for ungraded problems in a session.
    Useful for testing blank detection from scratch.

    WARNING: This will reset auto-detection for all ungraded problems.

    Args:
        session_id: The grading session ID

    Returns:
        Status and count of cleared flags
    """
    log.warning(f"Clearing all blank flags for session {session_id}")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Clear blank flags only for ungraded problems
        cursor.execute("""
            UPDATE problems
            SET is_blank = 0,
                blank_confidence = NULL,
                blank_method = NULL,
                blank_reasoning = NULL
            WHERE submission_id IN (
                SELECT id FROM submissions WHERE session_id = ?
            ) AND graded = 0
        """, (session_id,))

        rows_updated = cursor.rowcount
        conn.commit()

        log.info(f"Cleared blank flags for {rows_updated} ungraded problems in session {session_id}")

        return {
            "status": "success",
            "rows_updated": rows_updated,
            "message": f"Cleared blank flags for {rows_updated} ungraded problems"
        }
