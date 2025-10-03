"""
Exam processing service - extracts logic from Assignment__Exam

This service handles:
- PDF processing and splitting
- Student name extraction
- Page shuffling and redaction
"""
from typing import List, Tuple, Optional
from pathlib import Path
import logging

log = logging.getLogger(__name__)


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

    def process_exams(
        self,
        input_dir: Path,
        canvas_students: List[dict],
        page_ranges: Optional[List[Tuple[int, int]]] = None
    ) -> Tuple[List[dict], List[dict]]:
        """
        Process exam PDFs from input directory.

        Args:
            input_dir: Directory containing PDF files
            canvas_students: List of student dicts with name and user_id
            page_ranges: Optional list of (start, end) page ranges to merge

        Returns:
            Tuple of (matched_submissions, unmatched_submissions)
        """
        # TODO: Extract logic from Assignment__Exam.prepare()
        # This will include:
        # - Reading PDFs from input_dir
        # - Extracting names using AI
        # - Fuzzy matching to canvas_students
        # - Shuffling pages
        # - Redacting names
        # - Splitting into individual problems

        log.info(f"Processing exams from {input_dir}")
        log.info(f"Canvas students to match: {len(canvas_students)}")

        # Placeholder
        return [], []

    def extract_name(
        self,
        pdf_path: Path,
        use_ai: bool = True,
        student_names: Optional[List[str]] = None
    ) -> str:
        """
        Extract student name from PDF.

        Args:
            pdf_path: Path to PDF file
            use_ai: Whether to use AI for name extraction
            student_names: Optional list of possible names to guide AI

        Returns:
            Extracted student name
        """
        # TODO: Extract from Assignment__Exam.get_approximate_student_name()
        return ""

    def redact_and_split(
        self,
        pdf_path: Path,
        page_ranges: Optional[List[Tuple[int, int]]] = None
    ) -> List[bytes]:
        """
        Redact names and split PDF into individual problems.

        Args:
            pdf_path: Path to PDF file
            page_ranges: List of (start, end) page ranges

        Returns:
            List of PDF page data as bytes
        """
        # TODO: Extract from Assignment__Exam.redact_and_split()
        return []

    def merge_pages(
        self,
        page_images: List[bytes],
        page_mappings: List[int]
    ) -> bytes:
        """
        Merge pages back into complete PDF.

        Args:
            page_images: List of individual page images
            page_mappings: Order to arrange pages

        Returns:
            Complete PDF as bytes
        """
        # TODO: Extract from Assignment__Exam.merge_pages()
        return b""
