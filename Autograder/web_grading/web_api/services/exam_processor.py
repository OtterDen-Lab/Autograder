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

    def __init__(self, name_rect: Optional[dict] = None):
        """
        Initialize exam processor.

        Args:
            name_rect: Rectangle coordinates for name detection
                      {x, y, width, height} in pixels
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

    def process_exams(
        self,
        input_files: List[Path],
        canvas_students: List[dict],
        page_ranges: Optional[List[Tuple[int, int]]] = None,
        use_ai: bool = True,
        detect_blank: bool = False,
        blank_confidence_threshold: float = 0.8,
        use_ai_for_borderline: bool = False,
        progress_callback: Optional[callable] = None,
        document_id_offset: int = 0,
        file_metadata: Optional[Dict[Path, Dict]] = None,
        problem_max_points: Optional[Dict[int, float]] = None,
        extract_max_points_enabled: bool = False,
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

        # Shuffle PDFs
        random.shuffle(input_files)

        # Determine page ranges from first PDF
        if not input_files:
            return [], []

        first_pdf = fitz.open(str(input_files[0]))
        num_pages = first_pdf.page_count
        first_pdf.close()

        # Handle page ranges and shuffling
        use_auto_detection = (page_ranges is None)

        if not use_auto_detection:
            log.info(f"Using manual page ranges: {page_ranges}")

            # Create shuffled page mappings
            num_submissions = len(input_files)
            num_problems = len(page_ranges)
            page_mappings_by_submission = collections.defaultdict(list)

            for problem_num in range(num_problems):
                shuffled_order = random.sample(range(num_submissions), k=num_submissions)
                for submission_id, random_id in enumerate(shuffled_order):
                    page_mappings_by_submission[submission_id].append(random_id)
        else:
            log.info("Using manual split points for problem detection")
            # No shuffling for manual split detection (all students get same order)
            page_mappings_by_submission = None

            # Manual split points are now required
            if manual_split_points is None:
                raise ValueError("Manual split points are required. Please use the alignment interface to specify split points.")

            log.info(f"Using manual split points for {len(manual_split_points)} pages")
            consensus_break_points = manual_split_points

            total_consensus_breaks = sum(len(breaks) for breaks in consensus_break_points.values())
            log.info(f"Using {total_consensus_breaks} manual split points across {len(consensus_break_points)} pages")

        # Process each PDF
        matched_submissions = []
        unmatched_submissions = []
        unmatched_students = canvas_students.copy()

        for index, pdf_path in enumerate(input_files):
            document_id = index + document_id_offset
            log.info(f"Processing exam {index + 1}/{len(input_files)} (document_id={document_id}): {pdf_path.name}")

            # Report progress: starting exam
            if progress_callback:
                progress_callback(
                    processed=index,
                    matched=len(matched_submissions),
                    message=f"Processing exam {index + 1}/{len(input_files)}: {pdf_path.name}"
                )

            # Extract name
            approximate_name, name_image = self.extract_name(
                pdf_path,
                use_ai=use_ai,
                student_names=[s["name"] for s in unmatched_students]
            )
            log.info(f"  Extracted name: {approximate_name}")

            # Report progress: extracted name
            if progress_callback:
                progress_callback(
                    processed=index,
                    matched=len(matched_submissions),
                    message=f"Processing exam {index + 1}/{len(input_files)}: Extracted name: {approximate_name}"
                )

            # Try to match to Canvas student
            matched_student = None
            if approximate_name and unmatched_students:
                best_score = 0
                best_match = None

                for student in unmatched_students:
                    score = fuzzywuzzy.fuzz.ratio(student["name"], approximate_name)
                    if score > best_score:
                        best_score = score
                        best_match = student

                if best_score >= NAME_SIMILARITY_THRESHOLD:
                    matched_student = best_match
                    unmatched_students.remove(best_match)
                    log.info(f"  Matched to: {matched_student['name']} ({best_score}%)")
                else:
                    log.warning(f"  No good match found (best: {best_match['name']} at {best_score}%)")

            # Report progress: matched student
            if progress_callback:
                match_msg = f"Matched to: {matched_student['name']}" if matched_student else "No match found"
                progress_callback(
                    processed=index,
                    matched=len(matched_submissions) + (1 if matched_student else 0),
                    message=f"Processing exam {index + 1}/{len(input_files)}: {match_msg}"
                )

            # Report progress: splitting into problems
            if progress_callback:
                progress_callback(
                    processed=index,
                    matched=len(matched_submissions) + (1 if matched_student else 0),
                    message=f"Processing exam {index + 1}/{len(input_files)}: Splitting into problems..."
                )

            # Redact and split into problems (use auto-detection if no page_ranges specified)
            if page_ranges is None:
                # Initialize problem_max_points dict if not provided (shared across all exams)
                if problem_max_points is None:
                    problem_max_points = {}

                # Use manual split points to extract problem regions
                # Returns (pdf_base64, problems_list) where problems contain region metadata
                pdf_data, problems = self.redact_and_extract_regions(
                    pdf_path,
                    split_points=consensus_break_points,
                    detect_blank=detect_blank,
                    blank_confidence_threshold=blank_confidence_threshold,
                    use_ai_for_borderline=use_ai_for_borderline,
                    problem_max_points=problem_max_points,
                    extract_max_points_enabled=extract_max_points_enabled,
                    skip_first_region=skip_first_region,
                    last_page_blank=last_page_blank
                )
            else:
                # Use manual page ranges (old path - still stores individual PNGs for backwards compatibility)
                pdf_data = None  # For backwards compatibility with manual page ranges
                problem_images = self.redact_and_split(pdf_path, page_ranges)

                # Convert problem images to base64
                problems = []
                for problem_num, problem_doc in enumerate(problem_images):
                    # Convert PDF page to PNG
                    page = problem_doc[0]  # First (and only) page
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    img_base64 = base64.b64encode(img_bytes).decode("utf-8")

                    problems.append({
                        "problem_number": problem_num + 1,
                        "image_base64": img_base64
                    })

                    problem_doc.close()

            # Create submission dict
            submission = {
                "document_id": document_id,
                "approximate_name": approximate_name,
                "name_image_data": name_image,
                "student_name": matched_student["name"] if matched_student else None,
                "canvas_user_id": matched_student["user_id"] if matched_student else None,
                "page_mappings": page_mappings_by_submission[document_id] if page_mappings_by_submission else [],
                "problems": problems,
                "pdf_data": pdf_data,  # Base64 PDF (None for manual page ranges)
                "file_hash": file_metadata[pdf_path]["hash"] if file_metadata and pdf_path in file_metadata else None,
                "original_filename": file_metadata[pdf_path]["original_filename"] if file_metadata and pdf_path in file_metadata else pdf_path.name
            }

            if matched_student:
                matched_submissions.append(submission)
            else:
                unmatched_submissions.append(submission)

            # Report progress: completed exam
            if progress_callback:
                progress_callback(
                    processed=index + 1,
                    matched=len(matched_submissions),
                    message=f"Completed exam {index + 1}/{len(input_files)} ({len(matched_submissions)} matched, {len(unmatched_submissions)} need matching)"
                )

        log.info(f"Matched: {len(matched_submissions)}, Unmatched: {len(unmatched_submissions)}")
        return matched_submissions, unmatched_submissions

    def extract_name(
        self,
        pdf_path: Path,
        use_ai: bool = True,
        student_names: Optional[List[str]] = None
    ) -> tuple[str, str]:
        """Extract student name from PDF using AI.

        Returns:
            Tuple of (extracted_name, name_image_base64)
        """
        if not use_ai:
            return "", ""

        try:
            document = fitz.open(str(pdf_path))
            page = document[0]
            pix = page.get_pixmap(clip=list(self.fitz_name_rect))
            image_bytes = pix.tobytes("png")
            base64_str = base64.b64encode(image_bytes).decode("utf-8")
            document.close()

            query = "What name is written in this picture? Please respond with only the name."
            if student_names:
                query += "\n\nPossible names (use as guide):\n - " + "\n - ".join(sorted(student_names))

            response, _ = ai_helper.AI_Helper__Anthropic().query_ai(query, attachments=[("png", base64_str)])
            return response.strip(), base64_str
        except Exception as e:
            log.error(f"Name extraction failed: {e}")
            return "", ""

    def redact_and_split(
        self,
        pdf_path: Path,
        page_ranges: List[Tuple[int, int]]
    ) -> List[fitz.Document]:
        """Redact names and split PDF into problems."""
        pdf_document = fitz.open(str(pdf_path))

        # Redact first page name area
        pdf_document[0].draw_rect(self.fitz_name_rect, color=(0, 0, 0), fill=(0, 0, 0))

        # Split into problems based on page ranges
        problem_pdfs = []
        for start_page, end_page in page_ranges:
            problem_pdf = fitz.open()
            problem_pdf.insert_pdf(pdf_document, from_page=start_page, to_page=end_page)
            problem_pdfs.append(problem_pdf)

        pdf_document.close()
        return problem_pdfs

    def _extract_cross_page_region(
        self,
        pdf_document: fitz.Document,
        start_page: int,
        start_y: float,
        end_page: int,
        end_y: float,
        dpi: int = 150
    ) -> Tuple[str, int]:
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
                log.warning(f"Empty region on page {start_page}: y={start_y} to y={end_y}")
                # Create a minimal white image
                img = Image.new('RGB', (int(page.rect.width), 1), color='white')
                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
                img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                return img_base64, 1

            problem_pdf = fitz.open()
            problem_page = problem_pdf.new_page(width=region.width, height=region.height)
            problem_page.show_pdf_page(problem_page.rect, pdf_document, start_page, clip=region)

            pix = problem_page.get_pixmap(dpi=dpi)
            img_bytes = pix.tobytes("png")
            img_base64 = base64.b64encode(img_bytes).decode("utf-8")

            problem_pdf.close()

            return img_base64, int(region.height)

        else:
            # Multi-page region - extract each page's portion and merge vertically
            log.info(f"Extracting cross-page region from page {start_page} (y={start_y}) to page {end_page} (y={end_y})")

            # Extract first page (from start_y to bottom)
            first_page = pdf_document[start_page]
            first_region = fitz.Rect(0, start_y, first_page.rect.width, first_page.rect.height)

            log.debug(f"First page region: height={first_region.height}, is_empty={first_region.is_empty}, page_height={first_page.rect.height}")

            # Skip first page if region is empty (start_y is at page boundary)
            if not first_region.is_empty and first_region.height > 0:
                problem_pdf = fitz.open()
                problem_page = problem_pdf.new_page(width=first_region.width, height=first_region.height)
                problem_page.show_pdf_page(problem_page.rect, pdf_document, start_page, clip=first_region)

                pix = problem_page.get_pixmap(dpi=dpi)
                img_bytes = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_bytes))
                page_images.append(img)
                problem_pdf.close()
            else:
                log.debug(f"Skipping empty region on first page {start_page} (y={start_y} to bottom)")

            # Extract middle pages (full pages)
            for page_num in range(start_page + 1, end_page):
                page = pdf_document[page_num]
                region = fitz.Rect(0, 0, page.rect.width, page.rect.height)

                problem_pdf = fitz.open()
                problem_page = problem_pdf.new_page(width=region.width, height=region.height)
                problem_page.show_pdf_page(problem_page.rect, pdf_document, page_num, clip=region)

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
                problem_page = problem_pdf.new_page(width=last_region.width, height=last_region.height)
                problem_page.show_pdf_page(problem_page.rect, pdf_document, end_page, clip=last_region)

                pix = problem_page.get_pixmap(dpi=dpi)
                img_bytes = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_bytes))
                page_images.append(img)
                problem_pdf.close()
            else:
                log.debug(f"Skipping empty region on last page {end_page} (top to y={end_y})")

            # Handle case where we have no images (all regions were empty)
            if not page_images:
                log.warning(f"No valid regions extracted from page {start_page} to {end_page}")
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

    def split_page_by_lines(
        self,
        page: fitz.Page,
        line_positions: List[int],
        include_top_margin: bool = True,
        min_region_height: int = 100
    ) -> List[fitz.Rect]:
        """
        Split a page into regions based on horizontal line positions.
        Lines are treated as TOP borders of questions (line is above the question).

        Args:
            page: PyMuPDF page object
            line_positions: Y-coordinates of horizontal divider lines (sorted)
            include_top_margin: Whether to include the region above the first line
            min_region_height: Minimum height in points for a region to be included (default 100)

        Returns:
            List of fitz.Rect objects defining each problem region
        """
        regions = []
        page_height = page.rect.height
        page_width = page.rect.width

        if not line_positions:
            # No lines detected, return full page
            return [page.rect]

        # Add top region if requested (e.g., on first page above first question)
        if include_top_margin and line_positions[0] > min_region_height:
            regions.append(fitz.Rect(0, 0, page_width, line_positions[0]))

        # Add regions FROM each line DOWN to the next line
        # (Line is the TOP border of the question)
        for i in range(len(line_positions) - 1):
            y_start = line_positions[i]  # Start at the line (include it)
            y_end = line_positions[i + 1]
            height = y_end - y_start

            # Only include if region is tall enough
            if height >= min_region_height:
                regions.append(fitz.Rect(0, y_start, page_width, y_end))
            else:
                log.debug(f"Skipping small region at y={y_start} (height={height})")

        # Add bottom region (from last line to end of page)
        y_start = line_positions[-1]
        height = page_height - y_start
        if height >= min_region_height:
            regions.append(fitz.Rect(0, y_start, page_width, page_height))

        log.info(f"Split page into {len(regions)} regions (filtered by min height {min_region_height})")
        return regions

    def redact_and_get_pdf_data(self, pdf_path: Path) -> str:
        """
        Redact name area and return PDF as base64 string.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Base64 encoded PDF data
        """
        pdf_document = fitz.open(str(pdf_path))

        # Redact name area on first page
        if pdf_document.page_count > 0:
            pdf_document[0].draw_rect(self.fitz_name_rect, color=(0, 0, 0), fill=(0, 0, 0))

        # Save to bytes and encode
        pdf_bytes = pdf_document.tobytes()
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

        pdf_document.close()

        return pdf_base64

    def redact_and_extract_regions(
        self,
        pdf_path: Path,
        split_points: Dict[int, List[int]],
        detect_blank: bool = False,
        blank_confidence_threshold: float = 0.8,
        use_ai_for_borderline: bool = False,
        problem_max_points: Dict[int, float] = None,
        extract_max_points_enabled: bool = False,
        skip_first_region: bool = True,
        last_page_blank: bool = False
    ) -> Tuple[str, List[Dict]]:
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
            pdf_document[0].draw_rect(self.fitz_name_rect, color=(0, 0, 0), fill=(0, 0, 0))

        # Save redacted PDF as base64 (once for the entire submission)
        pdf_bytes = pdf_document.tobytes()
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

        # Create a linear list of all split points across pages
        # Each split point is (page_num, y_position)
        # Only add splits that were explicitly provided by the user
        linear_splits = []

        for page_num in range(total_pages):
            page = pdf_document[page_num]
            page_height = page.rect.height

            # Add manual split points for this page
            line_positions = split_points.get(page_num, [])
            for y_pos in sorted(line_positions):
                # Normalize splits at page boundaries:
                # If a split is at the very bottom of a page (within 1pt tolerance),
                # treat it as being at the top of the next page instead
                if abs(y_pos - page_height) < 1.0 and page_num < total_pages - 1:
                    # This is a page boundary split - move it to start of next page
                    log.debug(f"Normalizing page boundary split: ({page_num}, {y_pos}) -> ({page_num + 1}, 0)")
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
            linear_splits = [(0, 0), (total_pages - 1, pdf_document[total_pages - 1].rect.height)]

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

        log.info(f"Created linear split list with {len(linear_splits)} splits across {total_pages} pages")
        log.info(f"Linear splits: {linear_splits}")

        # Filter out last page if requested (common with odd-numbered page counts)
        if last_page_blank and total_pages > 0:
            last_page_num = total_pages - 1
            # Remove all splits that reference the last page
            splits_before_filter = len(linear_splits)
            linear_splits = [(page, y) for page, y in linear_splits if page < last_page_num]

            # Ensure we have an ending split at the bottom of the second-to-last page
            if total_pages > 1 and linear_splits:
                second_to_last_page = pdf_document[last_page_num - 1]
                expected_end = (last_page_num - 1, second_to_last_page.rect.height)
                if linear_splits[-1] != expected_end:
                    linear_splits.append(expected_end)
                    log.debug(f"Added ending split at bottom of page {last_page_num - 1}")

            splits_removed = splits_before_filter - len(linear_splits)
            log.info(f"Skipping last page (page {last_page_num}) - removed {splits_removed} split(s)")
            log.info(f"Updated linear splits: {linear_splits}")

        # Determine starting index for problem extraction
        # If skip_first_region is True, skip the first split pair (header region)
        start_index = 1 if skip_first_region else 0

        if skip_first_region and len(linear_splits) > 1:
            log.info(f"Skipping first region (header/title area): from {linear_splits[0]} to {linear_splits[1]}")

        # NOW scan QR codes from the linearized problem regions (BEFORE redaction)
        # This must happen after linear_splits is calculated but before problems are created
        if self.qr_scanner.available:
            log.info(f"Pre-scanning {len(linear_splits) - 1 - start_index} problem regions for QR codes from unredacted PDF...")
            problem_number_prescan = 1

            for i in range(start_index, len(linear_splits) - 1):
                start_page, start_y = linear_splits[i]
                end_page, end_y = linear_splits[i + 1]

                # Adjust end point if needed (same logic as problem extraction)
                if end_y == 0 and end_page > start_page:
                    end_page = end_page - 1
                    end_y = pdf_document_original[end_page].rect.height

                # Extract region from ORIGINAL unredacted PDF at higher DPI for QR detection
                problem_image_base64, _ = self._extract_cross_page_region(
                    pdf_document_original,
                    start_page, start_y,
                    end_page, end_y,
                    dpi=300  # Higher DPI for complex QR codes
                )

                # Scan for QR code in this problem region
                qr_data = self.qr_scanner.scan_qr_from_image(problem_image_base64)
                if qr_data:
                    log.info(f"Pre-scan: Problem {problem_number_prescan}: "
                            f"Found QR code with max_points={qr_data['max_points']}")
                    qr_data_by_problem[problem_number_prescan] = qr_data
                else:
                    log.debug(f"Pre-scan: Problem {problem_number_prescan}: No QR code found")

                problem_number_prescan += 1

            log.info(f"Pre-scan complete: Found {len(qr_data_by_problem)} QR codes out of {problem_number_prescan - 1} problems")

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
                log.debug(f"Adjusted end point from top of page {end_page + 1} to bottom of page {end_page}")

            log.debug(f"Problem {problem_number}: from ({start_page}, {start_y}) to ({end_page}, {end_y})")

            # Extract region(s) and create merged image
            problem_image_base64, region_height = self._extract_cross_page_region(
                pdf_document,
                start_page, start_y,
                end_page, end_y
            )

            # Initialize problem dict with region coordinates
            problem_dict = {
                "problem_number": problem_number,
                "page_number": start_page,  # Start page for backwards compatibility
                "region_y_start": int(start_y),
                "region_y_end": int(end_y) if start_page == end_page else int(pdf_document[start_page].rect.height),
                "region_height": region_height,
                "is_blank": False,
                "blank_confidence": 0.0
            }

            # For cross-page problems, add end page info
            if end_page != start_page:
                problem_dict["end_page_number"] = end_page
                problem_dict["end_region_y"] = int(end_y)
                log.info(f"Problem {problem_number} spans multiple pages: {start_page} to {end_page}")

            # Check if we have pre-scanned QR data for this problem
            qr_data = qr_data_by_problem.get(problem_number)

            if qr_data:
                log.info(f"Problem {problem_number}: Using pre-scanned QR code data with max_points={qr_data['max_points']}")
                problem_dict["max_points"] = qr_data["max_points"]
                problem_dict["qr_encrypted_data"] = qr_data.get("encrypted_data")  # Store encrypted string for answer regeneration

                # Cache the max points for this problem number (for future exams)
                if problem_max_points is not None:
                    problem_max_points[problem_number] = qr_data["max_points"]

            # Detect blank if requested
            if detect_blank:
                heuristic_result = self.is_blank_heuristic(problem_image_base64)
                problem_dict["is_blank"] = heuristic_result["is_blank"]
                problem_dict["blank_confidence"] = heuristic_result["confidence"]
                problem_dict["blank_method"] = "heuristic"

                if use_ai_for_borderline and heuristic_result["confidence"] < blank_confidence_threshold:
                    log.info(f"Problem {problem_number}: Low confidence ({heuristic_result['confidence']:.2f}), using AI verification")
                    ai_result = self.is_blank_ai(problem_image_base64)
                    problem_dict["is_blank"] = ai_result["is_blank"]
                    problem_dict["blank_confidence"] = ai_result["confidence"]
                    problem_dict["blank_method"] = "ai"
                    problem_dict["blank_reasoning"] = ai_result.get("reasoning", "")

            # Extract max points from score box
            if problem_max_points and problem_number in problem_max_points:
                problem_dict["max_points"] = problem_max_points[problem_number]
            elif extract_max_points_enabled:
                max_points = self.extract_max_points(problem_image_base64)
                if max_points is not None:
                    problem_dict["max_points"] = max_points
                    if problem_max_points is not None:
                        problem_max_points[problem_number] = max_points

            problems.append(problem_dict)
            problem_number += 1

        pdf_document.close()

        # Filter out blank trailing page if present
        if problems and detect_blank:
            last_problem = problems[-1]
            # For last problem, need to extract and check
            pdf_doc = fitz.open("pdf", base64.b64decode(pdf_base64))
            page = pdf_doc[last_problem["page_number"]]
            region = fitz.Rect(0, last_problem["region_y_start"], page.rect.width, last_problem["region_y_end"])

            problem_pdf = fitz.open()
            problem_page = problem_pdf.new_page(width=region.width, height=region.height)
            problem_page.show_pdf_page(problem_page.rect, pdf_doc, last_problem["page_number"], clip=region)

            pix = problem_page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            img_base64 = base64.b64encode(img_bytes).decode("utf-8")

            full_page_check = self.is_blank_heuristic(img_base64, crop_to_answer_area=False, threshold=0.015)

            if full_page_check["is_blank"] and full_page_check["confidence"] > 0.85:
                log.info(f"Removing blank trailing page (problem {last_problem['problem_number']}) - ink_density={full_page_check['ink_density']:.4f}")
                problems.pop()

            problem_pdf.close()
            pdf_doc.close()

        if detect_blank:
            blank_count = sum(1 for p in problems if p["is_blank"])
            log.info(f"Split PDF into {len(problems)} problems ({blank_count} detected as blank) using manual split points")
        else:
            log.info(f"Split PDF into {len(problems)} problems using manual split points")

        return pdf_base64, problems

    def is_blank_heuristic(self, image_base64: str, threshold: float = 0.02, crop_to_answer_area: bool = True) -> Dict:
        """
        Use heuristics to determine if a problem image appears blank/unanswered.

        Args:
            image_base64: Base64 encoded image
            threshold: Ink density threshold (default 2% = mostly blank)
            crop_to_answer_area: If True, only analyze middle/bottom area (skip printed question text)

        Returns:
            Dict with {is_blank: bool, confidence: float, ink_density: float, edge_density: float}
        """
        import io
        from PIL import Image

        # Decode image
        img_bytes = base64.b64decode(image_base64)
        img = Image.open(io.BytesIO(img_bytes))

        # Convert to grayscale
        if img.mode != 'L':
            img = img.convert('L')

        # Crop to answer area if requested (skip top 30% where question text usually is)
        if crop_to_answer_area:
            width, height = img.size
            # Crop to middle 60% vertically (skip top 20% and bottom 20%)
            # This avoids printed question text at top and page numbers at bottom
            crop_top = int(height * 0.2)
            crop_bottom = int(height * 0.8)
            img = img.crop((0, crop_top, width, crop_bottom))
            log.debug(f"Cropped to answer area: {crop_top} to {crop_bottom} (middle 60%)")
        else:
            # For full page checks, still apply small margins to avoid edge artifacts
            width, height = img.size
            margin = 30  # 30 pixels on each side
            img = img.crop((margin, margin, width - margin, height - margin))
            log.debug(f"Using full page with {margin}px margins for blank detection")

        # Convert to numpy array
        img_array = np.array(img)

        # Calculate ink density (ratio of dark pixels that are likely real handwriting)
        # Use threshold at 200 to ignore light gray bleed-through from opposite page
        # (handwritten ink is typically much darker than bleed-through)
        ink_pixels = np.sum(img_array < 200)
        total_pixels = img_array.size
        ink_density = ink_pixels / total_pixels

        # Calculate edge density (how much writing/structure is present)
        # Use higher thresholds to ignore faint bleed-through edges
        edges = cv2.Canny(img_array, 100, 200)
        edge_pixels = np.sum(edges > 0)
        edge_density = edge_pixels / total_pixels

        # Calculate pixel variance (blank pages have low variance)
        pixel_variance = np.var(img_array)

        # Determine if blank based on heuristics
        # More lenient thresholds since we're now only counting darker pixels
        is_blank = (
            ink_density < 0.03 and  # Less than 3% dark ink
            edge_density < 0.015 and  # Less than 1.5% strong edges
            pixel_variance < 150  # Low variance (but allow for some bleed-through noise)
        )

        # Confidence score (higher = more confident in the assessment)
        if is_blank:
            # If clearly blank (very low ink), high confidence
            confidence = 1.0 - (ink_density / threshold)
        else:
            # If has content, confidence based on how much content
            confidence = min(1.0, ink_density / threshold)

        log.debug(f"Blank detection: is_blank={is_blank}, ink_density={ink_density:.4f}, "
                  f"edge_density={edge_density:.4f}, variance={pixel_variance:.2f}, confidence={confidence:.2f}")

        return {
            "is_blank": is_blank,
            "confidence": confidence,
            "ink_density": ink_density,
            "edge_density": edge_density,
            "pixel_variance": pixel_variance
        }

    def is_blank_ai(self, image_base64: str) -> Dict:
        """
        Use AI to determine if a problem image is blank/unanswered.
        Only call this for borderline cases where heuristic is uncertain.

        Args:
            image_base64: Base64 encoded image (full problem image)

        Returns:
            Dict with {is_blank: bool, confidence: float, reasoning: str}
        """
        try:
            query = """Is this exam question unanswered (no handwritten work)?

This is an exam question that may have printed text (the question) and blank space for the answer.
Look for ANY handwritten work, calculations, or answers. Even partial attempts count as answered.
Ignore printed text, lines, and page numbers - only look for handwriting/student work.

Respond with ONLY a JSON object in this format:
{"is_blank": true/false, "confidence": 0.0-1.0, "reasoning": "brief explanation"}"""

            response, _ = ai_helper.AI_Helper__Anthropic().query_ai(
                query,
                attachments=[("png", image_base64)]
            )

            # Parse JSON response
            import json
            result = json.loads(response.strip())

            log.info(f"AI blank detection: is_blank={result['is_blank']}, "
                     f"confidence={result['confidence']}, reasoning={result['reasoning']}")

            return result

        except Exception as e:
            log.error(f"AI blank detection failed: {e}")
            return {
                "is_blank": False,  # Default to not blank if AI fails
                "confidence": 0.0,
                "reasoning": f"AI detection failed: {str(e)}"
            }

    def extract_max_points(self, image_base64: str) -> Optional[float]:
        """
        Extract max points from score box in upper right corner.
        Looks for patterns like "___/8" or "____ / 10"
        """
        try:
            from PIL import Image
            import io
            import re

            # Decode image
            image_data = base64.b64decode(image_base64)
            img = Image.open(io.BytesIO(image_data))

            # Crop to upper right corner, avoiding name redaction area
            # Name box is typically left-center (350-600px from left)
            # Score box is in upper right corner
            width, height = img.size
            crop_height = int(height * 0.15)

            # Use rightmost 15% of width to avoid name box
            crop_width = int(width * 0.15)
            crop_box = (width - crop_width, 0, width, crop_height)
            cropped = img.crop(crop_box)

            # Convert to base64
            buffer = io.BytesIO()
            cropped.save(buffer, format='PNG')
            cropped_b64 = base64.b64encode(buffer.getvalue()).decode()

            # Use AI to extract the number
            query = """Look at this image of a score box from the upper right corner of an exam problem.
It should contain text like "___/8" or "____ / 10" where the number after the slash is the maximum points for this problem.

Extract ONLY the number after the slash. If you cannot find a clear score box pattern, respond with "NOT_FOUND".
Your response should be either a single number (e.g., "8" or "10") or "NOT_FOUND"."""

            response, _ = ai_helper.AI_Helper__Anthropic().query_ai(query, attachments=[("png", cropped_b64)])
            text = response.strip()

            # Try to extract a number
            match = re.search(r'\d+\.?\d*', text)
            if match:
                max_points = float(match.group())
                log.info(f"Extracted max points: {max_points} from score box")
                return max_points
            else:
                log.warning(f"Could not extract max points from AI response: {text}")
                return None

        except Exception as e:
            log.error(f"Max points extraction failed: {e}")
            return None
