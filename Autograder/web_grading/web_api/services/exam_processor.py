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
        progress_callback: Optional[callable] = None
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Process exam PDFs.

        Args:
            input_files: List of PDF file paths
            canvas_students: List of student dicts with name and user_id
            page_ranges: Optional list of (start, end) page ranges to merge
            use_ai: Whether to use AI for name extraction
            progress_callback: Optional callback function(processed, matched, message) for progress updates

        Returns:
            Tuple of (matched_submissions, unmatched_submissions)
            Each submission dict contains: document_id, student_name, canvas_user_id,
            page_mappings, problems (list of {problem_number, image_base64})
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

        if page_ranges is None:
            # Default: each page is a separate problem
            page_ranges = [(p, p) for p in range(num_pages)]

        log.info(f"Page ranges: {page_ranges}")

        # Create shuffled page mappings
        num_submissions = len(input_files)
        num_problems = len(page_ranges)
        page_mappings_by_submission = collections.defaultdict(list)

        for problem_num in range(num_problems):
            shuffled_order = random.sample(range(num_submissions), k=num_submissions)
            for submission_id, random_id in enumerate(shuffled_order):
                page_mappings_by_submission[submission_id].append(random_id)

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

            # Redact and split into problems
            problem_images = self.redact_and_split(pdf_path, page_ranges)

            # Report progress: generating images
            if progress_callback:
                progress_callback(
                    processed=document_id,
                    matched=len(matched_submissions) + (1 if matched_student else 0),
                    message=f"Processing exam {document_id + 1}/{len(input_files)}: Generating problem images..."
                )

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
                "page_mappings": page_mappings_by_submission[document_id],
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
