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
from ..repositories import ProblemRepository, SubmissionRepository

# Add parent to path for AI helper import
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
import Autograder.ai_helper as ai_helper

from PIL import Image
import io

import json
import logging

log = logging.getLogger(__name__)

router = APIRouter()


def extract_problem_image(pdf_data: str,
                          page_number: int,
                          region_y_start: int,
                          region_y_end: int,
                          end_page_number: int = None,
                          end_region_y: int = None) -> str:
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
  is_cross_page = (end_page_number is not None
                   and end_page_number != page_number)

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
    problem_page = problem_pdf.new_page(width=region.width,
                                        height=region.height)
    problem_page.show_pdf_page(problem_page.rect,
                               pdf_document,
                               page_number,
                               clip=region)

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
    first_region = fitz.Rect(0, start_y, first_page.rect.width,
                             first_page.rect.height)

    if not first_region.is_empty and first_region.height > 0:
      problem_pdf = fitz.open()
      problem_page = problem_pdf.new_page(width=first_region.width,
                                          height=first_region.height)
      problem_page.show_pdf_page(problem_page.rect,
                                 pdf_document,
                                 start_page,
                                 clip=first_region)

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
      problem_page = problem_pdf.new_page(width=region.width,
                                          height=region.height)
      problem_page.show_pdf_page(problem_page.rect,
                                 pdf_document,
                                 page_num,
                                 clip=region)

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
      problem_page = problem_pdf.new_page(width=last_region.width,
                                          height=last_region.height)
      problem_page.show_pdf_page(problem_page.rect,
                                 pdf_document,
                                 end_page,
                                 clip=last_region)

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


def get_problem_image_data(problem, submission_repo: SubmissionRepository = None) -> str:
  """
    Get image data for a problem, extracting from PDF if needed.

    Args:
        problem: Problem domain object or dict-like with region_coords, submission_id, id
        submission_repo: Optional SubmissionRepository (creates new if None)

    Returns:
        Base64 encoded PNG image
    """
  # Handle both Problem objects and dict-like rows
  problem_id = problem.id if hasattr(problem, 'id') else problem["id"]
  submission_id = problem.submission_id if hasattr(problem, 'submission_id') else problem["submission_id"]
  region_coords = problem.region_coords if hasattr(problem, 'region_coords') else (
    json.loads(problem["region_coords"]) if problem.get("region_coords") else None
  )

  # Extract from PDF using region metadata from region_coords
  # Note: image_data column removed in v21, always use PDF-based extraction
  if region_coords:
    try:
      # Get PDF data from submission
      if submission_repo is None:
        submission_repo = SubmissionRepository()

      pdf_data = submission_repo.get_pdf_data(submission_id)

      if pdf_data:
        return extract_problem_image(
          pdf_data,
          region_coords["page_number"],
          region_coords["region_y_start"],
          region_coords["region_y_end"],
          region_coords.get("end_page_number"),  # Optional: for cross-page regions
          region_coords.get("end_region_y")  # Optional: for cross-page regions
        )
      else:
        log.error(
          f"Problem {problem_id}: No PDF data found for submission {submission_id}"
        )
    except (json.JSONDecodeError, KeyError) as e:
      log.error(
        f"Problem {problem_id}: Invalid region_coords data: {str(e)}")
      raise HTTPException(status_code=500,
                          detail=f"Invalid region_coords data: {str(e)}")

  # Fallback: no image data available
  log.error(
    f"Problem {problem_id}: No image data available. has_region_coords={bool(region_coords)}"
  )
  raise HTTPException(
    status_code=500,
    detail="Problem image data not available (no region_coords or PDF data)")


@router.get("/{session_id}/{problem_number}/next",
            response_model=ProblemResponse)
async def get_next_problem(session_id: int, problem_number: int):
  """Get next ungraded problem for a specific problem number"""
  problem_repo = ProblemRepository()
  submission_repo = SubmissionRepository()

  # Get next ungraded problem (non-blank first, then blank)
  problem = problem_repo.get_next_ungraded(session_id, problem_number)
  if not problem:
    raise HTTPException(
      status_code=404,
      detail=f"No ungraded problems found for problem {problem_number}")

  # Get counts for context (including blank counts)
  counts = problem_repo.get_counts_for_problem_number(session_id, problem_number)
  total_count = counts["total"]
  graded_count = counts["graded"]
  ungraded_blank = counts["ungraded_blank"]
  ungraded_nonblank = counts["ungraded_nonblank"]
  current_index = graded_count + 1

  # Get image data (extract from PDF if needed)
  image_data = get_problem_image_data(problem, submission_repo)

  return ProblemResponse(
    id=problem.id,
    problem_number=problem.problem_number,
    submission_id=problem.submission_id,
    image_data=image_data,
    score=problem.score,
    feedback=problem.feedback,
    graded=problem.graded,
    max_points=problem.max_points,
    current_index=current_index,
    total_count=total_count,
    ungraded_blank=ungraded_blank,
    ungraded_nonblank=ungraded_nonblank,
    is_blank=problem.is_blank,
    blank_confidence=problem.blank_confidence,
    blank_method=problem.blank_method,
    blank_reasoning=problem.blank_reasoning,
    ai_reasoning=problem.ai_reasoning,
    has_qr_data=bool(problem.qr_encrypted_data)
  )


@router.get("/{session_id}/{problem_number}/previous",
            response_model=ProblemResponse)
async def get_previous_problem(session_id: int, problem_number: int):
  """Get most recently graded problem for a specific problem number"""
  problem_repo = ProblemRepository()
  submission_repo = SubmissionRepository()

  # Get most recently graded problem
  problem = problem_repo.get_previous_graded(session_id, problem_number)
  if not problem:
    raise HTTPException(
      status_code=404,
      detail=f"No graded problems found for problem {problem_number}")

  # Get counts for context (including blank counts)
  counts = problem_repo.get_counts_for_problem_number(session_id, problem_number)
  total_count = counts["total"]
  graded_count = counts["graded"]
  ungraded_blank = counts["ungraded_blank"]
  ungraded_nonblank = counts["ungraded_nonblank"]
  current_index = graded_count

  # Get image data (extract from PDF if needed)
  image_data = get_problem_image_data(problem, submission_repo)

  return ProblemResponse(
    id=problem.id,
    problem_number=problem.problem_number,
    submission_id=problem.submission_id,
    image_data=image_data,
    score=problem.score,
    feedback=problem.feedback,
    graded=problem.graded,
    max_points=problem.max_points,
    current_index=current_index,
    total_count=total_count,
    ungraded_blank=ungraded_blank,
    ungraded_nonblank=ungraded_nonblank,
    is_blank=problem.is_blank,
    blank_confidence=problem.blank_confidence,
    blank_method=problem.blank_method,
    blank_reasoning=problem.blank_reasoning,
    ai_reasoning=problem.ai_reasoning,
    has_qr_data=bool(problem.qr_encrypted_data)
  )


@router.post("/{problem_id}/grade")
async def grade_problem(problem_id: int, grade: GradeSubmission):
  """Submit a grade for a problem

    Special handling: If score is exactly "-" (dash), mark the problem as blank
    and set score to 0. This allows manual blank detection alongside AI heuristics.
    Feedback can still be provided normally for context.
    """
  problem_repo = ProblemRepository()

  # Check if score indicates manual blank marking (dash)
  is_manual_blank = isinstance(grade.score, str) and grade.score.strip() == "-"

  if is_manual_blank:
    # Mark as blank with score 0
    problem_repo.mark_as_blank(problem_id, grade.feedback)
  else:
    # Normal grading - convert score to float and save
    try:
      score_value = float(grade.score)
    except (ValueError, TypeError):
      raise HTTPException(
        status_code=400,
        detail=f"Invalid score value: {grade.score}. Must be a number or '-' for blank."
      )

    problem_repo.update_grade(problem_id, score_value, grade.feedback)

  # Get session_id for stats update
  problem = problem_repo.get_by_id(problem_id)
  if not problem:
    raise HTTPException(status_code=404, detail="Problem not found")

  # Update statistics after grading
  update_problem_stats(problem.session_id)

  return {
    "status": "graded",
    "problem_id": problem_id,
    "is_blank": is_manual_blank
  }


@router.get("/{problem_id}", response_model=ProblemResponse)
async def get_problem(problem_id: int):
  """Get a specific problem by ID"""
  problem_repo = ProblemRepository()
  submission_repo = SubmissionRepository()

  problem = problem_repo.get_by_id(problem_id)
  if not problem:
    raise HTTPException(status_code=404, detail="Problem not found")

  # Get context counts
  counts = problem_repo.get_counts_for_problem_number(problem.session_id, problem.problem_number)

  # Get image data (extract from PDF if needed)
  image_data = get_problem_image_data(problem, submission_repo)

  return ProblemResponse(
    id=problem.id,
    problem_number=problem.problem_number,
    submission_id=problem.submission_id,
    image_data=image_data,
    score=problem.score,
    feedback=problem.feedback,
    graded=problem.graded,
    current_index=counts["graded"] + 1,
    total_count=counts["total"],
    is_blank=problem.is_blank,
    blank_confidence=problem.blank_confidence,
    blank_method=problem.blank_method,
    blank_reasoning=problem.blank_reasoning,
    ai_reasoning=problem.ai_reasoning,
    has_qr_data=bool(problem.qr_encrypted_data)
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
  problem_repo = ProblemRepository()
  submission_repo = SubmissionRepository()

  # Get problem with region metadata
  problem = problem_repo.get_by_id(problem_id)
  if not problem:
    raise HTTPException(status_code=404, detail="Problem not found")

  # Check if PDF-based storage is available
  if not problem.region_coords:
    raise HTTPException(
      status_code=400,
      detail="Context view not available (problem uses legacy image storage)"
    )

  # Get PDF data from submission
  pdf_data = submission_repo.get_pdf_data(problem.submission_id)
  if not pdf_data:
    raise HTTPException(status_code=500,
                        detail="PDF data not found for submission")

  # Extract full page as image
  pdf_bytes = base64.b64decode(pdf_data)
  pdf_document = fitz.open("pdf", pdf_bytes)
  page = pdf_document[problem.region_coords["page_number"]]

  # Convert full page to PNG
  pix = page.get_pixmap(dpi=150)
  img_bytes = pix.tobytes("png")
  page_image_base64 = base64.b64encode(img_bytes).decode("utf-8")

  pdf_document.close()

  return {
    "problem_id": problem_id,
    "page_image": page_image_base64,
    "problem_region": {
      "y_start": problem.region_coords["region_y_start"],
      "y_end": problem.region_coords["region_y_end"],
      "height": problem.region_coords.get("region_height")
    },
    "page_number": problem.region_coords["page_number"]
  }


@router.post("/{problem_id}/decipher")
async def decipher_handwriting(problem_id: int, model: str = "default"):
  """Use AI to transcribe handwritten text from a problem image

    Args:
        problem_id: ID of the problem to transcribe
        model: AI model to use ("default", "ollama", "sonnet", "opus")
               "default" uses Ollama (cheapest, may have quality issues)
    """
  problem_repo = ProblemRepository()
  submission_repo = SubmissionRepository()

  problem = problem_repo.get_by_id(problem_id)
  if not problem:
    raise HTTPException(status_code=404, detail="Problem not found")

  # Get image data (extract from PDF if needed)
  image_base64 = get_problem_image_data(problem, submission_repo)

  # Simple, direct prompt to avoid editorializing or commentary
  query = "Transcribe all handwritten text from this image. Output only the transcribed text."

  try:
    # Select AI provider based on model parameter
    if model == "opus":
      # Use Opus directly via Anthropic client
      ai = ai_helper.AI_Helper__Anthropic()
      response = ai._client.messages.create(
        model="claude-opus-4-20250514",  # Most capable model
        max_tokens=2000,
        messages=[{
          "role":
          "user",
          "content": [{
            "type": "text",
            "text": query
          }, {
            "type": "image",
            "source": {
              "type": "base64",
              "media_type": "image/png",
              "data": image_base64
            }
          }]
        }])
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
      # Default to Ollama (cheapest, may have quality issues)
      ai = ai_helper.AI_Helper__Ollama()
      response, _ = ai.query_ai(query, attachments=[("png", image_base64)])
      transcription = response
      model_name = f"Ollama ({os.getenv('OLLAMA_MODEL', 'qwen3-vl:2b')})"

    # Validate transcription is not empty
    if not transcription or not transcription.strip():
      error_msg = f"Model returned empty transcription. Try a different model (Sonnet or Opus)."
      log.warning(
        f"Empty transcription from {model_name} for problem {problem_id}")
      raise HTTPException(status_code=500, detail=error_msg)

    # Cache Ollama results for future use (to avoid repeated slow requests)
    if model == "ollama":
      problem_repo.update_transcription(problem_id, transcription.strip(), model_name)
      log.info(f"Cached Ollama transcription for problem {problem_id}")

    return {
      "problem_id": problem_id,
      "transcription": transcription.strip(),
      "model": model_name
    }
  except Exception as e:
    import traceback
    log.error(f"Transcription failed: {traceback.format_exc()}")
    raise HTTPException(status_code=500,
                        detail=f"Transcription failed: {str(e)}")


@router.get("/{session_id}/{problem_number}/graded")
async def get_graded_problems(session_id: int,
                              problem_number: int,
                              offset: int = 0,
                              limit: int = 20):
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
  problem_repo = ProblemRepository()

  problems_data, total_count = problem_repo.get_graded_with_student_names(
    session_id, problem_number, limit, offset
  )

  if total_count == 0:
    return {"problems": [], "total": 0, "offset": offset, "limit": limit}

  # Format for response
  problems = []
  for row in problems_data:
    problems.append({
      "id": row["id"],
      "problem_number": row["problem_number"],
      "submission_id": row["submission_id"],
      "student_name": row.get("student_name"),
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
  problem_repo = ProblemRepository()

  problem = problem_repo.get_by_id(problem_id)
  if not problem:
    raise HTTPException(status_code=404, detail="Problem not found")

  # Check if QR encrypted data is available
  if not problem.qr_encrypted_data:
    raise HTTPException(status_code=400,
                        detail="QR code data not available for this problem")

  # Import QuizGeneration regeneration function
  try:
    from grade_from_qr import regenerate_from_encrypted
  except ImportError:
    raise HTTPException(
      status_code=500,
      detail=
      "QuizGeneration module not available. Please install it to use answer regeneration."
    )

  try:
    # Regenerate the answer using encrypted QR data
    result = regenerate_from_encrypted(encrypted_data=problem.qr_encrypted_data,
                                       points=problem.max_points or 0.0)

    # Extract metadata from result (returned by regenerate_from_encrypted)
    question_type = result.get('question_type')
    seed = result.get('seed')
    version = result.get('version')
    config = result.get('config')

    # Format answers for display
    answers = []
    for key, answer_obj in result['answer_objects'].items():
      answer_dict = {"key": key, "value": str(answer_obj.value)}

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

    # Include explanation markdown if available
    if 'explanation_markdown' in result:
      response['explanation_markdown'] = result['explanation_markdown']

    return response

  except ValueError as e:
    raise HTTPException(status_code=500,
                        detail=f"Failed to regenerate answer: {str(e)}")
  except Exception as e:
    raise HTTPException(
      status_code=500,
      detail=f"Unexpected error during answer regeneration: {str(e)}")


@router.post("/{problem_id}/rescan-qr")
async def rescan_qr_for_single_problem(problem_id: int, dpi: int = 600):
  """
    Re-scan QR code for a specific problem instance at a specified DPI.
    This is useful when the initial scan fails to detect the QR code.

    Args:
        problem_id: The specific problem ID to re-scan
        dpi: DPI to use for rendering (default 600, higher = better for complex QR codes)

    Returns:
        Statistics about QR code found and updated
    """
  # Import required modules
  from ..services.qr_scanner import QRScanner
  from ..services.exam_processor import ExamProcessor

  log.info(f"Re-scanning QR code for problem ID {problem_id} at {dpi} DPI")

  # Initialize QR scanner
  qr_scanner = QRScanner()
  if not qr_scanner.available:
    raise HTTPException(
      status_code=400,
      detail="QR scanner not available (opencv-python or pyzbar not installed)"
    )

  from ..repositories import with_transaction, ProblemMetadataRepository

  # Get problem and submission data
  problem_repo = ProblemRepository()
  submission_repo = SubmissionRepository()

  problem = problem_repo.get_by_id(problem_id)
  if not problem:
    raise HTTPException(status_code=404, detail=f"Problem {problem_id} not found")

  if not problem.region_coords:
    raise HTTPException(status_code=400,
                        detail="Problem has no region coordinates")

  # Get PDF data
  pdf_base64 = submission_repo.get_pdf_data(problem.submission_id)
  if not pdf_base64:
    raise HTTPException(status_code=404, detail="No PDF data found for submission")

  # Decode PDF
  pdf_bytes = base64.b64decode(pdf_base64)
  pdf_document = fitz.open("pdf", pdf_bytes)

  # Parse region coordinates
  start_page = problem.region_coords["page_number"]
  start_y = problem.region_coords["region_y_start"]
  end_page = problem.region_coords.get("end_page_number", start_page)
  end_y = problem.region_coords["region_y_end"]

  # Use ExamProcessor to extract the region at higher DPI
  exam_processor = ExamProcessor()
  problem_image_base64, _ = exam_processor._extract_cross_page_region(
    pdf_document, start_page, start_y, end_page, end_y, dpi=dpi)

  # Scan for QR code
  qr_data = qr_scanner.scan_qr_from_image(problem_image_base64)

  pdf_document.close()

  if qr_data:
    log.info(
      f"Problem {problem.problem_number} (ID {problem_id}): Found QR code with max_points={qr_data['max_points']}"
    )

    # Update problem and metadata in transaction
    with with_transaction() as repos:
      # Update problem with QR data
      repos.problems.update_qr_data(problem_id, qr_data["max_points"], qr_data.get("encrypted_data"))

      # Also update problem_metadata for this session
      repos.metadata.upsert_max_points(problem.session_id, problem.problem_number, qr_data["max_points"])

    log.info(
      f"QR re-scan complete for problem ID {problem_id}: QR code found and updated"
    )

    return {
      "status": "success",
      "problem_id": problem_id,
      "problem_number": problem.problem_number,
      "qr_found": True,
      "max_points": qr_data["max_points"],
      "dpi_used": dpi,
      "message": f"Successfully found and updated QR code for Problem {problem.problem_number} (max points: {qr_data['max_points']}) at {dpi} DPI."
    }
  else:
    log.warning(
      f"Problem {problem.problem_number} (ID {problem_id}): No QR code found at {dpi} DPI"
    )

    return {
      "status": "success",
      "problem_id": problem_id,
      "problem_number": problem.problem_number,
      "qr_found": False,
      "dpi_used": dpi,
      "message": f"No QR code found for Problem {problem.problem_number} at {dpi} DPI. Try increasing DPI or check if QR code is present."
    }
