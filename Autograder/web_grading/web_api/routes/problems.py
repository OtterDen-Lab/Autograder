"""
Problem grading endpoints.
"""
import textwrap
import os

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

from PIL import Image
import io

import json
import logging

log = logging.getLogger(__name__)

router = APIRouter()


def extract_problem_image(pdf_data: str, page_number: int, region_y_start: int,
                         region_y_end: int, end_page_number: int = None, end_region_y: int = None) -> str:
    """
    Extract a problem image from stored PDF data using region coordinates.
    Supports cross-page regions.

    Args:
        pdf_data: Base64 encoded PDF
        page_number: 0-indexed start page number
        region_y_start: Y coordinate of region start on start page
        region_y_end: Y coordinate of region end on start page (or end page if cross-page)
        end_page_number: Optional end page number for cross-page regions
        end_region_y: Optional end y-coordinate for cross-page regions

    Returns:
        Base64 encoded PNG image of the problem region
    """

    # Decode PDF from base64
    pdf_bytes = base64.b64decode(pdf_data)
    pdf_document = fitz.open("pdf", pdf_bytes)

    # Determine if this is a cross-page region
    is_cross_page = (end_page_number is not None and end_page_number != page_number)

    if not is_cross_page:
        # Single page extraction (original logic)
        page = pdf_document[page_number]
        region = fitz.Rect(0, region_y_start, page.rect.width, region_y_end)

        # Validate region is not empty
        if region.is_empty or region.height <= 0:
            img = Image.new('RGB', (int(page.rect.width), 1), color='white')
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            pdf_document.close()
            return img_base64

        # Extract region as new PDF page
        problem_pdf = fitz.open()
        problem_page = problem_pdf.new_page(width=region.width, height=region.height)
        problem_page.show_pdf_page(problem_page.rect, pdf_document, page_number, clip=region)

        # Convert to PNG
        pix = problem_page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")

        problem_pdf.close()
        pdf_document.close()

        return img_base64

    else:
        # Cross-page extraction - merge multiple pages
        page_images = []
        start_page = page_number
        end_page = end_page_number
        start_y = region_y_start
        end_y = end_region_y

        # Extract first page (from start_y to bottom)
        first_page = pdf_document[start_page]
        first_region = fitz.Rect(0, start_y, first_page.rect.width, first_page.rect.height)

        if not first_region.is_empty and first_region.height > 0:
            problem_pdf = fitz.open()
            problem_page = problem_pdf.new_page(width=first_region.width, height=first_region.height)
            problem_page.show_pdf_page(problem_page.rect, pdf_document, start_page, clip=first_region)

            pix = problem_page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes))
            page_images.append(img)
            problem_pdf.close()

        # Extract middle pages (full pages)
        for page_num in range(start_page + 1, end_page):
            page = pdf_document[page_num]
            region = fitz.Rect(0, 0, page.rect.width, page.rect.height)

            problem_pdf = fitz.open()
            problem_page = problem_pdf.new_page(width=region.width, height=region.height)
            problem_page.show_pdf_page(problem_page.rect, pdf_document, page_num, clip=region)

            pix = problem_page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes))
            page_images.append(img)
            problem_pdf.close()

        # Extract last page (from top to end_y)
        last_page = pdf_document[end_page]
        last_region = fitz.Rect(0, 0, last_page.rect.width, end_y)

        if not last_region.is_empty and last_region.height > 0:
            problem_pdf = fitz.open()
            problem_page = problem_pdf.new_page(width=last_region.width, height=last_region.height)
            problem_page.show_pdf_page(problem_page.rect, pdf_document, end_page, clip=last_region)

            pix = problem_page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes))
            page_images.append(img)
            problem_pdf.close()

        # Handle case where we have no images
        if not page_images:
            width = int(pdf_document[start_page].rect.width)
            img = Image.new('RGB', (width, 1), color='white')
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            pdf_document.close()
            return img_base64

        # Merge images vertically
        width = page_images[0].width
        total_height = sum(img.height for img in page_images)

        merged = Image.new('RGB', (width, total_height), color='white')
        current_y = 0
        for img in page_images:
            merged.paste(img, (0, current_y))
            current_y += img.height

        # Convert to base64
        buffer = io.BytesIO()
        merged.save(buffer, format='PNG')
        merged_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        pdf_document.close()

        return merged_base64


def get_problem_image_data(problem_row, cursor) -> str:
    """
    Get image data for a problem, extracting from PDF if needed.

    Args:
        problem_row: Database row for the problem
        cursor: Database cursor (for fetching submission PDF data)

    Returns:
        Base64 encoded PNG image
    """

    # Extract from PDF using region metadata from region_coords JSON
    # Note: image_data column removed in v21, always use PDF-based extraction
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
                    region_data["region_y_end"],
                    region_data.get("end_page_number"),  # Optional: for cross-page regions
                    region_data.get("end_region_y")  # Optional: for cross-page regions
                )
            else:
                log.error(f"Problem {problem_row['id']}: No PDF data found for submission {problem_row['submission_id']}")
        except (json.JSONDecodeError, KeyError) as e:
            log.error(f"Problem {problem_row['id']}: Invalid region_coords data: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Invalid region_coords data: {str(e)}"
            )

    # Fallback: no image data available
    log.error(f"Problem {problem_row['id']}: No image data available. has_region_coords={bool(problem_row['region_coords'])}")
    raise HTTPException(
        status_code=500,
        detail="Problem image data not available (no region_coords or PDF data)"
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
            has_qr_data=bool(row["qr_encrypted_data"]) if "qr_encrypted_data" in row.keys() else False
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
            has_qr_data=bool(row["qr_encrypted_data"]) if "qr_encrypted_data" in row.keys() else False
        )


@router.post("/{problem_id}/grade")
async def grade_problem(problem_id: int, grade: GradeSubmission):
    """Submit a grade for a problem

    Special handling: If score is exactly "-" (dash), mark the problem as blank
    and set score to 0. This allows manual blank detection alongside AI heuristics.
    Feedback can still be provided normally for context.
    """
    # Get session_id first
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Check if score indicates manual blank marking (dash)
        is_manual_blank = isinstance(grade.score, str) and grade.score.strip() == "-"

        if is_manual_blank:
            # Mark as blank with score 0
            cursor.execute("""
                UPDATE problems
                SET score = 0,
                    feedback = ?,
                    graded = 1,
                    graded_at = ?,
                    is_blank = 1,
                    blank_method = 'manual',
                    blank_reasoning = 'Manually marked as blank by grader (dash in score field)'
                WHERE id = ?
            """, (grade.feedback, datetime.now(), problem_id))
        else:
            # Normal grading - convert score to float and save
            try:
                score_value = float(grade.score)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid score value: {grade.score}. Must be a number or '-' for blank."
                )

            cursor.execute("""
                UPDATE problems
                SET score = ?, feedback = ?, graded = 1, graded_at = ?
                WHERE id = ?
            """, (score_value, grade.feedback, datetime.now(), problem_id))

        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Problem not found")

        # Get session_id for stats update
        cursor.execute("SELECT session_id FROM problems WHERE id = ?", (problem_id,))
        row = cursor.fetchone()
        session_id = row["session_id"]

    # Update statistics after connection is closed to avoid database lock
    update_problem_stats(session_id)

    return {"status": "graded", "problem_id": problem_id, "is_blank": is_manual_blank}


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
            has_qr_data=bool(row["qr_encrypted_data"]) if "qr_encrypted_data" in row.keys() else False
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
async def decipher_handwriting(problem_id: int, model: str = "default"):
    """Use AI to transcribe handwritten text from a problem image

    Args:
        problem_id: ID of the problem to transcribe
        model: AI model to use ("default", "ollama", "sonnet", "opus")
               "default" uses the user's global AI provider setting (typically Ollama)
    """
    from ..services.transcription_queue import get_transcription_queue

    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM problems WHERE id = ?", (problem_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Problem not found")

        # Check cache first for Ollama/default requests
        if model in ("default", "ollama"):
            cursor.execute("""
                SELECT transcription, transcription_model FROM problems
                WHERE id = ? AND transcription IS NOT NULL
            """, (problem_id,))
            cached = cursor.fetchone()
            if cached:
                log.info(f"Returning cached transcription for problem {problem_id}")
                return {
                    "problem_id": problem_id,
                    "transcription": cached[0].strip(),
                    "model": cached[1] or "Ollama (cached)"
                }

            # Not cached - bump priority in queue and process immediately
            log.info(f"Cache miss for problem {problem_id}, queueing with high priority")
            queue = get_transcription_queue()
            queue.bump_priority(problem_id, new_priority=0)

        # Get image data (extract from PDF if needed)
        image_base64 = get_problem_image_data(row, cursor)

    # Single comprehensive prompt for all models
    query = textwrap.dedent(
      """
      Please transcribe all handwritten text from this exam answer with maximum accuracy.

      Instructions:
      - Transcribe ONLY handwritten text (ignore printed questions/instructions)
      - Preserve the structure and organization of the answer exactly
      - For unclear text, make your best interpretation and note uncertainty with [possibly: "alternative"]
      - Describe any diagrams, drawings, or mathematical figures in detail within [brackets]
      - Maintain all mathematical notation, equations, and symbols precisely
      - Note any corrections, cross-outs, or marginal notes
      
      Respond with just the transcribed text, being as thorough and accurate as possible.
      """
    )

    try:
        # Select AI provider based on model parameter
        if model == "opus":
            # Use Opus directly via Anthropic client
            ai = ai_helper.AI_Helper__Anthropic()
            response = ai._client.messages.create(
                model="claude-opus-4-20250514",  # Most capable model
                max_tokens=2000,
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
            model_name = "Opus (Premium)"
        elif model == "sonnet":
            # Use Sonnet via standard query_ai
            ai = ai_helper.AI_Helper__Anthropic()
            response, _ = ai.query_ai(query, attachments=[("png", image_base64)])
            transcription = response
            model_name = "Sonnet"
        elif model == "ollama":
            # Use Ollama explicitly
            ai = ai_helper.AI_Helper__Ollama()
            response, _ = ai.query_ai(query, attachments=[("png", image_base64)])
            transcription = response
            model_name = f"Ollama ({os.getenv('OLLAMA_MODEL', 'qwen3-vl:2b')})"
        else:  # "default" or any other value
            # Default to Ollama (user's typical choice for cost-effective processing)
            ai = ai_helper.AI_Helper__Ollama()
            response, _ = ai.query_ai(query, attachments=[("png", image_base64)])
            transcription = response
            model_name = f"Ollama ({os.getenv('OLLAMA_MODEL', 'qwen3-vl:2b')})"

        # Cache Ollama results for future use
        if model in ("default", "ollama"):
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE problems
                    SET transcription = ?, transcription_model = ?, transcription_cached_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (transcription.strip(), model_name, problem_id))
                log.info(f"Cached Ollama transcription for problem {problem_id}")

        return {
            "problem_id": problem_id,
            "transcription": transcription.strip(),
            "model": model_name
        }
    except Exception as e:
        import traceback
        log.error(f"Transcription failed: {traceback.format_exc()}")
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
            SELECT qr_encrypted_data, max_points, problem_number
            FROM problems
            WHERE id = ?
        """, (problem_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Problem not found")

        # Check if QR encrypted data is available
        if not row["qr_encrypted_data"]:
            raise HTTPException(
                status_code=400,
                detail="QR code data not available for this problem"
            )

    # Import QuizGeneration regeneration function
    try:
        from grade_from_qr import regenerate_from_encrypted
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="QuizGeneration module not available. Please install it to use answer regeneration."
        )

    try:
        # Regenerate the answer using encrypted QR data
        result = regenerate_from_encrypted(
            encrypted_data=row["qr_encrypted_data"],
            points=row["max_points"] or 0.0
        )

        # Extract metadata from result (returned by regenerate_from_encrypted)
        question_type = result.get('question_type')
        seed = result.get('seed')
        version = result.get('version')
        config = result.get('config')

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

        # Prepare response
        response = {
            "problem_id": problem_id,
            "problem_number": row["problem_number"],
            "question_type": question_type,
            "seed": seed,
            "version": version,
            "max_points": row["max_points"],
            "answers": answers
        }

        # Include config if available
        if config:
            response['config'] = config

        # Include HTML answer key if available
        if 'answer_key_html' in result:
            response['answer_key_html'] = result['answer_key_html']

        return response

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
