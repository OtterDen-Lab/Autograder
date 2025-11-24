"""
Session management endpoints.
"""
from fastapi import APIRouter, HTTPException, Response, UploadFile, File
from fastapi.responses import StreamingResponse
from typing import List, Optional
import json
import io
from datetime import datetime

import logging
import json
import base64
import fitz
from ..services.qr_scanner import QRScanner
from ..services.exam_processor import ExamProcessor

from ..models import (
    SessionCreate,
    SessionResponse,
    SessionStatsResponse,
    ProblemStatsResponse,
    SessionStatusUpdate,
    SessionStatusChange,
)
from ..database import get_db_connection
from lms_interface.canvas_interface import CanvasInterface
from ..services.qr_scanner import QRScanner
import os

router = APIRouter()


@router.post("", response_model=SessionResponse)
async def create_session(session: SessionCreate):
    """Create a new grading session"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO grading_sessions
            (assignment_id, assignment_name, course_id, course_name, status, canvas_points, use_prod_canvas)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session.assignment_id,
            session.assignment_name,
            session.course_id,
            session.course_name,
            "preprocessing",
            session.canvas_points,
            1 if session.use_prod_canvas else 0,
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


@router.patch("/{session_id}/status")
async def update_session_status(session_id: int, status_update: SessionStatusChange):
    """Update session status (e.g., from name_matching_needed to ready)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Update status
        cursor.execute("""
            UPDATE grading_sessions
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (status_update.status, session_id))

        return {"status": "updated", "session_id": session_id, "new_status": status_update.status}


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

        # Get per-problem stats (calculate comprehensive statistics)
        cursor.execute("""
            SELECT DISTINCT problem_number
            FROM problems
            WHERE session_id = ?
            ORDER BY problem_number
        """, (session_id,))

        problem_numbers = [row["problem_number"] for row in cursor.fetchall()]
        problem_stats = []

        for problem_num in problem_numbers:
            # Get all scores for this problem (for median and stddev)
            cursor.execute("""
                SELECT score, is_blank
                FROM problems
                WHERE session_id = ? AND problem_number = ? AND graded = 1
            """, (session_id, problem_num))

            results = cursor.fetchall()
            scores = [row["score"] for row in results if row["score"] is not None]
            num_blank = sum(1 for row in results if row["is_blank"])

            # Get max_points for this problem (default to 8 if not set)
            cursor.execute("""
                SELECT max_points
                FROM problem_metadata
                WHERE session_id = ? AND problem_number = ?
            """, (session_id, problem_num))
            max_points_row = cursor.fetchone()
            max_points = max_points_row["max_points"] if max_points_row else 8.0

            # Get total count (including ungraded) and count of ungraded blanks
            cursor.execute("""
                SELECT COUNT(*) as num_total,
                       SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as num_graded,
                       SUM(CASE WHEN graded = 0 AND is_blank = 1 THEN 1 ELSE 0 END) as num_blank_ungraded,
                       SUM(CASE WHEN is_blank = 1 THEN 1 ELSE 0 END) as num_blank_total
                FROM problems
                WHERE session_id = ? AND problem_number = ?
            """, (session_id, problem_num))
            count_row = cursor.fetchone()
            num_total = count_row["num_total"]
            num_graded = count_row["num_graded"]
            num_blank_ungraded = count_row["num_blank_ungraded"] or 0
            num_blank_total = count_row["num_blank_total"] or 0

            # Debug log to see what we're getting
            import logging
            log = logging.getLogger(__name__)
            log.info(f"[STATS] Problem {problem_num}: total={num_total}, graded={num_graded}, blank_ungraded={num_blank_ungraded}, blank_total={num_blank_total}")

            # Calculate statistics
            import statistics
            avg_score = statistics.mean(scores) if scores else None
            min_score = min(scores) if scores else None
            max_score = max(scores) if scores else None
            median_score = statistics.median(scores) if scores else None
            stddev_score = statistics.stdev(scores) if len(scores) > 1 else None

            # Calculate normalized mean and stddev (0-1 scale based on max_points)
            mean_normalized = None
            stddev_normalized = None
            if avg_score is not None and max_points is not None and max_points > 0:
                mean_normalized = avg_score / max_points
            if stddev_score is not None and max_points is not None and max_points > 0:
                stddev_normalized = stddev_score / max_points

            # Calculate percentage blank
            pct_blank = (num_blank / num_graded * 100) if num_graded > 0 else None

            problem_stats.append(ProblemStatsResponse(
                problem_number=problem_num,
                avg_score=avg_score,
                min_score=min_score,
                max_score=max_score,
                median_score=median_score,
                stddev_score=stddev_score,
                mean_normalized=mean_normalized,
                stddev_normalized=stddev_normalized,
                pct_blank=pct_blank,
                num_blank=num_blank,
                num_blank_ungraded=num_blank_ungraded,
                num_graded=num_graded,
                num_total=num_total,
                max_points=max_points,
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


@router.get("/{session_id}/submissions/{submission_id}/problems")
async def get_submission_problems(session_id: int, submission_id: int):
    """Get all problems for a specific submission"""
    from ..models import ProblemResponse

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify submission belongs to this session and get PDF data
        cursor.execute("""
            SELECT id, exam_pdf_data FROM submissions
            WHERE id = ? AND session_id = ?
        """, (submission_id, session_id))

        submission_row = cursor.fetchone()
        if not submission_row:
            raise HTTPException(status_code=404, detail="Submission not found in this session")

        pdf_base64 = submission_row["exam_pdf_data"]

        # Get all problems for this submission
        cursor.execute("""
            SELECT
                id, problem_number, submission_id, region_coords,
                score, feedback, graded, is_blank,
                blank_confidence, blank_method, blank_reasoning
            FROM problems
            WHERE submission_id = ?
            ORDER BY problem_number
        """, (submission_id,))

        problems = []
        exam_processor = ExamProcessor()

        for row in cursor.fetchall():
            # Extract image from PDF using region coords
            region_coords = json.loads(row["region_coords"])
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
            except Exception as e:
                log.error(f"Failed to extract image for problem {row['id']}: {e}")
                problem_image_base64 = ""

            problems.append(ProblemResponse(
                id=row["id"],
                problem_number=row["problem_number"],
                submission_id=row["submission_id"],
                image_data=problem_image_base64,
                score=row["score"],
                feedback=row["feedback"],
                graded=bool(row["graded"]),
                is_blank=bool(row["is_blank"]),
                blank_confidence=row["blank_confidence"] or 0.0,
                blank_method=row["blank_method"],
                blank_reasoning=row["blank_reasoning"],
                current_index=0,  # Not applicable for this endpoint
                total_count=0,    # Not applicable for this endpoint
                ungraded_blank=0, # Not applicable for this endpoint
                ungraded_nonblank=0, # Not applicable for this endpoint
                has_qr_data=False # Not needed for debug view
            ))

        return problems


@router.get("/{session_id}/canvas-info")
async def get_canvas_info(session_id: int):
    """Get Canvas course and assignment information for verification before finalization"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT course_id, assignment_id, course_name, assignment_name, use_prod_canvas
            FROM grading_sessions
            WHERE id = ?
        """, (session_id,))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")

    # Get Canvas environment from session (default to False for older sessions)
    # Note: SQLite stores booleans as INTEGER (0 or 1)
    try:
        use_prod = bool(row["use_prod_canvas"]) if "use_prod_canvas" in row.keys() else False
    except (KeyError, IndexError):
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


@router.put("/{session_id}/canvas-config")
async def update_canvas_config(
    session_id: int,
    course_id: int,
    assignment_id: int,
    use_prod: bool = False
):
    """Update Canvas configuration for a session (useful for switching dev→prod)"""
    # Get course and assignment details from Canvas
    canvas_interface = CanvasInterface(prod=use_prod)
    try:
        course = canvas_interface.get_course(course_id)
        assignment = course.get_assignment(assignment_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE grading_sessions
                SET course_id = ?,
                    course_name = ?,
                    assignment_id = ?,
                    assignment_name = ?,
                    use_prod_canvas = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                course_id,
                course.name,
                assignment_id,
                assignment.name,
                1 if use_prod else 0,
                session_id
            ))

            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Session not found")

        return {
            "status": "updated",
            "course_id": course_id,
            "course_name": course.name,
            "assignment_id": assignment_id,
            "assignment_name": assignment.name,
            "environment": "production" if use_prod else "development"
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch Canvas data: {str(e)}")


@router.get("/{session_id}/problem-max-points-all")
async def get_all_problem_max_points(session_id: int):
    """Get max points for all problems in a session"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Get all max points from metadata
        cursor.execute("""
            SELECT problem_number, max_points
            FROM problem_metadata
            WHERE session_id = ?
        """, (session_id,))

        max_points = {row["problem_number"]: row["max_points"] for row in cursor.fetchall()}

        return {"max_points": max_points}


@router.put("/{session_id}/problem-max-points")
async def update_problem_max_points(
    session_id: int,
    problem_number: int,
    max_points: float
):
    """Update max points for a specific problem number in a session"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Update metadata
        cursor.execute("""
            INSERT INTO problem_metadata (session_id, problem_number, max_points)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id, problem_number)
            DO UPDATE SET max_points = excluded.max_points, updated_at = CURRENT_TIMESTAMP
        """, (session_id, problem_number, max_points))

        # Update all existing problems with this number
        cursor.execute("""
            UPDATE problems
            SET max_points = ?
            WHERE session_id = ? AND problem_number = ?
        """, (max_points, session_id, problem_number))

        return {
            "status": "updated",
            "session_id": session_id,
            "problem_number": problem_number,
            "max_points": max_points,
            "problems_updated": cursor.rowcount
        }


@router.get("/{session_id}/default-feedback/{problem_number}")
async def get_default_feedback(session_id: int, problem_number: int):
    """Get default feedback for a specific problem number"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT default_feedback, default_feedback_threshold
            FROM problem_metadata
            WHERE session_id = ? AND problem_number = ?
        """, (session_id, problem_number))

        row = cursor.fetchone()
        if row:
            return {
                "default_feedback": row["default_feedback"],
                "default_feedback_threshold": row["default_feedback_threshold"] or 100.0
            }
        else:
            return {
                "default_feedback": None,
                "default_feedback_threshold": 100.0
            }


@router.put("/{session_id}/default-feedback")
async def update_default_feedback(
    session_id: int,
    problem_number: int,
    default_feedback: str = None,
    threshold: float = 100.0
):
    """Update default feedback for a specific problem number"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Update or create metadata
        cursor.execute("""
            INSERT INTO problem_metadata (session_id, problem_number, default_feedback, default_feedback_threshold)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, problem_number)
            DO UPDATE SET
                default_feedback = excluded.default_feedback,
                default_feedback_threshold = excluded.default_feedback_threshold,
                updated_at = CURRENT_TIMESTAMP
        """, (session_id, problem_number, default_feedback, threshold))

        return {
            "status": "updated",
            "session_id": session_id,
            "problem_number": problem_number,
            "default_feedback": default_feedback,
            "threshold": threshold
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


@router.get("/{session_id}/export")
async def export_session(session_id: int):
    """Export complete session data as JSON for checkpointing"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get session metadata
        cursor.execute("SELECT * FROM grading_sessions WHERE id = ?", (session_id,))
        session_row = cursor.fetchone()
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found")

        session_data = dict(session_row)

        # Get all submissions
        cursor.execute("SELECT * FROM submissions WHERE session_id = ?", (session_id,))
        submissions = [dict(row) for row in cursor.fetchall()]

        # Get all problems for each submission
        for submission in submissions:
            cursor.execute("""
                SELECT * FROM problems
                WHERE session_id = ? AND submission_id = ?
                ORDER BY problem_number
            """, (session_id, submission["id"]))
            submission["problems"] = [dict(row) for row in cursor.fetchall()]

        # Get problem stats
        cursor.execute("SELECT * FROM problem_stats WHERE session_id = ?", (session_id,))
        problem_stats = [dict(row) for row in cursor.fetchall()]

        # Get problem metadata (max_points, default_feedback, etc.)
        cursor.execute("SELECT * FROM problem_metadata WHERE session_id = ?", (session_id,))
        problem_metadata = [dict(row) for row in cursor.fetchall()]

        # Get feedback tags
        cursor.execute("SELECT * FROM feedback_tags WHERE session_id = ?", (session_id,))
        feedback_tags = [dict(row) for row in cursor.fetchall()]

        # Build export structure
        export_data = {
            "export_version": 1,
            "exported_at": datetime.now().isoformat(),
            "session": session_data,
            "submissions": submissions,
            "problem_stats": problem_stats,
            "problem_metadata": problem_metadata,
            "feedback_tags": feedback_tags
        }

        # Create JSON response
        json_str = json.dumps(export_data, indent=2, default=str)

        # Generate filename
        assignment_name = session_data["assignment_name"].replace(" ", "_")
        filename = f"grading_session_{session_id}_{assignment_name}.json"

        # Return as downloadable file
        return StreamingResponse(
            io.BytesIO(json_str.encode()),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


@router.post("/import")
async def import_session(file: UploadFile = File(...)):
    """Import session data from JSON checkpoint file"""
    import logging
    log = logging.getLogger(__name__)

    try:
        # Read file content
        content = await file.read()

        # Parse JSON
        import_data = json.loads(content.decode())

        # Validate structure
        if import_data.get("export_version") != 1:
            raise HTTPException(status_code=400, detail="Unsupported export version")

        session_data = import_data["session"]
        submissions = import_data["submissions"]
        problem_stats = import_data.get("problem_stats", [])
        problem_metadata = import_data.get("problem_metadata", [])
        feedback_tags = import_data.get("feedback_tags", [])

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Create new session (without id to get auto-increment)
            cursor.execute("""
                INSERT INTO grading_sessions
                (assignment_id, assignment_name, course_id, course_name, status, canvas_points,
                 created_at, updated_at, total_exams, processed_exams, matched_exams, processing_message, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_data["assignment_id"],
                session_data["assignment_name"],
                session_data["course_id"],
                session_data.get("course_name"),
                session_data["status"],
                session_data.get("canvas_points"),
                session_data.get("created_at"),
                datetime.now(),  # Use current time for updated_at
                session_data.get("total_exams", 0),
                session_data.get("processed_exams", 0),
                session_data.get("matched_exams", 0),
                session_data.get("processing_message"),
                session_data.get("metadata")
            ))

            new_session_id = cursor.lastrowid
            log.info(f"Created new session {new_session_id} from import")

            # Import submissions and problems
            submission_id_map = {}  # Map old submission_id -> new submission_id

            for submission in submissions:
                old_submission_id = submission["id"]

                cursor.execute("""
                    INSERT INTO submissions
                    (session_id, document_id, approximate_name, name_image_data, student_name, display_name,
                     canvas_user_id, page_mappings, total_score, graded_at, file_hash, original_filename, exam_pdf_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    new_session_id,
                    submission["document_id"],
                    submission.get("approximate_name"),
                    submission.get("name_image_data"),
                    submission.get("student_name"),
                    submission.get("display_name"),
                    submission.get("canvas_user_id"),
                    submission["page_mappings"],
                    submission.get("total_score"),
                    submission.get("graded_at"),
                    submission.get("file_hash"),
                    submission.get("original_filename"),
                    submission.get("exam_pdf_data")
                ))

                new_submission_id = cursor.lastrowid
                submission_id_map[old_submission_id] = new_submission_id

                # Import problems for this submission
                for problem in submission.get("problems", []):
                    cursor.execute("""
                        INSERT INTO problems
                        (session_id, submission_id, problem_number, score, feedback,
                         graded, graded_at, is_blank, blank_confidence, blank_method, blank_reasoning, max_points,
                         region_coords, qr_encrypted_data, ai_reasoning, transcription, transcription_model, transcription_cached_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        new_session_id,
                        new_submission_id,
                        problem["problem_number"],
                        problem.get("score"),
                        problem.get("feedback"),
                        problem.get("graded", 0),
                        problem.get("graded_at"),
                        problem.get("is_blank", 0),
                        problem.get("blank_confidence", 0.0),
                        problem.get("blank_method"),
                        problem.get("blank_reasoning"),
                        problem.get("max_points"),
                        problem.get("region_coords"),
                        problem.get("qr_encrypted_data"),
                        problem.get("ai_reasoning"),
                        problem.get("transcription"),
                        problem.get("transcription_model"),
                        problem.get("transcription_cached_at")
                    ))

            # Import problem stats
            for stat in problem_stats:
                cursor.execute("""
                    INSERT INTO problem_stats
                    (session_id, problem_number, avg_score, num_graded, num_total, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    new_session_id,
                    stat["problem_number"],
                    stat.get("avg_score"),
                    stat.get("num_graded", 0),
                    stat.get("num_total", 0),
                    datetime.now()
                ))

            # Import problem metadata (max_points, default_feedback, etc.)
            for metadata in problem_metadata:
                cursor.execute("""
                    INSERT INTO problem_metadata
                    (session_id, problem_number, max_points, default_feedback, default_feedback_threshold)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    new_session_id,
                    metadata["problem_number"],
                    metadata.get("max_points"),
                    metadata.get("default_feedback"),
                    metadata.get("default_feedback_threshold", 100.0)
                ))

            # Import feedback tags
            for tag in feedback_tags:
                cursor.execute("""
                    INSERT INTO feedback_tags
                    (session_id, problem_number, short_name, comment_text, use_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    new_session_id,
                    tag["problem_number"],
                    tag["short_name"],
                    tag["comment_text"],
                    tag.get("use_count", 0),
                    tag.get("created_at", datetime.now())
                ))

            log.info(f"Imported {len(submissions)} submissions, {sum(len(s.get('problems', [])) for s in submissions)} problems, {len(problem_metadata)} metadata entries, and {len(feedback_tags)} feedback tags")

        return {
            "status": "imported",
            "session_id": new_session_id,
            "assignment_name": session_data["assignment_name"],
            "submissions_imported": len(submissions)
        }

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    except Exception as e:
        log.error(f"Import failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


@router.post("/encryption-key/test")
async def test_encryption_key(encrypted_data: str, encryption_key: str):
    """Test if an encryption key can decrypt sample QR code data"""
    from ..services.qr_scanner import MinimalQuestionQRCode
    import logging
    log = logging.getLogger(__name__)

    try:
        # Try to decrypt with the provided key
        metadata = MinimalQuestionQRCode.decrypt_question_data(
            encrypted_data,
            encryption_key.encode()
        )

        return {
            "status": "success",
            "message": "Encryption key is valid",
            "metadata": metadata
        }
    except Exception as e:
        log.warning(f"Failed to decrypt with provided key: {e}")
        return {
            "status": "failed",
            "message": f"Encryption key failed to decrypt: {str(e)}"
        }


@router.post("/encryption-key/set")
async def set_encryption_key(encryption_key: str):
    """
    Set the encryption key for the current session (runtime only, not persisted).
    This is a workaround for when the QUIZ_ENCRYPTION_KEY env var isn't available.
    """
    import logging
    log = logging.getLogger(__name__)

    # Set the environment variable for this process
    os.environ['QUIZ_ENCRYPTION_KEY'] = encryption_key

    log.info("Encryption key updated for current session (runtime only)")

    return {
        "status": "success",
        "message": "Encryption key set for current session. This will be lost when the server restarts."
    }


@router.post("/{session_id}/rescan-qr")
async def rescan_qr_codes(session_id: int, dpi: int = 600):
    """
    Re-scan QR codes for all problems in a session at a specified DPI.
    This is useful when the initial scan fails to detect QR codes.

    Args:
        session_id: The session ID to re-scan
        dpi: DPI to use for rendering (default 600, higher = better for complex QR codes)

    Returns:
        Statistics about QR codes found and updated
    """
    log = logging.getLogger(__name__)
    log.info(f"Re-scanning QR codes for session {session_id} at {dpi} DPI")

    # Initialize QR scanner
    qr_scanner = QRScanner()
    if not qr_scanner.available:
        raise HTTPException(status_code=400, detail="QR scanner not available (opencv-python or pyzbar not installed)")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Verify session exists
        cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        # Get all submissions with their PDF data and problems
        cursor.execute("""
            SELECT id, exam_pdf_data
            FROM submissions
            WHERE session_id = ? AND exam_pdf_data IS NOT NULL
        """, (session_id,))

        submissions = cursor.fetchall()

        if not submissions:
            raise HTTPException(status_code=400, detail="No submissions with PDF data found in this session")

        total_submissions = len(submissions)
        total_problems_scanned = 0
        total_qr_codes_found = 0
        problems_updated = 0

        for submission in submissions:
            submission_id = submission["id"]
            pdf_base64 = submission["exam_pdf_data"]

            # Decode PDF
            pdf_bytes = base64.b64decode(pdf_base64)
            pdf_document = fitz.open("pdf", pdf_bytes)

            # Get all problems for this submission
            cursor.execute("""
                SELECT id, problem_number, region_coords
                FROM problems
                WHERE session_id = ? AND submission_id = ?
                ORDER BY problem_number
            """, (session_id, submission_id))

            problems = cursor.fetchall()

            for problem in problems:
                problem_id = problem["id"]
                problem_number = problem["problem_number"]
                region_coords_json = problem["region_coords"]

                if not region_coords_json:
                    log.warning(f"Problem {problem_id} (number {problem_number}) has no region coordinates, skipping")
                    continue

                # Parse region coordinates
                region_coords = json.loads(region_coords_json)
                start_page = region_coords["page_number"]
                start_y = region_coords["region_y_start"]
                end_page = region_coords.get("end_page_number", start_page)
                end_y = region_coords["region_y_end"]

                # Use ExamProcessor to extract the region at higher DPI
                exam_processor = ExamProcessor()
                problem_image_base64, _ = exam_processor._extract_cross_page_region(
                    pdf_document,
                    start_page, start_y,
                    end_page, end_y,
                    dpi=dpi
                )

                total_problems_scanned += 1

                # Scan for QR code
                qr_data = qr_scanner.scan_qr_from_image(problem_image_base64)

                if qr_data:
                    log.info(f"Problem {problem_number} (ID {problem_id}): Found QR code with max_points={qr_data['max_points']}")
                    total_qr_codes_found += 1

                    # Update problem with QR data
                    cursor.execute("""
                        UPDATE problems
                        SET max_points = ?,
                            qr_encrypted_data = ?
                        WHERE id = ?
                    """, (qr_data["max_points"], qr_data.get("encrypted_data"), problem_id))

                    # Also update problem_metadata for this session
                    cursor.execute("""
                        INSERT INTO problem_metadata (session_id, problem_number, max_points)
                        VALUES (?, ?, ?)
                        ON CONFLICT(session_id, problem_number)
                        DO UPDATE SET max_points = excluded.max_points, updated_at = CURRENT_TIMESTAMP
                    """, (session_id, problem_number, qr_data["max_points"]))

                    problems_updated += 1
                else:
                    log.debug(f"Problem {problem_number} (ID {problem_id}): No QR code found")

            pdf_document.close()

        log.info(f"QR re-scan complete: {total_qr_codes_found} codes found in {total_problems_scanned} problems across {total_submissions} submissions")

        return {
            "status": "success",
            "total_submissions": total_submissions,
            "total_problems_scanned": total_problems_scanned,
            "qr_codes_found": total_qr_codes_found,
            "problems_updated": problems_updated,
            "dpi_used": dpi,
            "message": f"Re-scanned {total_problems_scanned} problems at {dpi} DPI. Found {total_qr_codes_found} QR codes and updated {problems_updated} problems."
        }


@router.post("/{session_id}/fix-blank-counts")
async def fix_blank_counts(session_id: int):
    """
    Fix is_blank flags for graded problems to match actual grades.
    Sets is_blank=1 only for graded problems where score=0.
    This repairs any corruption from rerun-blank-detection on already-graded problems.
    """
    log = logging.getLogger(__name__)
    log.info(f"Fixing blank counts for session {session_id}")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Reset is_blank to match actual grades for ALL graded problems
        # If graded and score=0: is_blank=1
        # If graded and score>0: is_blank=0
        # If ungraded: leave is_blank unchanged (from auto-detection)
        cursor.execute("""
            UPDATE problems
            SET is_blank = CASE
                WHEN graded = 1 AND score = 0 THEN 1
                WHEN graded = 1 AND score > 0 THEN 0
                ELSE is_blank
            END
            WHERE session_id = (SELECT id FROM grading_sessions WHERE id = ?)
        """, (session_id,))

        rows_updated = cursor.rowcount
        conn.commit()

        log.info(f"Fixed blank flags for {rows_updated} problems in session {session_id}")

        return {"status": "success", "rows_updated": rows_updated}


@router.get("/{session_id}/rerun-blank-detection")
async def rerun_blank_detection(session_id: int):
    """
    Re-run blank detection for all problems in a session using the current algorithm.
    This is useful for testing improvements to blank detection on existing sessions.

    Uses Server-Sent Events to stream progress updates to the client.

    Args:
        session_id: The session ID to re-analyze

    Returns:
        SSE stream with progress updates and final results
    """
    log = logging.getLogger(__name__)
    log.info(f"Re-running blank detection for session {session_id}")

    from ..services.exam_processor import ExamProcessor
    from fastapi.responses import StreamingResponse
    import asyncio

    async def event_generator():
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Verify session exists
            cursor.execute("SELECT id FROM grading_sessions WHERE id = ?", (session_id,))
            if not cursor.fetchone():
                yield f"data: {json.dumps({'error': 'Session not found'})}\n\n"
                return

            # Get distinct problem numbers
            cursor.execute("""
                SELECT DISTINCT p.problem_number
                FROM problems p
                JOIN submissions s ON p.submission_id = s.id
                WHERE s.session_id = ? AND s.exam_pdf_data IS NOT NULL AND p.region_coords IS NOT NULL
                ORDER BY p.problem_number
            """, (session_id,))

            problem_numbers = [row["problem_number"] for row in cursor.fetchall()]

            if not problem_numbers:
                yield f"data: {json.dumps({'error': 'No problems with PDF data found in this session'})}\n\n"
                return

            # Count total problems for progress tracking
            cursor.execute("""
                SELECT COUNT(*) as total
                FROM problems p
                JOIN submissions s ON p.submission_id = s.id
                WHERE s.session_id = ? AND s.exam_pdf_data IS NOT NULL AND p.region_coords IS NOT NULL
            """, (session_id,))
            total_problems = cursor.fetchone()["total"]

            blank_detected = 0
            not_blank = 0
            errors = 0
            processed_count = 0

            yield f"data: {json.dumps({'type': 'start', 'total': total_problems, 'problem_count': len(problem_numbers)})}\n\n"
            await asyncio.sleep(0)

            exam_processor = ExamProcessor()

            # Process each problem number as a population
            for problem_num in problem_numbers:
                try:
                    # Get all UNGRADED submissions for this problem number
                    # Don't overwrite blank detection on already-graded problems
                    cursor.execute("""
                        SELECT
                            p.id as problem_id,
                            p.region_coords,
                            s.exam_pdf_data,
                            p.graded
                        FROM problems p
                        JOIN submissions s ON p.submission_id = s.id
                        WHERE s.session_id = ? AND p.problem_number = ?
                          AND s.exam_pdf_data IS NOT NULL AND p.region_coords IS NOT NULL
                          AND p.graded = 0
                        ORDER BY s.id
                    """, (session_id, problem_num))

                    problem_submissions = cursor.fetchall()

                    if not problem_submissions:
                        continue

                    # Extract all images for this problem number
                    images_base64 = []
                    problem_ids = []
                    for submission in problem_submissions:
                        problem_ids.append(submission["problem_id"])
                        region_coords = json.loads(submission["region_coords"])
                        pdf_base64 = submission["exam_pdf_data"]

                        start_page = region_coords["page_number"]
                        start_y = region_coords["region_y_start"]
                        end_page = region_coords.get("end_page_number", start_page)
                        end_y = region_coords["region_y_end"]

                        # Extract problem image at standard DPI (150)
                        problem_image_base64, _ = exam_processor._extract_cross_page_region(
                            fitz.open(stream=base64.b64decode(pdf_base64), filetype="pdf"),
                            start_page, start_y,
                            end_page, end_y,
                            dpi=150
                        )
                        images_base64.append(problem_image_base64)

                    # Run population-based blank detection
                    blank_results = exam_processor.is_blank_heuristic_population(
                        images_base64,
                        percentile_threshold=5.0
                    )

                    # Update all problems with their results
                    for problem_id, blank_result in zip(problem_ids, blank_results):
                        if blank_result["is_blank"]:
                            blank_detected += 1
                        else:
                            not_blank += 1

                        cursor.execute("""
                            UPDATE problems
                            SET is_blank = ?,
                                blank_confidence = ?,
                                blank_method = ?,
                                blank_reasoning = ?
                            WHERE id = ?
                        """, (
                            1 if blank_result["is_blank"] else 0,
                            blank_result["confidence"],
                            blank_result["method"],
                            blank_result["reasoning"],
                            problem_id
                        ))

                        processed_count += 1
                        # Send progress update per submission
                        is_blank_val = int(blank_result['is_blank']) == 1  # Convert to Python bool
                        yield f"data: {json.dumps({'type': 'progress', 'current': processed_count, 'total': total_problems, 'is_blank': is_blank_val, 'problem_number': problem_num})}\n\n"
                        await asyncio.sleep(0)

                    log.info(f"Problem {problem_num}: processed {len(problem_submissions)} submissions, "
                            f"threshold={blank_results[0].get('threshold', 'N/A') if blank_results else 'N/A'}")

                except Exception as e:
                    log.error(f"Error processing problem number {problem_num}: {e}")
                    errors += 1
                    yield f"data: {json.dumps({'type': 'error', 'problem_number': problem_num, 'message': str(e)})}\n\n"
                    await asyncio.sleep(0)

            conn.commit()

            log.info(f"Population-based blank detection complete: {blank_detected} blank, {not_blank} not blank, {errors} errors out of {total_problems} problems")

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete', 'total_problems': total_problems, 'blank_detected': blank_detected, 'not_blank': not_blank, 'errors': errors})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
