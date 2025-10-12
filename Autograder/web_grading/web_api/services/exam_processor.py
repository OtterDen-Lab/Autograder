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

NAME_SIMILARITY_THRESHOLD = 95  # Percentage threshold for fuzzy matching


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
        manual_split_points: Optional[Dict[int, List[int]]] = None
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Process exam PDFs.

        Args:
            input_files: List of PDF file paths
            canvas_students: List of student dicts with name and user_id
            page_ranges: Optional list of (start, end) page ranges to merge
            use_ai: Whether to use AI for name extraction
            detect_blank: Whether to detect blank/unanswered problems
            blank_confidence_threshold: Confidence threshold for using AI verification on blanks
            use_ai_for_borderline: Whether to use AI for low-confidence blank detections
            progress_callback: Optional callback function(processed, matched, message) for progress updates
            document_id_offset: Starting document_id (useful when adding more exams to existing session)
            file_metadata: Optional dict mapping file_path -> {hash, original_filename}

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

                if best_score > NAME_SIMILARITY_THRESHOLD:
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
                    extract_max_points_enabled=extract_max_points_enabled
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
        extract_max_points_enabled: bool = False
    ) -> Tuple[str, List[Dict]]:
        """
        Redact names and extract problem regions using manual split points.
        Returns PDF data once and region metadata for each problem.

        Args:
            pdf_path: Path to PDF file
            split_points: Dict mapping page_number -> list of y-positions (manual split points from alignment UI)
            detect_blank: Whether to detect blank/unanswered problems
            blank_confidence_threshold: Confidence threshold (0-1) for using AI verification
            use_ai_for_borderline: Whether to use AI for low-confidence detections
            problem_max_points: Shared dict for caching max points by problem number
            extract_max_points_enabled: Whether to extract max points from images

        Returns:
            Tuple of (pdf_base64, problems_list)
            - pdf_base64: Base64 encoded redacted PDF
            - problems_list: List of problem dicts with region metadata
        """
        pdf_document = fitz.open(str(pdf_path))
        total_pages = pdf_document.page_count

        # Redact name area on first page
        if total_pages > 0:
            pdf_document[0].draw_rect(self.fitz_name_rect, color=(0, 0, 0), fill=(0, 0, 0))

        # Save redacted PDF as base64 (once for the entire submission)
        pdf_bytes = pdf_document.tobytes()
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")

        problems = []
        problem_number = 1

        for page_num in range(total_pages):
            page = pdf_document[page_num]

            # Get manual split points for this page
            line_positions = split_points.get(page_num, [])

            # Split page into regions
            regions = self.split_page_by_lines(page, line_positions, include_top_margin=False)

            # Create metadata for each region
            for region in regions:
                # Initialize problem dict with region coordinates
                problem_dict = {
                    "problem_number": problem_number,
                    "page_number": page_num,  # 0-indexed for PDF access
                    "region_y_start": int(region.y0),
                    "region_y_end": int(region.y1),
                    "region_height": int(region.height),
                    "is_blank": False,
                    "blank_confidence": 0.0
                }

                # Always extract region temporarily if we need to do any analysis
                # QR scanning should always run if available (even if other flags are False)
                needs_extraction = detect_blank or extract_max_points_enabled or self.qr_scanner.available

                if needs_extraction:
                    # Extract region as image for analysis
                    problem_pdf = fitz.open()
                    problem_page = problem_pdf.new_page(width=region.width, height=region.height)
                    problem_page.show_pdf_page(problem_page.rect, pdf_document, page_num, clip=region)

                    pix = problem_page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    img_base64 = base64.b64encode(img_bytes).decode("utf-8")

                    # Try to scan QR code first (highest priority for max points)
                    qr_data = self.qr_scanner.scan_qr_from_image(img_base64)
                    if qr_data:
                        log.info(f"Problem {problem_number}: Found QR code with max_points={qr_data['max_points']}")
                        problem_dict["max_points"] = qr_data["max_points"]
                        # Store QR metadata for potential future use (e.g., regenerating answers)
                        problem_dict["qr_question_type"] = qr_data.get("question_type")
                        problem_dict["qr_seed"] = qr_data.get("seed")
                        problem_dict["qr_version"] = qr_data.get("version")

                        # Cache the max points for this problem number
                        if problem_max_points is not None:
                            problem_max_points[problem_number] = qr_data["max_points"]

                    # Detect blank if requested
                    if detect_blank:
                        heuristic_result = self.is_blank_heuristic(img_base64)
                        problem_dict["is_blank"] = heuristic_result["is_blank"]
                        problem_dict["blank_confidence"] = heuristic_result["confidence"]
                        problem_dict["blank_method"] = "heuristic"

                        if use_ai_for_borderline and heuristic_result["confidence"] < blank_confidence_threshold:
                            log.info(f"Problem {problem_number}: Low confidence ({heuristic_result['confidence']:.2f}), using AI verification")
                            ai_result = self.is_blank_ai(img_base64)
                            problem_dict["is_blank"] = ai_result["is_blank"]
                            problem_dict["blank_confidence"] = ai_result["confidence"]
                            problem_dict["blank_method"] = "ai"
                            problem_dict["blank_reasoning"] = ai_result.get("reasoning", "")

                    # Extract max points from score box if not already found via QR code
                    if not qr_data:
                        if problem_max_points and problem_number in problem_max_points:
                            problem_dict["max_points"] = problem_max_points[problem_number]
                        elif extract_max_points_enabled:
                            max_points = self.extract_max_points(img_base64)
                            if max_points is not None:
                                problem_dict["max_points"] = max_points
                                if problem_max_points is not None:
                                    problem_max_points[problem_number] = max_points

                    problem_pdf.close()

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
