"""
Problem grading endpoints.
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime
from typing import Optional
import sys
from pathlib import Path
import base64
import fitz  # PyMuPDF

from ..models import ProblemResponse, GradeSubmission
from ..database import get_db_connection, update_problem_stats

# Add parent to path for AI helper import
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
import Autograder.ai_helper as ai_helper

router = APIRouter()


def extract_problem_image(pdf_data: str, page_number: int, region_y_start: int,
                         region_y_end: int) -> str:
    """
    Extract a problem image from stored PDF data using region coordinates.

    Args:
        pdf_data: Base64 encoded PDF
        page_number: 0-indexed page number
        region_y_start: Y coordinate of region start
        region_y_end: Y coordinate of region end

    Returns:
        Base64 encoded PNG image of the problem region
    """
    # Decode PDF from base64
    pdf_bytes = base64.b64decode(pdf_data)
    pdf_document = fitz.open("pdf", pdf_bytes)

    # Get the page
    page = pdf_document[page_number]

    # Create region rectangle
    region = fitz.Rect(0, region_y_start, page.rect.width, region_y_end)

    # Extract region as new PDF page
    problem_pdf = fitz.open()
    problem_page = problem_pdf.new_page(width=region.width, height=region.height)
    problem_page.show_pdf_page(problem_page.rect, pdf_document, page_number, clip=region)

    # Convert to PNG
    pix = problem_page.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")
    img_base64 = base64.b64encode(img_bytes).decode("utf-8")

    # Cleanup
    problem_pdf.close()
    pdf_document.close()

    return img_base64


def get_problem_image_data(problem_row, cursor) -> str:
    """
    Get image data for a problem, extracting from PDF if needed.

    Args:
        problem_row: Database row for the problem
        cursor: Database cursor (for fetching submission PDF data)

    Returns:
        Base64 encoded PNG image
    """
    import json

    # If image_data is stored, return it directly
    if problem_row["image_data"]:
        return problem_row["image_data"]

    # Otherwise, extract from PDF using region metadata from region_coords JSON
    if problem_row["region_coords"]:
        try:
            region_data = json.loads(problem_row["region_coords"])

            # Get PDF data from submission (column is exam_pdf_data)
            cursor.execute(
                "SELECT exam_pdf_data FROM submissions WHERE id = ?",
                (problem_row["submission_id"],)
            )
            submission_row = cursor.fetchone()

            if submission_row and submission_row["exam_pdf_data"]:
                return extract_problem_image(
                    submission_row["exam_pdf_data"],
                    region_data["page_number"],
                    region_data["region_y_start"],
                    region_data["region_y_end"]
                )
        except (json.JSONDecodeError, KeyError) as e:
            raise HTTPException(
                status_code=500,
                detail=f"Invalid region_coords data: {str(e)}"
            )

    # Fallback: no image data available
    raise HTTPException(
        status_code=500,
        detail="Problem image data not available (no stored image or PDF data)"
    )


@router.get("/{session_id}/{problem_number}/next", response_model=ProblemResponse)
async def get_next_problem(session_id: int, problem_number: int):
    """Get next ungraded problem for a specific problem number"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get next ungraded problem (non-blank first, then blank)
        cursor.execute("""
            SELECT * FROM problems
            WHERE session_id = ? AND problem_number = ? AND graded = 0
            ORDER BY is_blank ASC, RANDOM()
            LIMIT 1
        """, (session_id, problem_number))

        row = cursor.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No ungraded problems found for problem {problem_number}"
            )

        # Get counts for context (including blank counts)
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as graded,
                SUM(CASE WHEN graded = 0 AND is_blank = 1 THEN 1 ELSE 0 END) as ungraded_blank,
                SUM(CASE WHEN graded = 0 AND is_blank = 0 THEN 1 ELSE 0 END) as ungraded_nonblank
            FROM problems
            WHERE session_id = ? AND problem_number = ?
        """, (session_id, problem_number))

        count_row = cursor.fetchone()
        total_count = count_row["total"]
        graded_count = count_row["graded"]
        ungraded_blank = count_row["ungraded_blank"]
        ungraded_nonblank = count_row["ungraded_nonblank"]
        current_index = graded_count + 1

        # Get image data (extract from PDF if needed)
        image_data = get_problem_image_data(row, cursor)

        return ProblemResponse(
            id=row["id"],
            problem_number=row["problem_number"],
            submission_id=row["submission_id"],
            image_data=image_data,
            score=row["score"],
            feedback=row["feedback"],
            graded=bool(row["graded"]),
            max_points=row["max_points"],
            current_index=current_index,
            total_count=total_count,
            ungraded_blank=ungraded_blank,
            ungraded_nonblank=ungraded_nonblank,
            is_blank=bool(row["is_blank"]),
            blank_confidence=row["blank_confidence"] or 0.0,
            blank_method=row["blank_method"],
            blank_reasoning=row["blank_reasoning"],
            ai_reasoning=row["ai_reasoning"],
            qr_question_type=row["qr_question_type"],
            qr_seed=row["qr_seed"],
            qr_version=row["qr_version"]
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

        # Get counts for context (including blank counts)
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN graded = 1 THEN 1 ELSE 0 END) as graded,
                SUM(CASE WHEN graded = 0 AND is_blank = 1 THEN 1 ELSE 0 END) as ungraded_blank,
                SUM(CASE WHEN graded = 0 AND is_blank = 0 THEN 1 ELSE 0 END) as ungraded_nonblank
            FROM problems
            WHERE session_id = ? AND problem_number = ?
        """, (session_id, problem_number))

        count_row = cursor.fetchone()
        total_count = count_row["total"]
        graded_count = count_row["graded"]
        ungraded_blank = count_row["ungraded_blank"]
        ungraded_nonblank = count_row["ungraded_nonblank"]
        current_index = graded_count

        # Get image data (extract from PDF if needed)
        image_data = get_problem_image_data(row, cursor)

        return ProblemResponse(
            id=row["id"],
            problem_number=row["problem_number"],
            submission_id=row["submission_id"],
            image_data=image_data,
            score=row["score"],
            feedback=row["feedback"],
            graded=bool(row["graded"]),
            max_points=row["max_points"],
            current_index=current_index,
            total_count=total_count,
            ungraded_blank=ungraded_blank,
            ungraded_nonblank=ungraded_nonblank,
            is_blank=bool(row["is_blank"]),
            blank_confidence=row["blank_confidence"] or 0.0,
            blank_method=row["blank_method"],
            blank_reasoning=row["blank_reasoning"],
            ai_reasoning=row["ai_reasoning"],
            qr_question_type=row["qr_question_type"],
            qr_seed=row["qr_seed"],
            qr_version=row["qr_version"]
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

        # Get image data (extract from PDF if needed)
        image_data = get_problem_image_data(row, cursor)

        return ProblemResponse(
            id=row["id"],
            problem_number=row["problem_number"],
            submission_id=row["submission_id"],
            image_data=image_data,
            score=row["score"],
            feedback=row["feedback"],
            graded=bool(row["graded"]),
            current_index=count_row["graded"] + 1,
            total_count=count_row["total"],
            is_blank=bool(row["is_blank"]),
            blank_confidence=row["blank_confidence"] or 0.0,
            blank_method=row["blank_method"],
            blank_reasoning=row["blank_reasoning"],
            ai_reasoning=row["ai_reasoning"],
            qr_question_type=row["qr_question_type"],
            qr_seed=row["qr_seed"],
            qr_version=row["qr_version"]
        )


@router.get("/{problem_id}/context")
async def get_problem_in_context(problem_id: int):
    """
    Get the full page containing this problem, with the problem region highlighted.

    Returns:
        JSON with:
        - page_image: Base64 PNG of full page
        - problem_region: Coordinates {y_start, y_end, height} for highlighting
    """
    import json

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get problem with region metadata
        cursor.execute("SELECT * FROM problems WHERE id = ?", (problem_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Problem not found")

        # Check if PDF-based storage is available (parse region_coords JSON)
        if not row["region_coords"]:
            raise HTTPException(
                status_code=400,
                detail="Context view not available (problem uses legacy image storage)"
            )

        try:
            region_data = json.loads(row["region_coords"])
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=500,
                detail="Invalid region_coords data"
            )

        # Get PDF data from submission (column is exam_pdf_data)
        cursor.execute(
            "SELECT exam_pdf_data FROM submissions WHERE id = ?",
            (row["submission_id"],)
        )
        submission_row = cursor.fetchone()

        if not submission_row or not submission_row["exam_pdf_data"]:
            raise HTTPException(
                status_code=500,
                detail="PDF data not found for submission"
            )

        # Extract full page as image
        pdf_bytes = base64.b64decode(submission_row["exam_pdf_data"])
        pdf_document = fitz.open("pdf", pdf_bytes)
        page = pdf_document[region_data["page_number"]]

        # Convert full page to PNG
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        page_image_base64 = base64.b64encode(img_bytes).decode("utf-8")

        pdf_document.close()

        return {
            "problem_id": problem_id,
            "page_image": page_image_base64,
            "problem_region": {
                "y_start": region_data["region_y_start"],
                "y_end": region_data["region_y_end"],
                "height": region_data.get("region_height")
            },
            "page_number": region_data["page_number"]
        }


@router.post("/{problem_id}/decipher")
async def decipher_handwriting(problem_id: int, use_premium_model: bool = False):
    """Use AI to transcribe handwritten text from a problem image

    Args:
        problem_id: ID of the problem to transcribe
        use_premium_model: If True, use a more capable (and expensive) model
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM problems WHERE id = ?", (problem_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Problem not found")

        # Get image data (extract from PDF if needed)
        image_base64 = get_problem_image_data(row, cursor)

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


@router.get("/{session_id}/{problem_number}/graded")
async def get_graded_problems(session_id: int, problem_number: int, offset: int = 0, limit: int = 20):
    """
    Get graded problems for a specific problem number for review.

    Args:
        session_id: Grading session ID
        problem_number: Problem number to fetch
        offset: Pagination offset (default 0)
        limit: Max number of problems to return (default 20)

    Returns:
        List of graded problems with metadata
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Get total count
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM problems
            WHERE session_id = ? AND problem_number = ? AND graded = 1
        """, (session_id, problem_number))

        total_count = cursor.fetchone()["count"]

        if total_count == 0:
            return {
                "problems": [],
                "total": 0,
                "offset": offset,
                "limit": limit
            }

        # Get graded problems, ordered by graded_at
        cursor.execute("""
            SELECT p.*, s.student_name
            FROM problems p
            LEFT JOIN submissions s ON p.submission_id = s.id
            WHERE p.session_id = ? AND p.problem_number = ? AND p.graded = 1
            ORDER BY p.graded_at DESC
            LIMIT ? OFFSET ?
        """, (session_id, problem_number, limit, offset))

        rows = cursor.fetchall()

        problems = []
        for row in rows:
            problems.append({
                "id": row["id"],
                "problem_number": row["problem_number"],
                "submission_id": row["submission_id"],
                "student_name": row["student_name"],
                "score": row["score"],
                "feedback": row["feedback"],
                "max_points": row["max_points"],
                "graded_at": row["graded_at"],
                "is_blank": bool(row["is_blank"])
            })

        return {
            "problems": problems,
            "total": total_count,
            "offset": offset,
            "limit": limit
        }


@router.get("/{problem_id}/regenerate-answer")
async def regenerate_answer(problem_id: int):
    """
    Regenerate the correct answer from QR code metadata.

    This endpoint uses the question_type, seed, and version stored from
    the QR code to regenerate the original correct answer.

    Args:
        problem_id: ID of the problem

    Returns:
        JSON with regenerated answers or error if QR metadata not available
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT qr_question_type, qr_seed, qr_version, max_points, problem_number
            FROM problems
            WHERE id = ?
        """, (problem_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Problem not found")

        # Check if QR metadata is available
        if not row["qr_question_type"] or row["qr_seed"] is None:
            raise HTTPException(
                status_code=400,
                detail="QR code metadata not available for this problem"
            )

    # Import QuizGeneration regeneration function
    try:
        from grade_from_qr import regenerate_from_metadata
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="QuizGeneration module not available. Please install it to use answer regeneration."
        )

    try:
        # Regenerate the answer using QR metadata
        result = regenerate_from_metadata(
            question_type=row["qr_question_type"],
            seed=row["qr_seed"],
            version=row["qr_version"],
            points=row["max_points"] or 0.0
        )

        # Format answers for display
        answers = []
        for key, answer_obj in result['answer_objects'].items():
            answer_dict = {
                "key": key,
                "value": str(answer_obj.value)
            }

            # Include tolerance for numerical answers
            if hasattr(answer_obj, 'tolerance') and answer_obj.tolerance is not None:
                answer_dict['tolerance'] = answer_obj.tolerance

            answers.append(answer_dict)

        return {
            "problem_id": problem_id,
            "problem_number": row["problem_number"],
            "question_type": row["qr_question_type"],
            "seed": row["qr_seed"],
            "version": row["qr_version"],
            "max_points": row["max_points"],
            "answers": answers
        }

    except ValueError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to regenerate answer: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error during answer regeneration: {str(e)}"
        )
