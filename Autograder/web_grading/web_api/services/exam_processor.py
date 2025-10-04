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

    def process_exams(
        self,
        input_files: List[Path],
        canvas_students: List[dict],
        page_ranges: Optional[List[Tuple[int, int]]] = None,
        use_ai: bool = True,
        detect_blank: bool = False,
        blank_confidence_threshold: float = 0.8,
        use_ai_for_borderline: bool = False,
        progress_callback: Optional[callable] = None
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

        Returns:
            Tuple of (matched_submissions, unmatched_submissions)
            Each submission dict contains: document_id, student_name, canvas_user_id,
            page_mappings, problems (list of {problem_number, image_base64, is_blank, blank_confidence})
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
            log.info("Using automatic problem detection via horizontal lines")
            # No shuffling for auto-detection (all students get same order)
            page_mappings_by_submission = None

        # Process each PDF
        matched_submissions = []
        unmatched_submissions = []
        unmatched_students = canvas_students.copy()

        for document_id, pdf_path in enumerate(input_files):
            log.info(f"Processing exam {document_id + 1}/{len(input_files)}: {pdf_path.name}")

            # Report progress: starting exam
            if progress_callback:
                progress_callback(
                    processed=document_id,
                    matched=len(matched_submissions),
                    message=f"Processing exam {document_id + 1}/{len(input_files)}: {pdf_path.name}"
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
                    processed=document_id,
                    matched=len(matched_submissions),
                    message=f"Processing exam {document_id + 1}/{len(input_files)}: Extracted name: {approximate_name}"
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
                    processed=document_id,
                    matched=len(matched_submissions) + (1 if matched_student else 0),
                    message=f"Processing exam {document_id + 1}/{len(input_files)}: {match_msg}"
                )

            # Report progress: splitting into problems
            if progress_callback:
                progress_callback(
                    processed=document_id,
                    matched=len(matched_submissions) + (1 if matched_student else 0),
                    message=f"Processing exam {document_id + 1}/{len(input_files)}: Splitting into problems..."
                )

            # Redact and split into problems (use auto-detection if no page_ranges specified)
            if page_ranges is None:
                # Auto-detect problems using horizontal line detection
                problems = self.redact_and_split_auto(
                    pdf_path,
                    detect_blank=detect_blank,
                    blank_confidence_threshold=blank_confidence_threshold,
                    use_ai_for_borderline=use_ai_for_borderline
                )
            else:
                # Use manual page ranges
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
                "problems": problems
            }

            if matched_student:
                matched_submissions.append(submission)
            else:
                unmatched_submissions.append(submission)

            # Report progress: completed exam
            if progress_callback:
                progress_callback(
                    processed=document_id + 1,
                    matched=len(matched_submissions),
                    message=f"Completed exam {document_id + 1}/{len(input_files)} ({len(matched_submissions)} matched, {len(unmatched_submissions)} need matching)"
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

    def detect_horizontal_lines(self, page: fitz.Page, min_line_width_ratio: float = 0.7) -> List[int]:
        """
        Detect horizontal divider lines on a page.

        Args:
            page: PyMuPDF page object
            min_line_width_ratio: Minimum ratio of line width to page width (0.7 = 70% of page width)

        Returns:
            List of y-coordinates where horizontal lines are detected, sorted top to bottom
        """
        # Render page to image
        pix = page.get_pixmap(dpi=150)
        img_data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

        # Convert to grayscale
        if img_data.shape[2] == 4:  # RGBA
            gray = cv2.cvtColor(img_data, cv2.COLOR_RGBA2GRAY)
        elif img_data.shape[2] == 3:  # RGB
            gray = cv2.cvtColor(img_data, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_data

        # Apply binary threshold to get black lines
        _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

        # Detect horizontal lines using morphology
        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (int(pix.width * 0.5), 1))
        detected_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)

        # Find contours of horizontal lines
        contours, _ = cv2.findContours(detected_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        line_positions = []
        min_width = pix.width * min_line_width_ratio

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            # Filter for lines that are wide enough and thin enough
            if w >= min_width and h < 20:  # Line should be at least 70% page width and less than 20px tall
                # Convert from image coordinates (150 DPI) back to PDF coordinates (72 DPI)
                pdf_y = (y / pix.height) * page.rect.height
                line_positions.append(int(pdf_y))

        # Sort lines from top to bottom
        line_positions.sort()

        log.info(f"Detected {len(line_positions)} horizontal divider lines at positions: {line_positions}")
        return line_positions

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

    def redact_and_split_auto(
        self,
        pdf_path: Path,
        detect_blank: bool = False,
        blank_confidence_threshold: float = 0.8,
        use_ai_for_borderline: bool = False
    ) -> List[Dict]:
        """
        Redact names and automatically split PDF into problems based on horizontal line detection.

        Args:
            pdf_path: Path to PDF file
            detect_blank: Whether to detect blank/unanswered problems
            blank_confidence_threshold: Confidence threshold (0-1) for using AI verification
            use_ai_for_borderline: Whether to use AI for low-confidence detections

        Returns:
            List of problem dicts with {problem_number, page_number, image_base64, is_blank, blank_confidence}
        """
        pdf_document = fitz.open(str(pdf_path))
        total_pages = pdf_document.page_count

        problems = []
        problem_number = 1

        for page_num in range(total_pages):
            page = pdf_document[page_num]

            # Redact name area on first page
            if page_num == 0:
                page.draw_rect(self.fitz_name_rect, color=(0, 0, 0), fill=(0, 0, 0))

            # Detect horizontal lines
            line_positions = self.detect_horizontal_lines(page)

            # Split page into regions
            # On first page, don't include top margin (that's the name area)
            # On subsequent pages, include top margin (in case there's content above first line)
            regions = self.split_page_by_lines(page, line_positions, include_top_margin=False)

            # Create a problem for each region
            for region in regions:
                # Create a new single-page PDF with just this region
                problem_pdf = fitz.open()
                problem_page = problem_pdf.new_page(width=region.width, height=region.height)

                # Copy the region content to the new page
                problem_page.show_pdf_page(
                    problem_page.rect,
                    pdf_document,
                    page_num,
                    clip=region
                )

                # Convert to PNG
                pix = problem_page.get_pixmap(dpi=150)
                img_bytes = pix.tobytes("png")
                img_base64 = base64.b64encode(img_bytes).decode("utf-8")

                # Initialize problem dict
                problem_dict = {
                    "problem_number": problem_number,
                    "page_number": page_num + 1,
                    "image_base64": img_base64,
                    "is_blank": False,
                    "blank_confidence": 0.0
                }

                # Detect blank if requested
                if detect_blank:
                    # First try heuristic
                    heuristic_result = self.is_blank_heuristic(img_base64)
                    problem_dict["is_blank"] = heuristic_result["is_blank"]
                    problem_dict["blank_confidence"] = heuristic_result["confidence"]
                    problem_dict["blank_method"] = "heuristic"

                    # If confidence is low and AI is enabled, verify with AI
                    if use_ai_for_borderline and heuristic_result["confidence"] < blank_confidence_threshold:
                        log.info(f"Problem {problem_number}: Low confidence ({heuristic_result['confidence']:.2f}), using AI verification")
                        ai_result = self.is_blank_ai(img_base64)
                        problem_dict["is_blank"] = ai_result["is_blank"]
                        problem_dict["blank_confidence"] = ai_result["confidence"]
                        problem_dict["blank_method"] = "ai"
                        problem_dict["blank_reasoning"] = ai_result.get("reasoning", "")

                problems.append(problem_dict)

                problem_pdf.close()
                problem_number += 1

        pdf_document.close()

        if detect_blank:
            blank_count = sum(1 for p in problems if p["is_blank"])
            log.info(f"Auto-split PDF into {len(problems)} problems ({blank_count} detected as blank) across {total_pages} pages")
        else:
            log.info(f"Auto-split PDF into {len(problems)} problems across {total_pages} pages")

        return problems

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
