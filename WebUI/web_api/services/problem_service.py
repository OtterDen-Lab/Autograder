"""
Service for problem-specific business logic.
Handles operations on individual problems like image extraction.
"""
import base64
import io
import json
import logging
from pathlib import Path
from typing import Tuple, Optional, Dict
import fitz  # PyMuPDF
from PIL import Image

from ..repositories import ProblemRepository, SubmissionRepository

log = logging.getLogger(__name__)


class ProblemService:
  """Business logic for problem operations"""

  def __init__(self):
    self.problem_repo = ProblemRepository()
    self.submission_repo = SubmissionRepository()

  def get_problem_image(self, problem_id: int, dpi: int = 150) -> str:
    """
    Extract and return the image for a specific problem.

    Args:
        problem_id: The problem ID
        dpi: Resolution for image extraction (default 150)

    Returns:
        Base64 encoded PNG image
    """
    problem = self.problem_repo.get_by_id(problem_id)
    if not problem:
      raise ValueError(f"Problem {problem_id} not found")

    submission = self.submission_repo.get_by_id(problem.submission_id)
    if not submission:
      raise ValueError(
        f"Submission {problem.submission_id} not found for problem {problem_id}"
      )

    if not submission.exam_pdf_data:
      raise ValueError(
        f"No PDF data for submission {submission.id} (problem {problem_id})")

    # Parse region coordinates
    region_coords = json.loads(problem.region_coords)

    # Extract image using coordinates
    return self.extract_image_from_pdf_data(
      pdf_base64=submission.exam_pdf_data,
      page_number=region_coords["page_number"],
      region_y_start=region_coords["region_y_start"],
      region_y_end=region_coords["region_y_end"],
      end_page_number=region_coords.get("end_page_number"),
      end_region_y=region_coords.get("end_region_y"),
      dpi=dpi)

  def extract_image_from_pdf_data(
      self,
      pdf_base64: str,
      page_number: int,
      region_y_start: float,
      region_y_end: float,
      end_page_number: Optional[int] = None,
      end_region_y: Optional[float] = None,
      dpi: int = 150) -> str:
    """
    Extract a region from PDF data as an image.
    Supports both single-page and cross-page regions.

    Args:
        pdf_base64: Base64 encoded PDF data
        page_number: Starting page number (0-indexed)
        region_y_start: Y coordinate of region start
        region_y_end: Y coordinate of region end (on start page if single-page)
        end_page_number: Optional end page number for cross-page regions
        end_region_y: Optional end Y coordinate for cross-page regions
        dpi: Resolution for image extraction (default 150)

    Returns:
        Base64 encoded PNG image
    """
    # Decode PDF from base64
    pdf_bytes = base64.b64decode(pdf_base64)
    pdf_document = fitz.open("pdf", pdf_bytes)

    try:
      # Determine actual end page and Y
      actual_end_page = end_page_number if end_page_number is not None else page_number
      actual_end_y = end_region_y if end_region_y is not None else region_y_end

      # Extract using the opened document
      image_base64, _ = self.extract_image_from_document(
        pdf_document=pdf_document,
        start_page=page_number,
        start_y=region_y_start,
        end_page=actual_end_page,
        end_y=actual_end_y,
        dpi=dpi)

      return image_base64
    finally:
      pdf_document.close()

  def extract_image_from_document(
      self,
      pdf_document: fitz.Document,
      start_page: int,
      start_y: float,
      end_page: int,
      end_y: float,
      dpi: int = 150) -> Tuple[str, int]:
    """
    Extract a region from an already-opened PDF document.
    Supports both single-page and cross-page regions.

    This is the canonical implementation of image extraction.

    Args:
        pdf_document: Opened PyMuPDF document
        start_page: Starting page number (0-indexed)
        start_y: Starting Y coordinate
        end_page: Ending page number (0-indexed)
        end_y: Ending Y coordinate
        dpi: Resolution for image extraction (default 150)

    Returns:
        Tuple of (base64_image, total_height_pixels)
    """
    if start_page == end_page:
      # Single page region
      return self._extract_single_page_region(pdf_document, start_page,
                                               start_y, end_y, dpi)
    else:
      # Cross-page region
      return self._extract_cross_page_region(pdf_document, start_page, start_y,
                                              end_page, end_y, dpi)

  def _extract_single_page_region(self, pdf_document: fitz.Document,
                                   page_number: int, start_y: float,
                                   end_y: float,
                                   dpi: int) -> Tuple[str, int]:
    """Extract a region from a single page"""
    page = pdf_document[page_number]
    region = fitz.Rect(0, start_y, page.rect.width, end_y)

    # Validate region is not empty
    if region.is_empty or region.height <= 0:
      log.warning(
        f"Empty region on page {page_number}: y={start_y} to y={end_y}")
      # Create a minimal white image
      img = Image.new('RGB', (int(page.rect.width), 1), color='white')
      buffer = io.BytesIO()
      img.save(buffer, format='PNG')
      img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
      return img_base64, 1

    # Extract region as new PDF page
    problem_pdf = fitz.open()
    problem_page = problem_pdf.new_page(width=region.width,
                                        height=region.height)
    problem_page.show_pdf_page(problem_page.rect, pdf_document, page_number,
                               clip=region)

    # Convert to PNG
    pix = problem_page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("png")
    img_base64 = base64.b64encode(img_bytes).decode("utf-8")

    problem_pdf.close()

    return img_base64, int(region.height)

  def _extract_cross_page_region(self, pdf_document: fitz.Document,
                                  start_page: int, start_y: float,
                                  end_page: int, end_y: float,
                                  dpi: int) -> Tuple[str, int]:
    """Extract a region that spans multiple pages and merge vertically"""
    log.info(
      f"Extracting cross-page region from page {start_page} (y={start_y}) to page {end_page} (y={end_y})"
    )

    page_images = []
    total_height = 0

    # Extract first page (from start_y to bottom)
    first_page = pdf_document[start_page]
    first_region = fitz.Rect(0, start_y, first_page.rect.width,
                             first_page.rect.height)

    # Skip first page if region is empty (start_y is at page boundary)
    if not first_region.is_empty and first_region.height > 0:
      problem_pdf = fitz.open()
      problem_page = problem_pdf.new_page(width=first_region.width,
                                          height=first_region.height)
      problem_page.show_pdf_page(problem_page.rect, pdf_document, start_page,
                                 clip=first_region)

      pix = problem_page.get_pixmap(dpi=dpi)
      img = Image.open(io.BytesIO(pix.tobytes("png")))
      page_images.append(img)
      total_height += img.height
      problem_pdf.close()

    # Extract middle pages (full pages)
    for page_num in range(start_page + 1, end_page):
      middle_page = pdf_document[page_num]
      middle_region = fitz.Rect(0, 0, middle_page.rect.width,
                                middle_page.rect.height)

      problem_pdf = fitz.open()
      problem_page = problem_pdf.new_page(width=middle_region.width,
                                          height=middle_region.height)
      problem_page.show_pdf_page(problem_page.rect, pdf_document, page_num,
                                 clip=middle_region)

      pix = problem_page.get_pixmap(dpi=dpi)
      img = Image.open(io.BytesIO(pix.tobytes("png")))
      page_images.append(img)
      total_height += img.height
      problem_pdf.close()

    # Extract last page (from top to end_y)
    last_page = pdf_document[end_page]
    last_region = fitz.Rect(0, 0, last_page.rect.width, end_y)

    if not last_region.is_empty and last_region.height > 0:
      problem_pdf = fitz.open()
      problem_page = problem_pdf.new_page(width=last_region.width,
                                          height=last_region.height)
      problem_page.show_pdf_page(problem_page.rect, pdf_document, end_page,
                                 clip=last_region)

      pix = problem_page.get_pixmap(dpi=dpi)
      img = Image.open(io.BytesIO(pix.tobytes("png")))
      page_images.append(img)
      total_height += img.height
      problem_pdf.close()

    # Merge all page images vertically
    if not page_images:
      # No valid regions found - create minimal image
      img = Image.new('RGB', (int(first_page.rect.width), 1), color='white')
      buffer = io.BytesIO()
      img.save(buffer, format='PNG')
      return base64.b64encode(buffer.getvalue()).decode('utf-8'), 1

    # Get width from first image (all should be same width)
    merged_width = page_images[0].width

    # Create merged image
    merged_image = Image.new('RGB', (merged_width, total_height))

    # Paste each page image
    current_y = 0
    for img in page_images:
      merged_image.paste(img, (0, current_y))
      current_y += img.height

    # Convert to base64
    buffer = io.BytesIO()
    merged_image.save(buffer, format='PNG')
    img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    return img_base64, total_height
