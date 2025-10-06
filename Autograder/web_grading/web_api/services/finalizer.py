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

    def __init__(self, session_id: int, temp_dir: Path, stream_id: str):
        self.session_id = session_id
        self.temp_dir = temp_dir
        self.stream_id = stream_id
        self.canvas_interface = None
        self.course = None
        self.assignment = None
        self.total_submissions = 0
        self.current_submission = 0
        # Step-based progress tracking (3 steps per submission: PDF, comments, upload)
        self.steps_per_submission = 3
        self.total_steps = 0
        self.current_step = 0

    async def finalize(self):
        """Main finalization process"""
        # Get session info and initialize Canvas
        session_info = self._get_session_info()
        self._init_canvas(session_info)

        # Get all submissions
        submissions = self._get_submissions()
        self.total_submissions = len(submissions)
        self.total_steps = self.total_submissions * self.steps_per_submission

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

            return {
                "course_id": row["course_id"],
                "assignment_id": row["assignment_id"],
                "canvas_points": row["canvas_points"],
                "use_prod_canvas": use_prod
            }

    def _init_canvas(self, session_info: Dict):
        """Initialize Canvas interface"""
        use_prod = bool(session_info.get("use_prod_canvas", 0))
        log.info(f"Initializing Canvas interface with prod={use_prod}")
        self.canvas_interface = CanvasInterface(prod=use_prod)
        self.course = self.canvas_interface.get_course(session_info["course_id"])
        self.assignment = self.course.get_assignment(session_info["assignment_id"])

    def _get_submissions(self) -> List[Dict]:
        """Get all submissions for the session"""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    s.id,
                    s.student_name,
                    s.canvas_user_id,
                    s.page_mappings,
                    GROUP_CONCAT(p.problem_number || ':' || p.score || ':' || COALESCE(p.feedback, ''), '|') as problem_data,
                    GROUP_CONCAT(p.problem_number || ':' || p.image_data, '|') as image_data
                FROM submissions s
                LEFT JOIN problems p ON p.submission_id = s.id
                WHERE s.session_id = ?
                GROUP BY s.id
            """, (self.session_id,))

            submissions = []
            for row in cursor.fetchall():
                # Parse problem data
                problems = []
                if row["problem_data"]:
                    for prob_str in row["problem_data"].split('|'):
                        parts = prob_str.split(':', 2)
                        problems.append({
                            "problem_number": int(parts[0]),
                            "score": float(parts[1]) if parts[1] else 0.0,
                            "feedback": parts[2] if len(parts) > 2 else ""
                        })

                # Parse image data
                images = {}
                if row["image_data"]:
                    for img_str in row["image_data"].split('|'):
                        parts = img_str.split(':', 1)
                        images[int(parts[0])] = parts[1]

                submissions.append({
                    "id": row["id"],
                    "student_name": row["student_name"],
                    "canvas_user_id": row["canvas_user_id"],
                    "page_mappings": json.loads(row["page_mappings"]),
                    "problems": problems,
                    "images": images
                })

            return submissions

    def _create_annotated_pdf(self, submission: Dict) -> Path:
        """Create annotated PDF with score stickers on each page"""
        output_path = self.temp_dir / f"exam_{submission['id']}.pdf"

        # Create a new PDF document
        pdf_doc = fitz.open()

        # Add each problem as a page with score annotation
        for problem in sorted(submission["problems"], key=lambda p: p["problem_number"]):
            prob_num = problem["problem_number"]

            # Get the image data (base64 PNG)
            if prob_num not in submission["images"]:
                log.warning(f"Missing image for problem {prob_num}")
                continue

            image_data = base64.b64decode(submission["images"][prob_num])

            # Convert PNG to JPEG for smaller file size
            pil_img = Image.open(io.BytesIO(image_data))
            if pil_img.mode in ('RGBA', 'LA', 'P'):
                # Convert transparency to white background
                background = Image.new('RGB', pil_img.size, (255, 255, 255))
                if pil_img.mode == 'P':
                    pil_img = pil_img.convert('RGBA')
                background.paste(pil_img, mask=pil_img.split()[-1] if pil_img.mode == 'RGBA' else None)
                pil_img = background
            elif pil_img.mode != 'RGB':
                pil_img = pil_img.convert('RGB')

            # Save as JPEG with quality=85 (good balance between size and quality)
            jpeg_buffer = io.BytesIO()
            pil_img.save(jpeg_buffer, format='JPEG', quality=85, optimize=True)
            jpeg_data = jpeg_buffer.getvalue()

            # Open the JPEG to get dimensions
            img = fitz.open(stream=jpeg_data, filetype="jpeg")
            img_page = img[0]
            img_rect = img_page.rect

            # Create a new page with the same dimensions as the image
            page = pdf_doc.new_page(width=img_rect.width, height=img_rect.height)

            # Insert the JPEG onto the page
            page.insert_image(img_rect, stream=jpeg_data)
            img.close()

            # Add score sticker in upper right corner
            self._add_score_sticker(page, problem["score"])

        # Save the PDF with compression
        pdf_doc.save(
            str(output_path),
            garbage=4,  # Maximum garbage collection
            deflate=True,  # Compress content streams
            clean=True  # Clean up unused objects
        )
        pdf_doc.close()

        return output_path

    def _add_score_sticker(self, page: fitz.Page, score: float):
        """Add a score sticker to the upper right corner of a page"""
        # Define sticker dimensions and position
        sticker_width = 60
        sticker_height = 30
        margin = 10

        # Position in upper right corner
        page_width = page.rect.width
        x0 = page_width - sticker_width - margin
        y0 = margin
        x1 = page_width - margin
        y1 = margin + sticker_height

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
        """Generate feedback comments for Canvas"""
        comments = []

        # Overall score
        total_score = sum(p["score"] for p in submission["problems"])
        comments.append(f"Total Score: {total_score:.2f}\n")

        # Per-problem breakdown
        comments.append("Per-Problem Breakdown:")
        for problem in sorted(submission["problems"], key=lambda p: p["problem_number"]):
            prob_line = f"Problem {problem['problem_number']}: {problem['score']:.2f}"
            if problem["feedback"]:
                prob_line += f" - {problem['feedback']}"
            comments.append(prob_line)

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
                loop = asyncio.get_event_loop()
                asyncio.run_coroutine_threadsafe(
                    sse.send_event(self.stream_id, "progress", {
                        "total": self.total_submissions,
                        "current": self.current_submission,
                        "progress": progress_percent,
                        "current_step": self.current_step,
                        "total_steps": self.total_steps,
                        "message": message
                    }),
                    loop
                )
            except Exception as e:
                log.error(f"Failed to send SSE event: {e}")

        log.info(message)
