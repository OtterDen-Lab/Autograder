"""
Exam processing service - extracts logic from Assignment__Exam

This service handles:
- PDF processing and splitting
- Student name extraction
- Page shuffling and redaction
"""
from typing import List, Tuple, Optional, Dict
from pathlib import Path
import logging
import os
import random
import base64
import collections
import sys
import fitz  # PyMuPDF
import fuzzywuzzy.fuzz
import numpy as np
import cv2

# Add parent to path for AI helper import
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
import Autograder.ai_helper as ai_helper

# Import QR scanner service
from .qr_scanner import QRScanner

log = logging.getLogger(__name__)

NAME_SIMILARITY_THRESHOLD = 97  # Percentage threshold for fuzzy matching (exact match required)


class ExamProcessor:
  """
    Reusable exam processing logic extracted from Assignment__Exam.
    Can be used by both the web API and the original CLI.
    """

  def __init__(self,
               name_rect: Optional[dict] = None,
               ai_provider: str = "anthropic"):
    """
        Initialize exam processor.

        Args:
            name_rect: Rectangle coordinates for name detection
                      {x, y, width, height} in pixels
            ai_provider: AI provider to use ("anthropic", "openai", or "ollama")
        """
    self.name_rect = name_rect or {
      "x": 350,
      "y": 0,
      "width": 250,
      "height": 150
    }
    self.fitz_name_rect = fitz.Rect([
      self.name_rect["x"],
      self.name_rect["y"],
      self.name_rect["x"] + self.name_rect["width"],
      self.name_rect["y"] + self.name_rect["height"],
    ])
    self.qr_scanner = QRScanner()

    # Select AI provider
    self.ai_provider = ai_provider.lower()
    if self.ai_provider == "anthropic":
      self.ai_helper_class = ai_helper.AI_Helper__Anthropic
    elif self.ai_provider == "openai":
      self.ai_helper_class = ai_helper.AI_Helper__OpenAI
    elif self.ai_provider == "ollama":
      self.ai_helper_class = ai_helper.AI_Helper__Ollama
    else:
      log.warning(
        f"Unknown AI provider '{ai_provider}', defaulting to Anthropic")
      self.ai_helper_class = ai_helper.AI_Helper__Anthropic

  def _report_progress(self, progress_callback: Optional[callable],
                       processed: int, matched: int, message: str):
    """
    Report progress via callback if provided.

    Args:
        progress_callback: Optional callback function
        processed: Number of submissions processed
        matched: Number of submissions matched
        message: Progress message to display
    """
    if progress_callback:
      progress_callback(processed=processed, matched=matched, message=message)

  def _find_suggested_match(
      self, approximate_name: str,
      unmatched_students: List[dict]) -> Tuple[Optional[dict], int]:
    """
    Find best fuzzy match for a name among unmatched students.

    Args:
        approximate_name: The extracted student name
        unmatched_students: List of student dicts with name and user_id

    Returns:
        Tuple of (suggested_match, match_confidence)
        suggested_match is None if no good match found
    """
    if not approximate_name or not unmatched_students:
      return None, 0

    best_score = 0
    best_match = None

    for student in unmatched_students:
      score = fuzzywuzzy.fuzz.ratio(student["name"], approximate_name)
      if score > best_score:
        best_score = score
        best_match = student

    # Return suggestion only if meets threshold
    if best_match and best_score >= NAME_SIMILARITY_THRESHOLD:
      log.info(
        f"  Suggested match: {best_match['name']} ({best_score}%) - requires confirmation"
      )
      return best_match, best_score
    elif best_match:
      log.warning(f"  Weak match suggestion: {best_match['name']} at {best_score}%")
    else:
      log.warning(f"  No match found for: {approximate_name}")

    return None, 0

  def _setup_page_mappings(
      self, input_files: List[Path],
      manual_split_points: Optional[Dict[int, List[int]]]
  ) -> Dict:
    """
    Set up page mappings and split points based on configuration.

    Args:
        input_files: List of PDF file paths
        page_ranges: Optional list of (start, end) page ranges to merge
        manual_split_points: Optional dict mapping page_number -> list of y-positions

    Returns:
        Tuple of (use_auto_detection, page_mappings_by_submission, consensus_break_points)
    """
    log.info("Using manual split points for problem detection")

    # Manual split points are now required
    if manual_split_points is None:
      raise ValueError(
        "Manual split points are required. Please use the alignment interface to specify split points."
      )

    log.info(f"Using manual split points for {len(manual_split_points)} pages")
    consensus_break_points = manual_split_points

    total_consensus_breaks = sum(
      len(breaks) for breaks in consensus_break_points.values())
    log.info(
      f"Using {total_consensus_breaks} manual split points across {len(consensus_break_points)} pages"
    )

    return consensus_break_points

  def _extract_problems(
      self,
      pdf_path: Path,
      consensus_break_points: Optional[dict],
      skip_first_region: bool,
      last_page_blank: bool
  ) -> Tuple[Optional[str], List[Dict]]:
    """
    Extract problems from PDF using either manual split points or page ranges.

    Args:
        pdf_path: Path to PDF file
        page_ranges: Optional list of (start, end) page ranges
        consensus_break_points: Optional dict of manual split points
        problem_max_points: Dict mapping problem_number -> max_points
        detect_blank: Whether to detect blank problems
        blank_confidence_threshold: Threshold for blank detection
        use_ai_for_borderline: Use AI for borderline cases
        extract_max_points_enabled: Extract max points from images
        skip_first_region: Skip first region (header)
        last_page_blank: Skip last page

    Returns:
        Tuple of (pdf_data, problems)
        pdf_data is base64 PDF (None for manual page ranges)
        problems is list of problem dicts
    """
    # Use manual split points to extract problem regions
    pdf_data, problems = self.redact_and_extract_regions(
      pdf_path,
      split_points=consensus_break_points,
      skip_first_region=skip_first_region,
      last_page_blank=last_page_blank)
    return pdf_data, problems

  def _build_submission_dict(
      self, document_id: int, approximate_name: str, name_image: str,
      suggested_match: Optional[dict],
      page_mappings_by_submission: Optional[dict], problems: List[Dict],
      pdf_data: Optional[str], pdf_path: Path,
      file_metadata: Optional[Dict[Path, Dict]]) -> dict:
    """
    Build submission dictionary from extracted data.

    Args:
        document_id: Unique document ID
        approximate_name: Extracted student name
        name_image: Base64 image of name region
        suggested_match: Suggested student match (if any)
        page_mappings_by_submission: Page mappings for shuffled problems
        problems: List of problem dicts
        pdf_data: Base64 encoded PDF (None for manual page ranges)
        pdf_path: Path to original PDF
        file_metadata: Optional metadata about uploaded files

    Returns:
        Submission dictionary
    """
    return {
      "document_id":
      document_id,
      "approximate_name":
      approximate_name,
      "name_image_data":
      name_image,
      "student_name":
      None,  # No auto-matching - requires manual confirmation
      "canvas_user_id":
      None,  # No auto-matching - requires manual confirmation
      "suggested_canvas_user_id":
      suggested_match["user_id"] if suggested_match else None,
      "page_mappings":
      page_mappings_by_submission[document_id]
      if page_mappings_by_submission else [],
      "problems":
      problems,
      "pdf_data":
      pdf_data,  # Base64 PDF (None for manual page ranges)
      "file_hash":
      file_metadata[pdf_path]["hash"]
      if file_metadata and pdf_path in file_metadata else None,
      "original_filename":
      file_metadata[pdf_path]["original_filename"]
      if file_metadata and pdf_path in file_metadata else pdf_path.name
    }

  def process_exams(
      self,
      input_files: List[Path],
      canvas_students: List[dict],
      progress_callback: Optional[callable] = None,
      document_id_offset: int = 0,
      file_metadata: Optional[Dict[Path, Dict]] = None,
      manual_split_points: Optional[Dict[int, List[int]]] = None,
      skip_first_region: bool = True,
      last_page_blank: bool = False
  ) -> Tuple[List[Dict], List[Dict]]:
    """
        Process exam PDFs.

        Args:
            input_files: List of PDF file paths
            canvas_students: List of student dicts with name and user_id
            page_ranges: Optional list of (start, end) page ranges to merge
            use_ai: bool Whether to use AI for name extraction
            detect_blank: Whether to detect blank/unanswered problems
            blank_confidence_threshold: Confidence threshold for using AI verification on blanks
            use_ai_for_borderline: Whether to use AI for low-confidence blank detections
            progress_callback: Optional callback function(processed, matched, message) for progress updates
            document_id_offset: Starting document_id (useful when adding more exams to existing session)
            file_metadata: Optional dict mapping file_path -> {hash, original_filename}
            problem_max_points: Optional dict mapping problem_number -> max_points
            extract_max_points_enabled: Whether to extract max points from images
            manual_split_points: Optional dict mapping page_number -> list of y-positions for manual splits
            skip_first_region: Whether to skip the first region (header/title area) when splitting (default True)
            last_page_blank: Whether to skip the last page (common with odd-numbered page counts, default False)

        Returns:
            Tuple of (matched_submissions, unmatched_submissions)
            Each submission dict contains: document_id, student_name, canvas_user_id,
            page_mappings, problems (list of {problem_number, image_base64, is_blank, blank_confidence}),
            file_hash, original_filename
        """
    log.info(f"Processing {len(input_files)} exams")

    # Early return if no files
    if not input_files:
      return [], []

    # Set up page mappings and split points
    page_mappings_by_submission = None
    consensus_break_points = self._setup_page_mappings(
      input_files,
      manual_split_points
    )

    # Process each PDF
    matched_submissions = []
    unmatched_submissions = []
    unmatched_students = canvas_students.copy()

    for index, pdf_path in enumerate(input_files):
      document_id = index + document_id_offset
      log.info(
        f"Processing exam {index + 1}/{len(input_files)} (document_id={document_id}): {pdf_path.name}"
      )

      # Report progress: starting exam
      self._report_progress(
        progress_callback,
        index,
        len(matched_submissions),
        f"Processing exam {index + 1}/{len(input_files)}: {pdf_path.name}"
      )

      # Extract name
      approximate_name, name_image = self.extract_name(
        pdf_path,
        student_names=[s["name"] for s in unmatched_students])
      log.info(f"  Extracted name: {approximate_name}")

      # Find suggested match
      suggested_match, match_confidence = self._find_suggested_match(approximate_name, unmatched_students)

      # Extract problems from PDF
      pdf_data, problems = self._extract_problems(
        pdf_path,
        consensus_break_points,
        skip_first_region,
        last_page_blank
      )

      # Build submission dict
      submission = self._build_submission_dict(
        document_id,
        approximate_name,
        name_image,
        suggested_match,
        page_mappings_by_submission,
        problems,
        pdf_data,
        pdf_path,
        file_metadata
      )
      
      # If above threshold, add to matched list
      if match_confidence > NAME_SIMILARITY_THRESHOLD:
        matched_submissions.append(submission)
      else:
        unmatched_submissions.append(submission)

    log.info(
      f"Matched: {len(matched_submissions)}, Unmatched: {len(unmatched_submissions)}"
    )
    return matched_submissions, unmatched_submissions

  def extract_name(
      self,
      pdf_path: Path,
      student_names: Optional[List[str]] = None) -> tuple[str, str]:
    """Extract student name from PDF using AI.

        Returns:
            Tuple of (extracted_name, name_image_base64)
        """
    # First extract the name image (always do this)
    name_image_base64 = ""
    try:
      document = fitz.open(str(pdf_path))
      page = document[0]
      pix = page.get_pixmap(clip=list(self.fitz_name_rect))
      image_bytes = pix.tobytes("png")
      name_image_base64 = base64.b64encode(image_bytes).decode("utf-8")
      document.close()
    except Exception as e:
      log.error(f"Failed to extract name image: {e}")
      return "", ""

    # Then try AI name extraction (may fail if AI service unavailable)
    try:
      query = "What name is written in this picture? Please respond with only the name."
      if student_names:
        query += "\n\nPossible names (use as guide):\n - " + "\n - ".join(
          sorted(student_names))

      response, _ = self.ai_helper_class().query_ai(query,
                                                    attachments=[
                                                      ("png",
                                                       name_image_base64)
                                                    ])
      return response.strip(), name_image_base64
    except Exception as e:
      log.warning(
        f"AI name extraction failed (falling back to image only): {e}")
      # Return empty name but still include the image so user can manually match
      return "", name_image_base64

  def redact_and_split(
      self, pdf_path: Path,
      page_ranges: List[Tuple[int, int]]) -> List[fitz.Document]:
    """Redact names and split PDF into problems."""
    pdf_document = fitz.open(str(pdf_path))

    # Redact first page name area
    pdf_document[0].draw_rect(self.fitz_name_rect,
                              color=(0, 0, 0),
                              fill=(0, 0, 0))

    # Split into problems based on page ranges
    problem_pdfs = []
    for start_page, end_page in page_ranges:
      problem_pdf = fitz.open()
      problem_pdf.insert_pdf(pdf_document,
                             from_page=start_page,
                             to_page=end_page)
      problem_pdfs.append(problem_pdf)

    pdf_document.close()
    return problem_pdfs

  def _extract_cross_page_region(self,
                                 pdf_document: fitz.Document,
                                 start_page: int,
                                 start_y: float,
                                 end_page: int,
                                 end_y: float,
                                 dpi: int = 150) -> Tuple[str, int]:
    """
        Extract a region that may span multiple pages and return as merged image.

        Args:
            pdf_document: PyMuPDF document
            start_page: Starting page number (0-indexed)
            start_y: Starting y-position on start page
            end_page: Ending page number (0-indexed)
            end_y: Ending y-position on end page

        Returns:
            Tuple of (base64_image, total_height)
        """
    from PIL import Image
    import io

    page_images = []

    if start_page == end_page:
      # Single page region - simple case
      page = pdf_document[start_page]
      region = fitz.Rect(0, start_y, page.rect.width, end_y)

      # Validate region is not empty
      if region.is_empty or region.height <= 0:
        log.warning(
          f"Empty region on page {start_page}: y={start_y} to y={end_y}")
        # Create a minimal white image
        img = Image.new('RGB', (int(page.rect.width), 1), color='white')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return img_base64, 1

      problem_pdf = fitz.open()
      problem_page = problem_pdf.new_page(width=region.width,
                                          height=region.height)
      problem_page.show_pdf_page(problem_page.rect,
                                 pdf_document,
                                 start_page,
                                 clip=region)

      pix = problem_page.get_pixmap(dpi=dpi)
      img_bytes = pix.tobytes("png")
      img_base64 = base64.b64encode(img_bytes).decode("utf-8")

      problem_pdf.close()

      return img_base64, int(region.height)

    else:
      # Multi-page region - extract each page's portion and merge vertically
      log.info(
        f"Extracting cross-page region from page {start_page} (y={start_y}) to page {end_page} (y={end_y})"
      )

      # Extract first page (from start_y to bottom)
      first_page = pdf_document[start_page]
      first_region = fitz.Rect(0, start_y, first_page.rect.width,
                               first_page.rect.height)

      log.debug(
        f"First page region: height={first_region.height}, is_empty={first_region.is_empty}, page_height={first_page.rect.height}"
      )

      # Skip first page if region is empty (start_y is at page boundary)
      if not first_region.is_empty and first_region.height > 0:
        problem_pdf = fitz.open()
        problem_page = problem_pdf.new_page(width=first_region.width,
                                            height=first_region.height)
        problem_page.show_pdf_page(problem_page.rect,
                                   pdf_document,
                                   start_page,
                                   clip=first_region)

        pix = problem_page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        page_images.append(img)
        problem_pdf.close()
      else:
        log.debug(
          f"Skipping empty region on first page {start_page} (y={start_y} to bottom)"
        )

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

        pix = problem_page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        page_images.append(img)
        problem_pdf.close()

      # Extract last page (from top to end_y)
      last_page = pdf_document[end_page]
      last_region = fitz.Rect(0, 0, last_page.rect.width, end_y)

      # Skip last page if region is empty (end_y is at page top)
      if not last_region.is_empty and last_region.height > 0:
        problem_pdf = fitz.open()
        problem_page = problem_pdf.new_page(width=last_region.width,
                                            height=last_region.height)
        problem_page.show_pdf_page(problem_page.rect,
                                   pdf_document,
                                   end_page,
                                   clip=last_region)

        pix = problem_page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        page_images.append(img)
        problem_pdf.close()
      else:
        log.debug(
          f"Skipping empty region on last page {end_page} (top to y={end_y})")

      # Handle case where we have no images (all regions were empty)
      if not page_images:
        log.warning(
          f"No valid regions extracted from page {start_page} to {end_page}")
        # Create a minimal white image
        width = int(pdf_document[start_page].rect.width)
        img = Image.new('RGB', (width, 1), color='white')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return img_base64, 1

      # Merge images vertically
      log.info(f"Merging {len(page_images)} page regions vertically")

      # Get dimensions (assume all have same width)
      width = page_images[0].width
      total_height = sum(img.height for img in page_images)

      # Create merged image
      merged = Image.new('RGB', (width, total_height), color='white')

      # Paste each image
      current_y = 0
      for img in page_images:
        merged.paste(img, (0, current_y))
        current_y += img.height

      # Convert to base64
      buffer = io.BytesIO()
      merged.save(buffer, format='PNG')
      merged_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

      log.info(f"Merged image size: {width}x{total_height}")

      return merged_base64, total_height

  def redact_and_extract_regions(
      self,
      pdf_path: Path,
      split_points: Dict[int, List[int]],
      skip_first_region: bool = True,
      last_page_blank: bool = False) -> Tuple[str, List[Dict]]:
    """
        Redact names and extract problem regions using manual split points.
        Returns PDF data once and region metadata for each problem.
        Supports cross-page regions by linearizing split points across all pages.

        Args:
            pdf_path: Path to PDF file
            split_points: Dict mapping page_number -> list of y-positions (manual split points from alignment UI)
            detect_blank: Whether to detect blank/unanswered problems
            blank_confidence_threshold: Confidence threshold (0-1) for using AI verification
            use_ai_for_borderline: Whether to use AI for low-confidence detections
            problem_max_points: Shared dict for caching max points by problem number
            extract_max_points_enabled: Whether to extract max points from images
            skip_first_region: Whether to skip the first region (header/title area) on page 0
            last_page_blank: Whether to skip the last page (common with odd-numbered page counts)

        Returns:
            Tuple of (pdf_base64, problems_list)
            - pdf_base64: Base64 encoded redacted PDF
            - problems_list: List of problem dicts with region metadata
        """
    # IMPORTANT: Open PDF TWICE - once for QR scanning (unredacted), once for final output (redacted)
    # This ensures QR codes on the first page aren't covered by the name redaction box
    pdf_document_original = fitz.open(str(pdf_path))
    pdf_document = fitz.open(str(pdf_path))
    total_pages = pdf_document.page_count

    # Pre-scan QR codes on the ORIGINAL unredacted PDF before applying redaction
    # This is crucial because the redaction box may cover QR codes on the first page
    # We need to do this BEFORE redaction but AFTER calculating linear splits
    qr_data_by_problem = {}  # Will map problem_number -> qr_data

    # We'll scan QR codes after creating the linear splits (below)

    # Now redact name area on first page
    if total_pages > 0:
      pdf_document[0].draw_rect(self.fitz_name_rect,
                                color=(0, 0, 0),
                                fill=(0, 0, 0))

    # Save redacted PDF as base64 (once for the entire submission)
    pdf_bytes = pdf_document.tobytes()
    pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

    # Create a linear list of all split points across pages
    # Each split point is (page_num, y_position)
    # Only add splits that were explicitly provided by the user
    # NOTE: split_points now contain percentages (0.0-1.0), not absolute pixels
    linear_splits = []

    for page_num in range(total_pages):
      page = pdf_document[page_num]
      page_height = page.rect.height

      # Get split percentages for this page and convert to absolute coordinates
      line_percentages = split_points.get(page_num, [])
      # Convert from percentage of page height to absolute y-coordinate
      line_positions = [pct * page_height for pct in line_percentages]
      for y_pos in sorted(line_positions):
        # Normalize splits at page boundaries:
        # If a split is at the very bottom of a page (within 1pt tolerance),
        # treat it as being at the top of the next page instead
        if abs(y_pos - page_height) < 1.0 and page_num < total_pages - 1:
          # This is a page boundary split - move it to start of next page
          log.debug(
            f"Normalizing page boundary split: ({page_num}, {y_pos}) -> ({page_num + 1}, 0)"
          )
          linear_splits.append((page_num + 1, 0))
        else:
          linear_splits.append((page_num, y_pos))

    # Sort splits chronologically (by page, then by y-position)
    linear_splits.sort(key=lambda x: (x[0], x[1]))

    # Remove duplicate splits (can happen if user manually added a split at y=0 of next page)
    unique_splits = []
    for split in linear_splits:
      if not unique_splits or split != unique_splits[-1]:
        unique_splits.append(split)
    linear_splits = unique_splits

    # If no splits were provided, use the entire PDF as one problem
    if not linear_splits:
      log.warning("No split points found, treating entire PDF as one problem")
      linear_splits = [(0, 0),
                       (total_pages - 1,
                        pdf_document[total_pages - 1].rect.height)]

    # Add a split at the start if not present (problems start from top of page 0)
    if linear_splits[0] != (0, 0):
      linear_splits.insert(0, (0, 0))
      log.debug("Inserted starting split at (0, 0)")

    # Add final split at end of last page if not present
    last_page = pdf_document[total_pages - 1]
    last_split = (total_pages - 1, last_page.rect.height)
    if linear_splits[-1] != last_split:
      linear_splits.append(last_split)
      log.debug(f"Inserted ending split at {last_split}")

    log.info(
      f"Created linear split list with {len(linear_splits)} splits across {total_pages} pages"
    )
    log.info(f"Linear splits: {linear_splits}")

    # Filter out last page if requested (common with odd-numbered page counts)
    if last_page_blank and total_pages > 0:
      last_page_num = total_pages - 1
      # Remove all splits that reference the last page
      splits_before_filter = len(linear_splits)
      linear_splits = [(page, y) for page, y in linear_splits
                       if page < last_page_num]

      # Ensure we have an ending split at the bottom of the second-to-last page
      if total_pages > 1 and linear_splits:
        second_to_last_page = pdf_document[last_page_num - 1]
        expected_end = (last_page_num - 1, second_to_last_page.rect.height)
        if linear_splits[-1] != expected_end:
          linear_splits.append(expected_end)
          log.debug(
            f"Added ending split at bottom of page {last_page_num - 1}")

      splits_removed = splits_before_filter - len(linear_splits)
      log.info(
        f"Skipping last page (page {last_page_num}) - removed {splits_removed} split(s)"
      )
      log.info(f"Updated linear splits: {linear_splits}")

    # Determine starting index for problem extraction
    # If skip_first_region is True, skip the first split pair (header region)
    start_index = 1 if skip_first_region else 0

    if skip_first_region and len(linear_splits) > 1:
      log.info(
        f"Skipping first region (header/title area): from {linear_splits[0]} to {linear_splits[1]}"
      )

    # NOW scan QR codes from the linearized problem regions (BEFORE redaction)
    # This must happen after linear_splits is calculated but before problems are created
    if self.qr_scanner.available:
      log.info(
        f"Pre-scanning {len(linear_splits) - 1 - start_index} problem regions for QR codes from unredacted PDF..."
      )
      problem_number_prescan = 1

      for i in range(start_index, len(linear_splits) - 1):
        start_page, start_y = linear_splits[i]
        end_page, end_y = linear_splits[i + 1]

        # Adjust end point if needed (same logic as problem extraction)
        if end_y == 0 and end_page > start_page:
          end_page = end_page - 1
          end_y = pdf_document_original[end_page].rect.height

        # Extract region from ORIGINAL unredacted PDF for QR detection
        # Use progressive DPI: start low (fast), increase only if needed
        # Since PDF is vector, higher DPI doesn't lose quality, just takes more time
        qr_data = None
        for dpi in [150, 300, 600, 900]:
          problem_image_base64, _ = self._extract_cross_page_region(
            pdf_document_original,
            start_page,
            start_y,
            end_page,
            end_y,
            dpi=dpi)

          # Try scanning at this resolution
          qr_data = self.qr_scanner.scan_qr_from_image(problem_image_base64)
          if qr_data:
            if dpi > 150:
              log.info(
                f"QR code found at {dpi} DPI (after trying lower resolutions)")
            break  # Found it, no need to try higher DPI
        if qr_data:
          log.info(f"Pre-scan: Problem {problem_number_prescan}: "
                   f"Found QR code with max_points={qr_data['max_points']}")
          qr_data_by_problem[problem_number_prescan] = qr_data
        else:
          log.debug(
            f"Pre-scan: Problem {problem_number_prescan}: No QR code found")

        problem_number_prescan += 1

      log.info(
        f"Pre-scan complete: Found {len(qr_data_by_problem)} QR codes out of {problem_number_prescan - 1} problems"
      )

    # Close original PDF - we're done with it
    pdf_document_original.close()

    # Now create problems from consecutive split pairs
    problems = []
    problem_number = 1

    for i in range(start_index, len(linear_splits) - 1):
      start_page, start_y = linear_splits[i]
      end_page, end_y = linear_splits[i + 1]

      # Special case: if end_y is 0 (top of page), the region actually ends
      # at the bottom of the PREVIOUS page, not at the top of end_page
      if end_y == 0 and end_page > start_page:
        end_page = end_page - 1
        end_y = pdf_document[end_page].rect.height
        log.debug(
          f"Adjusted end point from top of page {end_page + 1} to bottom of page {end_page}"
        )

      log.debug(
        f"Problem {problem_number}: from ({start_page}, {start_y}) to ({end_page}, {end_y})"
      )

      # Extract region(s) and create merged image
      problem_image_base64, region_height = self._extract_cross_page_region(
        pdf_document, start_page, start_y, end_page, end_y)

      # Initialize problem dict with region coordinates
      problem_dict = {
        "problem_number":
        problem_number,
        "page_number":
        start_page,  # Start page for backwards compatibility
        "region_y_start":
        int(start_y),
        "region_y_end":
        int(end_y) if start_page == end_page else int(
          pdf_document[start_page].rect.height),
        "region_height":
        region_height,
        "is_blank":
        False,
        "blank_confidence":
        0.0
      }

      # For cross-page problems, add end page info
      if end_page != start_page:
        problem_dict["end_page_number"] = end_page
        problem_dict["end_region_y"] = int(end_y)
        log.info(
          f"Problem {problem_number} spans multiple pages: {start_page} to {end_page}"
        )

      # Check if we have pre-scanned QR data for this problem
      qr_data = qr_data_by_problem.get(problem_number)

      if qr_data:
        log.info(
          f"Problem {problem_number}: Using pre-scanned QR code data with max_points={qr_data['max_points']}"
        )
        problem_dict["max_points"] = qr_data["max_points"]
        problem_dict["qr_encrypted_data"] = qr_data.get(
          "encrypted_data")  # Store encrypted string for answer regeneration

      problems.append(problem_dict)
      problem_number += 1

    pdf_document.close()


    return pdf_base64, problems

  def is_blank_heuristic_population(self,
                                    images_base64: list,
                                    percentile_threshold: float = 5.0) -> list:
    """
        Population-based blank detection using black pixel ratio clustering.

        Analyzes all submissions for a problem together to find the natural blank baseline.

        Args:
            images_base64: List of base64 encoded images for all submissions of a problem
            percentile_threshold: Percentile cutoff for blank detection (default: 5.0 = bottom 5%)

        Returns:
            List of dicts with {is_blank: bool, confidence: float, black_pixel_ratio: float}
        """
    import io
    from PIL import Image, ImageFilter

    # Step 1: Calculate black pixel ratio for each submission
    black_pixel_ratios = []

    for img_b64 in images_base64:
      try:
        # Decode image
        img_bytes = base64.b64decode(img_b64)
        img = Image.open(io.BytesIO(img_bytes))

        # Convert to grayscale first
        if img.mode != 'L':
          img = img.convert('L')

        # Normalize histogram to handle varying scan brightness
        # This stretches the grayscale values to use the full 0-255 range
        from PIL import ImageOps
        img_normalized = ImageOps.autocontrast(img, cutoff=2)

        # Now convert to B/W with fixed threshold
        # Since histogram is normalized, threshold of 128 is consistent
        img_bw = img_normalized.convert("1").filter(
          ImageFilter.MedianFilter(3))

        # Convert to numpy array
        img_array = np.array(img_bw)

        # Count black pixels (value = 0 in binary image)
        black_pixels = np.sum(img_array == 0)
        total_pixels = img_array.size
        black_ratio = black_pixels / total_pixels if total_pixels > 0 else 0

        black_pixel_ratios.append(black_ratio)

      except Exception as e:
        log.warning(f"Error processing image for blank detection: {e}")
        black_pixel_ratios.append(0.0)  # Default to 0 on error

    # Step 2: Find threshold by identifying the left-edge cluster
    # Create histogram to find where the blank cluster ends
    num_bins = 20
    hist_counts, bin_edges = np.histogram(black_pixel_ratios, bins=num_bins)

    log.info(
      f"black_pixel_ratios histogram: counts={hist_counts}, edges={bin_edges}")

    # Find the first significant drop after we've seen at least 3% of submissions
    # Strategy: Look for first drop of 2+ bins or hit 0 after minimum threshold
    threshold_found = False
    threshold = None

    min_submissions_pct = 0.03  # At least 3% of submissions
    min_submissions = max(1,
                          int(len(black_pixel_ratios) * min_submissions_pct))
    cumulative_count = 0
    seen_minimum = False

    for i in range(len(hist_counts) - 1):  # -1 because we check i+1
      cumulative_count += hist_counts[i]

      # Check if we've seen at least the minimum number of submissions
      if cumulative_count >= min_submissions:
        seen_minimum = True

      # After seeing minimum, look for first significant drop or zero
      if seen_minimum:
        current_count = hist_counts[i]
        next_count = hist_counts[i + 1]

        # Significant drop: decrease of 2+ or hitting zero
        if next_count == 0 or (current_count - next_count >= 2):
          # Threshold is the edge after this bin
          threshold = bin_edges[i + 1]
          threshold_found = True
          log.info(f"Found cluster boundary at bin {i+1}: "
                   f"drop from {current_count} to {next_count}, "
                   f"cumulative={cumulative_count}, threshold={threshold:.4f}")
          break

    # Fallback to percentile if no clear drop-off found
    if not threshold_found:
      threshold = np.percentile(black_pixel_ratios, percentile_threshold)
      log.info(
        f"No clear cluster boundary found, using {percentile_threshold}th percentile: "
        f"threshold={threshold:.4f}")

    # Step 3: Classify each submission
    results = []
    num_blank = 0
    for i, ratio in enumerate(black_pixel_ratios):
      is_blank = ratio <= threshold
      if is_blank:
        num_blank += 1

      # Confidence based on distance from threshold
      # Further from threshold = higher confidence
      distance_from_threshold = abs(ratio - threshold)
      max_distance = max(abs(max(black_pixel_ratios) - threshold),
                         abs(min(black_pixel_ratios) - threshold))
      confidence = min(1.0, distance_from_threshold /
                       max_distance) if max_distance > 0 else 0.5

      results.append({
        "is_blank":
        is_blank,
        "confidence":
        confidence,
        "black_pixel_ratio":
        ratio,
        "threshold":
        threshold,
        "method":
        "population-gap",
        "reasoning":
        f"Black ratio: {ratio:.4f}, Threshold (gap): {threshold:.4f}"
      })

      log.debug(
        f"Submission {i}: black_ratio={ratio:.4f}, threshold={threshold:.4f}, is_blank={is_blank}, confidence={confidence:.2f}"
      )

    pct_blank = (num_blank / len(black_pixel_ratios) *
                 100) if len(black_pixel_ratios) > 0 else 0
    log.info(
      f"Detected {num_blank}/{len(black_pixel_ratios)} ({pct_blank:.1f}%) as blank"
    )

    return results

  def is_blank_heuristic(self,
                         image_base64: str,
                         num_bands: int = 20,
                         blank_threshold: float = 0.8,
                         clustering_method: str = "auto",
                         **kwargs) -> Dict:
    """
        Use band-based heuristics to determine if a problem image appears blank/unanswered.

        This method divides the image into horizontal bands and analyzes the darkness
        of each band to distinguish between:
        - Printed question text (consistently dark bands at top)
        - Handwritten answers (medium darkness in middle/bottom)
        - Blank answer areas (light bands in middle/bottom)

        Args:
            image_base64: Base64 encoded image
            num_bands: Number of horizontal bands to divide image into (default: 20)
            blank_threshold: Fraction of answer bands that must be blank (default: 0.8 = 80%)
            clustering_method: "2-group", "3-group", or "auto" (default: auto tries both)

        Returns:
            Dict with {is_blank: bool, confidence: float, band_count: int, ...}
        """
    import io
    from PIL import Image
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score, davies_bouldin_score

    # Decode image
    img_bytes = base64.b64decode(image_base64)
    img = Image.open(io.BytesIO(img_bytes))

    # Convert to grayscale
    if img.mode != 'L':
      img = img.convert('L')

    # Apply minimal margins to avoid edge artifacts only
    width, height = img.size
    margin = 10  # Minimal margin
    img = img.crop((margin, margin, width - margin, height - margin))

    # Convert to numpy array
    img_array = np.array(img)
    height, width = img_array.shape

    # Step 1: Divide into horizontal bands and analyze each
    band_height = height // num_bands
    band_stats = []

    for i in range(num_bands):
      start_y = i * band_height
      end_y = start_y + band_height if i < num_bands - 1 else height
      band = img_array[start_y:end_y, :]

      # Calculate statistics for this band
      median_darkness = np.median(band)
      max_darkness = 255 - np.max(band)  # Invert: higher = darker
      min_darkness = 255 - np.min(band)
      darkness_range = max_darkness - (255 - median_darkness)

      band_stats.append({
        'index': i,
        'median': median_darkness,
        'max_darkness': max_darkness,
        'darkness_range': darkness_range
      })

    # Step 2: Cluster bands by maximum darkness
    max_darkness_values = np.array([b['max_darkness']
                                    for b in band_stats]).reshape(-1, 1)

    best_clustering = None
    best_score = -np.inf
    best_n_clusters = 2

    # Try both 2 and 3 group clustering if auto mode
    n_clusters_to_try = [2, 3] if clustering_method == "auto" else [
      int(clustering_method.split('-')[0])
    ]

    for n_clusters in n_clusters_to_try:
      if len(max_darkness_values) < n_clusters:
        continue

      try:
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(max_darkness_values)

        # Evaluate clustering quality using silhouette score (higher is better)
        # Need at least 2 distinct clusters for silhouette score
        num_distinct_clusters = len(set(labels))
        if num_distinct_clusters > 1:
          score = silhouette_score(max_darkness_values, labels)
          log.debug(
            f"{n_clusters}-group clustering: silhouette score = {score:.3f}, distinct clusters = {num_distinct_clusters}"
          )

          if score > best_score:
            best_score = score
            best_clustering = labels
            best_n_clusters = n_clusters
        elif best_clustering is None:
          # If we haven't found any valid clustering yet, use this one even with only 1 cluster
          log.debug(
            f"{n_clusters}-group clustering: only {num_distinct_clusters} distinct cluster(s) found, using as fallback"
          )
          best_clustering = labels
          best_n_clusters = num_distinct_clusters
      except Exception as e:
        log.warning(f"Clustering with {n_clusters} groups failed: {e}")
        continue

    # Handle case where clustering completely failed
    if best_clustering is None:
      log.warning(
        "All clustering attempts failed, treating all bands as single group (assuming blank)"
      )
      best_clustering = np.zeros(len(band_stats), dtype=int)
      best_n_clusters = 1

    # Assign cluster labels to bands
    for i, label in enumerate(best_clustering):
      band_stats[i]['cluster'] = label

    # Log band clustering for debugging
    log.debug(f"Band clustering results ({best_n_clusters} clusters):")
    for i, band in enumerate(band_stats):
      log.debug(
        f"  Band {i}: max_darkness={band['max_darkness']:.1f}, cluster={band['cluster']}"
      )

    # Step 3: Identify question vs answer bands
    # Question bands are in the darkest cluster and typically at the top
    cluster_darkness = {}
    for cluster_id in range(best_n_clusters):
      cluster_bands = [b for b in band_stats if b['cluster'] == cluster_id]
      if cluster_bands:
        avg_darkness = np.mean([b['max_darkness'] for b in cluster_bands])
        cluster_darkness[cluster_id] = avg_darkness

    # Darkest cluster = question text
    question_cluster = max(cluster_darkness.keys(),
                           key=lambda k: cluster_darkness[k])

    # Find where question area ends (last band in question cluster)
    question_bands_indices = [
      b['index'] for b in band_stats if b['cluster'] == question_cluster
    ]
    if question_bands_indices:
      question_end = max(question_bands_indices)
    else:
      question_end = 0

    # Answer bands are everything after the question area
    answer_bands = [b for b in band_stats if b['index'] > question_end]

    # Step 4: Classify answer bands as handwritten or blank
    if not answer_bands:
      # No answer area detected - consider blank
      log.debug("No answer bands detected - marking as blank")
      return {
        "is_blank": True,
        "confidence": 0.5,
        "band_count": num_bands,
        "question_bands": len(question_bands_indices),
        "answer_bands": 0,
        "blank_bands": 0,
        "handwritten_bands": 0,
        "cluster_method": f"{best_n_clusters}-group"
      }

    # Determine blank vs handwritten in answer area
    if best_n_clusters == 3:
      # With 3 clusters, we can potentially identify: dark (question), medium (handwriting), light (blank)
      sorted_clusters = sorted(cluster_darkness.keys(),
                               key=lambda k: cluster_darkness[k],
                               reverse=True)
      medium_cluster = sorted_clusters[1] if len(sorted_clusters) > 1 else None
      light_cluster = sorted_clusters[2] if len(
        sorted_clusters) > 2 else sorted_clusters[-1]

      blank_bands = [b for b in answer_bands if b['cluster'] == light_cluster]
      handwritten_bands = [
        b for b in answer_bands if b['cluster'] == medium_cluster
      ] if medium_cluster else []
    else:
      # With 2 clusters, non-question bands need further analysis
      # Use intra-band analysis for borderline cases
      blank_bands = []
      handwritten_bands = []

      for band in answer_bands:
        # Apply intra-band analysis
        is_blank_band = self._analyze_band_for_handwriting(
          img_array, band['index'], band_height, height)
        if is_blank_band:
          blank_bands.append(band)
        else:
          handwritten_bands.append(band)

    # Step 5: Final decision
    blank_ratio = len(blank_bands) / len(answer_bands) if answer_bands else 1.0
    is_blank = blank_ratio >= blank_threshold

    # Confidence based on how clear the distinction is
    confidence = abs(blank_ratio -
                     0.5) * 2  # 0.5 = uncertain, 0 or 1 = certain
    confidence = max(0.0, min(1.0, confidence))

    log.info(
      f"Band-based blank detection: is_blank={is_blank}, "
      f"blank_ratio={blank_ratio:.2f}, confidence={confidence:.2f}, "
      f"question_bands={len(question_bands_indices)}, answer_bands={len(answer_bands)}, "
      f"blank_bands={len(blank_bands)}, handwritten_bands={len(handwritten_bands)}, "
      f"cluster_method={best_n_clusters}-group, question_end={question_end}")

    return {
      "is_blank": is_blank,
      "confidence": confidence,
      "band_count": num_bands,
      "question_bands": len(question_bands_indices),
      "answer_bands": len(answer_bands),
      "blank_bands": len(blank_bands),
      "handwritten_bands": len(handwritten_bands),
      "cluster_method": f"{best_n_clusters}-group"
    }

  def _analyze_band_for_handwriting(self, img_array: np.ndarray,
                                    band_index: int, band_height: int,
                                    total_height: int) -> bool:
    """
        Analyze a single band for handwriting by looking at spatial variation.

        Args:
            img_array: Full image as numpy array
            band_index: Index of the band to analyze
            band_height: Height of each band in pixels
            total_height: Total image height

        Returns:
            True if band appears blank, False if it has handwriting
        """
    start_y = band_index * band_height
    end_y = min(start_y + band_height, total_height)
    band = img_array[start_y:end_y, :]

    # Divide band into horizontal sub-regions
    num_segments = 10
    band_width = band.shape[1]
    segment_width = band_width // num_segments

    segment_max_darkness = []
    for i in range(num_segments):
      start_x = i * segment_width
      end_x = start_x + segment_width if i < num_segments - 1 else band_width
      segment = band[:, start_x:end_x]

      # Maximum darkness in this segment
      max_dark = 255 - np.min(segment)  # Invert: higher = darker
      segment_max_darkness.append(max_dark)

    # If segments show high variation in darkness, it's likely handwriting
    # Blank areas have uniform (low) darkness across segments
    darkness_variance = np.var(segment_max_darkness)
    mean_darkness = np.mean(segment_max_darkness)

    # Thresholds: low variance + low mean = blank
    is_blank = (darkness_variance < 100 and mean_darkness < 50)

    return is_blank
