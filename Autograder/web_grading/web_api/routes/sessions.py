"""
Session management endpoints.
"""
from fastapi import APIRouter, HTTPException, Response, UploadFile, File
from fastapi.responses import StreamingResponse
from typing import List
import json
import io
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

            # Get total count (including ungraded)
            cursor.execute("""
                SELECT COUNT(*) as num_total,
                       SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as num_graded
                FROM problems
                WHERE session_id = ? AND problem_number = ?
            """, (session_id, problem_num))
            count_row = cursor.fetchone()
            num_total = count_row["num_total"]
            num_graded = count_row["num_graded"]

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
    try:
        use_prod = bool(row["use_prod_canvas"] if row["use_prod_canvas"] is not None else 0)
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

        # Build export structure
        export_data = {
            "export_version": 1,
            "exported_at": datetime.now().isoformat(),
            "session": session_data,
            "submissions": submissions,
            "problem_stats": problem_stats
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
                     canvas_user_id, page_mappings, total_score, graded_at, file_hash, original_filename)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    submission.get("original_filename")
                ))

                new_submission_id = cursor.lastrowid
                submission_id_map[old_submission_id] = new_submission_id

                # Import problems for this submission
                for problem in submission.get("problems", []):
                    cursor.execute("""
                        INSERT INTO problems
                        (session_id, submission_id, problem_number, image_data, score, feedback,
                         graded, graded_at, is_blank, blank_confidence, blank_method, blank_reasoning, max_points)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        new_session_id,
                        new_submission_id,
                        problem["problem_number"],
                        problem["image_data"],
                        problem.get("score"),
                        problem.get("feedback"),
                        problem.get("graded", 0),
                        problem.get("graded_at"),
                        problem.get("is_blank", 0),
                        problem.get("blank_confidence", 0.0),
                        problem.get("blank_method"),
                        problem.get("blank_reasoning"),
                        problem.get("max_points")
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

            log.info(f"Imported {len(submissions)} submissions and {sum(len(s.get('problems', [])) for s in submissions)} problems")

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
