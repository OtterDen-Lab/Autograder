"""
Service for finalizing grading: annotating PDFs and uploading to Canvas.
"""
import asyncio
import base64
import io
import json
import logging
from pathlib import Path
from typing import List, Dict, Tuple
import fitz  # PyMuPDF
from PIL import Image

from ..database import get_db_connection
from lms_interface.canvas_interface import CanvasInterface
from lms_interface.classes import Feedback
from .. import sse

log = logging.getLogger(__name__)


class FinalizationService:
    """Handles finalization of grading sessions"""

    def __init__(self, session_id: int, temp_dir: Path, stream_id: str, event_loop):
        self.session_id = session_id
        self.temp_dir = temp_dir
        self.stream_id = stream_id
        self.event_loop = event_loop  # Store event loop reference for thread communication
        self.canvas_interface = None
        self.course = None
        self.assignment = None
        self.total_submissions = 0
        self.current_submission = 0
        # Step-based progress tracking (3 steps per submission: PDF, comments, upload)
        self.steps_per_submission = 3
        self.total_steps = 0
        self.current_step = 0

    def finalize(self):
        """Main finalization process (runs in thread to avoid blocking event loop)"""
        # Get session info and initialize Canvas
        session_info = self._get_session_info()
        self._init_canvas(session_info)

        # Get all submissions
        submissions = self._get_submissions()
        self.total_submissions = len(submissions)
        # Add 1 for final cleanup step
        self.total_steps = (self.total_submissions * self.steps_per_submission) + 1

        log.info(f"Finalizing {len(submissions)} submissions ({self.total_steps} total steps)")

        # Process each submission
        for i, submission in enumerate(submissions, 1):
            self.current_submission = i
            student_name = submission['student_name'] or 'Unknown'

            try:
                # Generate annotated PDF
                self._update_progress(f"Processing {i}/{len(submissions)}: Generating PDF for {student_name}")
                pdf_path = self._create_annotated_pdf(submission)

                # Generate comments
                self._update_progress(f"Processing {i}/{len(submissions)}: Preparing comments for {student_name}")
                comments = self._generate_comments(submission)

                # Upload to Canvas
                self._update_progress(f"Processing {i}/{len(submissions)}: Uploading to Canvas for {student_name}")
                self._upload_to_canvas(submission, pdf_path, comments)

                log.info(f"Successfully uploaded submission {i}/{len(submissions)} for {student_name}")

            except Exception as e:
                log.error(f"Failed to process submission {submission['id']}: {e}", exc_info=True)
                self._update_progress(f"Processing {i}/{len(submissions)}: ERROR - Failed for {student_name}: {str(e)}")
                # Continue with other submissions

        # Final cleanup step
        self._update_progress(f"Finalization complete - all {len(submissions)} submissions processed")

    def _get_session_info(self) -> Dict:
        """Get session information from database"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT course_id, assignment_id, canvas_points, use_prod_canvas
                FROM grading_sessions
                WHERE id = ?
            """, (self.session_id,))

            row = cursor.fetchone()

            # Handle older sessions without use_prod_canvas column
            try:
                use_prod = row["use_prod_canvas"] if row["use_prod_canvas"] is not None else 0
            except (KeyError, IndexError):
                use_prod = 0

            log.info(f"Session {self.session_id}: use_prod_canvas from DB = {row['use_prod_canvas'] if 'use_prod_canvas' in row.keys() else 'NOT FOUND'} (type: {type(row['use_prod_canvas']) if 'use_prod_canvas' in row.keys() else 'N/A'}), computed use_prod = {use_prod}")

            return {
                "course_id": row["course_id"],
                "assignment_id": row["assignment_id"],
                "canvas_points": row["canvas_points"],
                "use_prod_canvas": use_prod
            }

    def _init_canvas(self, session_info: Dict):
        """Initialize Canvas interface"""
        use_prod = bool(session_info.get("use_prod_canvas", 0))
        log.info(f"Initializing Canvas interface: session_info['use_prod_canvas'] = {session_info.get('use_prod_canvas')} → use_prod = {use_prod}")
        log.info(f"Calling CanvasInterface(prod={use_prod})")
        self.canvas_interface = CanvasInterface(prod=use_prod)
        log.info(f"Canvas interface initialized with URL: {self.canvas_interface.canvas_url}")
        self.course = self.canvas_interface.get_course(session_info["course_id"])
        self.assignment = self.course.get_assignment(session_info["assignment_id"])

    def _get_submissions(self) -> List[Dict]:
        """Get all submissions for the session"""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Get submissions with exam_pdf_data
            cursor.execute("""
                SELECT
                    s.id,
                    s.student_name,
                    s.canvas_user_id,
                    s.page_mappings,
                    s.exam_pdf_data
                FROM submissions s
                WHERE s.session_id = ?
            """, (self.session_id,))

            submissions = []
            for row in cursor.fetchall():
                submission_id = row["id"]

                # Get problems for this submission
                cursor.execute("""
                    SELECT
                        problem_number,
                        score,
                        COALESCE(feedback, '') as feedback,
                        region_coords
                    FROM problems
                    WHERE submission_id = ?
                    ORDER BY problem_number
                """, (submission_id,))

                problems = []

                for prob_row in cursor.fetchall():
                    prob_num = prob_row["problem_number"]

                    # Parse region coordinates if available
                    region_coords = None
                    if prob_row["region_coords"]:
                        region_coords = json.loads(prob_row["region_coords"])

                    problems.append({
                        "problem_number": prob_num,
                        "score": prob_row["score"] or 0.0,
                        "feedback": prob_row["feedback"],
                        "region_coords": region_coords
                    })

                submissions.append({
                    "id": submission_id,
                    "student_name": row["student_name"],
                    "canvas_user_id": row["canvas_user_id"],
                    "page_mappings": json.loads(row["page_mappings"]),
                    "exam_pdf_data": row["exam_pdf_data"],
                    "problems": problems
                })

            return submissions

    def _extract_problem_image_from_pdf(self, pdf_base64: str, page_number: int,
                                       region_y_start: int, region_y_end: int) -> str:
        """
        Extract a problem image from stored PDF data using region coordinates.

        Returns:
            Base64 encoded PNG image of the problem region
        """
        # Decode PDF from base64
        pdf_bytes = base64.b64decode(pdf_base64)
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

    def _create_annotated_pdf(self, submission: Dict) -> Path:
        """
        Create annotated PDF by adding score stickers to the original PDF.
        Works directly with the original exam PDF instead of reconstructing from images.
        """
        output_path = self.temp_dir / f"exam_{submission['id']}.pdf"

        # Check if we have the original PDF data
        if not submission.get("exam_pdf_data"):
            log.error(f"No exam_pdf_data for submission {submission['id']}, cannot create annotated PDF")
            raise ValueError(f"Missing exam_pdf_data for submission {submission['id']}")

        # Decode and open the original PDF
        pdf_bytes = base64.b64decode(submission["exam_pdf_data"])
        pdf_doc = fitz.open("pdf", pdf_bytes)

        # Add score stickers to each problem region
        for problem in submission["problems"]:
            region_coords = problem.get("region_coords")

            if not region_coords:
                log.warning(f"No region_coords for problem {problem['problem_number']}, skipping annotation")
                continue

            page_number = region_coords["page_number"]
            region_y_start = region_coords["region_y_start"]
            region_y_end = region_coords["region_y_end"]

            # Get the page
            if page_number >= len(pdf_doc):
                log.warning(f"Page {page_number} out of range for submission {submission['id']}")
                continue

            page = pdf_doc[page_number]

            # Add score sticker in the problem region (upper right corner of the region)
            self._add_score_sticker_at_region(
                page,
                problem["score"],
                region_y_start,
                region_y_end
            )

        # Save the annotated PDF with compression
        pdf_doc.save(
            str(output_path),
            garbage=4,  # Maximum garbage collection
            deflate=True,  # Compress content streams
            clean=True  # Clean up unused objects
        )
        pdf_doc.close()

        log.info(f"Created annotated PDF for submission {submission['id']} at {output_path}")
        return output_path

    def _add_score_sticker_at_region(self, page: fitz.Page, score: float, region_y_start: int, region_y_end: int):
        """
        Add a score sticker to the upper right corner of a problem region.

        Args:
            page: The PDF page to annotate
            score: The score to display
            region_y_start: Top Y coordinate of the problem region
            region_y_end: Bottom Y coordinate of the problem region
        """
        # Define sticker dimensions and position
        sticker_width = 60
        sticker_height = 30
        margin = 10

        # Position in upper right corner of the region
        page_width = page.rect.width
        x0 = page_width - sticker_width - margin
        y0 = region_y_start + margin
        x1 = page_width - margin
        y1 = region_y_start + margin + sticker_height

        # Create rectangle for background (black with 90% opacity)
        rect = fitz.Rect(x0, y0, x1, y1)
        page.draw_rect(rect, color=None, fill=(0, 0, 0), fill_opacity=0.9)

        # Add score text (blue, fully opaque)
        score_text = f"{score:.1f}"
        text_point = fitz.Point(x0 + sticker_width / 2, y0 + sticker_height / 2 + 5)

        # Insert text centered in the sticker
        page.insert_text(
            text_point,
            score_text,
            fontsize=16,
            fontname="Helvetica-Bold",
            color=(0.2, 0.5, 1.0),  # Blue color
            render_mode=0  # Fill text (fully opaque)
        )

    def _generate_comments(self, submission: Dict) -> str:
        """Generate feedback comments for Canvas in markdown format"""
        comments = []

        # Overall score
        total_score = sum(p["score"] for p in submission["problems"])
        comments.append(f"# Grading Summary\n")
        comments.append(f"**Total Score:** {total_score:.2f}\n")
        comments.append("---\n")

        # Per-problem breakdown with markdown headers
        for problem in sorted(submission["problems"], key=lambda p: p["problem_number"]):
            comments.append(f"## Problem {problem['problem_number']}")
            comments.append(f"**Score:** {problem['score']:.2f}\n")

            if problem["feedback"]:
                # Add feedback with proper spacing
                comments.append(f"{problem['feedback']}\n")

            # Add separator between problems
            comments.append("---\n")

        return "\n".join(comments)

    def _upload_to_canvas(self, submission: Dict, pdf_path: Path, comments: str):
        """Upload graded exam and comments to Canvas"""
        # Create feedback object
        feedback = Feedback()
        feedback.comments = comments

        # Add PDF as attachment
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        # Canvas expects file-like objects
        pdf_file = io.BytesIO(pdf_bytes)
        pdf_file.name = f"graded_exam_{submission['student_name']}.pdf"

        # Upload to Canvas
        self.assignment.push_feedback(
            score=sum(p["score"] for p in submission["problems"]),
            comments=comments,
            attachments=[pdf_file],
            user_id=submission["canvas_user_id"],
            keep_previous_best=True,
            clobber_feedback=False
        )

    def _update_progress(self, message: str):
        """Update progress message in database and send SSE event"""
        # Increment step counter
        self.current_step += 1

        # Update database
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE grading_sessions
                SET processing_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (message, self.session_id))

        # Send SSE progress event based on steps completed
        if self.total_steps > 0:
            progress_percent = min(100, int((self.current_step / self.total_steps) * 100))
            try:
                # Use stored event loop reference (we're in a thread)
                asyncio.run_coroutine_threadsafe(
                    sse.send_event(self.stream_id, "progress", {
                        "total": self.total_submissions,
                        "current": self.current_submission,
                        "progress": progress_percent,
                        "current_step": self.current_step,
                        "total_steps": self.total_steps,
                        "message": message
                    }),
                    self.event_loop
                )
            except Exception as e:
                log.error(f"Failed to send SSE event: {e}")

        log.info(message)
