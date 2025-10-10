"""
Service for AI-assisted grading of exam problems.
"""
import logging
from typing import Dict, List, Optional, Tuple
import sys
from pathlib import Path

# Add parent directory to path to import ai_helper
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from Autograder.ai_helper import AI_Helper__Anthropic

from ..database import get_db_connection

log = logging.getLogger(__name__)


class AIGraderService:
    """Handles AI-assisted autograding of exam problems"""

    def __init__(self):
        self.ai_helper = AI_Helper__Anthropic()

    def extract_question_text(self, image_base64: str) -> str:
        """Extract question text from a problem image, ignoring handwritten content.

        Args:
            image_base64: Base64-encoded PNG image of the problem

        Returns:
            Extracted question text
        """
        message = (
            "Please extract the question text from this exam problem image. "
            "Ignore all handwritten text - only extract the printed/typed question text. "
            "Return only the question text without any additional commentary."
        )

        attachments = [("png", image_base64)]
        question_text, usage = self.ai_helper.query_ai(message, attachments, max_response_tokens=2000)

        log.info(f"Extracted question text ({usage['total_tokens']} tokens): {question_text[:100]}...")
        return question_text.strip()

    def decipher_handwriting(self, image_base64: str) -> str:
        """Extract handwritten answer from a problem image.

        Args:
            image_base64: Base64-encoded PNG image of the problem

        Returns:
            Extracted handwritten text
        """
        message = (
            "Please extract ONLY the handwritten text from this exam problem image. "
            "Ignore the printed/typed question text - focus only on what the student wrote. "
            "Return only the handwritten text without any additional commentary."
        )

        attachments = [("png", image_base64)]
        handwriting_text, usage = self.ai_helper.query_ai(message, attachments, max_response_tokens=2000)

        log.info(f"Deciphered handwriting ({usage['total_tokens']} tokens): {handwriting_text[:100]}...")
        return handwriting_text.strip()

    def generate_rubric(self, question_text: str, max_points: float,
                       example_answers: List[Dict] = None) -> str:
        """Generate a grading rubric for a question using AI and representative student answers.

        Args:
            question_text: The exam question
            max_points: Maximum points for this problem
            example_answers: Optional list of dicts with 'answer', 'score', 'feedback'
                           from manually graded examples

        Returns:
            Generated rubric text
        """
        # Build examples section
        examples_section = ""
        if example_answers and len(example_answers) > 0:
            examples_section = "\n\nRepresentative student answers with your manual grades:\n\n"
            for i, example in enumerate(example_answers, 1):
                examples_section += (
                    f"Example {i}:\n"
                    f"Student Answer: {example['answer']}\n"
                    f"Your Score: {example['score']}/{max_points}\n"
                    f"Your Feedback: {example['feedback']}\n\n"
                )

        message = (
            f"You are creating a grading rubric for an exam problem worth {max_points} points.\n\n"
            f"Question:\n{question_text}"
            f"{examples_section}\n"
            f"Please create a detailed grading rubric that breaks down how to award points. "
            f"The rubric should:\n"
            f"1. List key concepts, steps, or components required for a complete answer\n"
            f"2. Specify point values for each component\n"
            f"3. Be clear and objective enough for consistent grading\n"
            f"4. Account for partial credit where appropriate\n"
            f"5. Align with the grading standards shown in the example answers above\n\n"
            f"Format the rubric clearly with point values. For example:\n"
            f"- Correct identification of X (2 points)\n"
            f"- Proper calculation showing Y (3 points)\n"
            f"- Clear explanation of Z (3 points)\n"
            f"- Partial credit: Award 1 point for attempt at X even if incorrect\n\n"
            f"Keep the rubric concise but comprehensive."
        )

        response, usage = self.ai_helper.query_ai(message, [], max_response_tokens=2000)

        log.info(f"Generated rubric ({usage['total_tokens']} tokens): {response[:200]}...")

        return response.strip()

    def grade_problem(self, question_text: str, student_answer: str, max_points: float,
                     grading_examples: List[Dict] = None, rubric: str = None) -> Tuple[int, str]:
        """Grade a student's answer using AI.

        Args:
            question_text: The exam question
            student_answer: The student's handwritten answer
            max_points: Maximum points for this problem
            grading_examples: Optional list of dicts with 'answer', 'score', 'feedback' for few-shot prompting
            rubric: Optional grading rubric to follow

        Returns:
            Tuple of (score, feedback)
        """
        # Build rubric section
        rubric_section = ""
        if rubric:
            rubric_section = f"\n\nGrading Rubric:\n{rubric}\n\nPlease follow this rubric when grading.\n"

        # Build few-shot examples section
        examples_section = ""
        if grading_examples and len(grading_examples) > 0:
            examples_section = "\n\nHere are examples of how you previously graded similar answers to this question:\n\n"
            for i, example in enumerate(grading_examples, 1):
                examples_section += (
                    f"Example {i}:\n"
                    f"Student Answer: {example['answer']}\n"
                    f"Your Score: {example['score']}/{max_points}\n"
                    f"Your Feedback: {example['feedback']}\n\n"
                )
            examples_section += "Please grade the current answer in a similar style and with similar standards.\n"

        message = (
            f"You are grading an exam problem worth {max_points} points.\n\n"
            f"Question:\n{question_text}"
            f"{rubric_section}"
            f"{examples_section}\n"
            f"Current Student's Answer:\n{student_answer}\n\n"
            f"Please grade this answer and provide:\n"
            f"1. An INTEGER score out of {max_points} points (no decimals, round to nearest integer)\n"
            f"2. Clear and constructive feedback for the student\n\n"
            f"IMPORTANT: The score must be a whole number (integer) between 0 and {int(max_points)}.\n"
            f"IMPORTANT: The feedback should be concise, direct, constructive, and helpful for the student to understand what they did well and what could be improved.\n\n"
            f"Format your response as:\n"
            f"SCORE: [integer]\n"
            f"FEEDBACK: [clear and constructive feedback for the student]"
        )

        response, usage = self.ai_helper.query_ai(message, [], max_response_tokens=1000)

        log.info(f"AI grading response ({usage['total_tokens']} tokens): {response[:200]}...")

        # Parse score and feedback from response
        score = 0  # Default to 0 if parsing fails
        feedback = response
        score_found = False

        try:
            lines = response.split('\n')
            for line in lines:
                if line.startswith('SCORE:'):
                    score_str = line.replace('SCORE:', '').strip()
                    # Extract number from string (handles "5" or "5.0" or "5 out of 10")
                    import re
                    score_match = re.search(r'(\d+\.?\d*)', score_str)
                    if score_match:
                        # Convert to int (round if decimal was provided)
                        score = int(round(float(score_match.group(1))))
                        score_found = True
                elif line.startswith('FEEDBACK:'):
                    feedback = line.replace('FEEDBACK:', '').strip()
                    # Get the rest of the response after FEEDBACK:
                    feedback_start = response.find('FEEDBACK:') + len('FEEDBACK:')
                    feedback = response[feedback_start:].strip()
                    break
        except Exception as e:
            log.error(f"Failed to parse AI grading response: {e}")
            feedback = response

        # Ensure score is within valid range (0 to max_points)
        score = max(0, min(int(max_points), score))

        if not score_found:
            log.warning(f"No score found in AI response, defaulting to 0. Response: {response[:200]}")

        return score, feedback

    def get_or_extract_question(self, session_id: int, problem_number: int,
                                 sample_image_base64: str) -> str:
        """Get question text from metadata or extract it from a sample image.

        Args:
            session_id: Grading session ID
            problem_number: Problem number
            sample_image_base64: Sample problem image to extract from if not cached

        Returns:
            Question text
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Check if question already extracted
            cursor.execute("""
                SELECT question_text
                FROM problem_metadata
                WHERE session_id = ? AND problem_number = ?
            """, (session_id, problem_number))

            row = cursor.fetchone()
            if row and row["question_text"]:
                log.info(f"Using cached question text for problem {problem_number}")
                return row["question_text"]

            # Extract question text
            log.info(f"Extracting question text for problem {problem_number}")
            question_text = self.extract_question_text(sample_image_base64)

            # Store in metadata
            cursor.execute("""
                INSERT INTO problem_metadata (session_id, problem_number, question_text)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id, problem_number)
                DO UPDATE SET question_text = excluded.question_text
            """, (session_id, problem_number, question_text))

            return question_text

    def get_grading_examples(self, session_id: int, problem_number: int, limit: int = 3) -> List[Dict]:
        """Fetch examples of previously graded submissions for few-shot prompting.

        Args:
            session_id: Grading session ID
            problem_number: Problem number
            limit: Maximum number of examples to return

        Returns:
            List of dicts with 'answer', 'score', 'feedback'
        """
        examples = []

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Get graded problems (exclude blanks and problems without feedback)
            cursor.execute("""
                SELECT p.id, p.image_data, p.region_coords, p.submission_id, p.score, p.feedback
                FROM problems p
                WHERE p.session_id = ? AND p.problem_number = ? AND p.graded = 1
                      AND p.is_blank = 0 AND p.feedback IS NOT NULL AND p.feedback != ''
                ORDER BY RANDOM()
                LIMIT ?
            """, (session_id, problem_number, limit))

            rows = cursor.fetchall()

            if not rows:
                log.info(f"No graded examples found for problem {problem_number}")
                return examples

            log.info(f"Found {len(rows)} graded examples for problem {problem_number}, deciphering...")

            for row in rows:
                try:
                    # Get image data - either directly or extract from PDF
                    image_data = None
                    if row["image_data"]:
                        # Legacy: image_data is stored
                        image_data = row["image_data"]
                    elif row["region_coords"]:
                        # New: extract from PDF using region_coords
                        import json
                        import base64
                        import fitz

                        region_data = json.loads(row["region_coords"])

                        # Get PDF data from submission
                        cursor.execute(
                            "SELECT exam_pdf_data FROM submissions WHERE id = ?",
                            (row["submission_id"],)
                        )
                        submission_row = cursor.fetchone()

                        if submission_row and submission_row["exam_pdf_data"]:
                            # Extract region from PDF
                            pdf_bytes = base64.b64decode(submission_row["exam_pdf_data"])
                            pdf_document = fitz.open("pdf", pdf_bytes)
                            page = pdf_document[region_data["page_number"]]

                            region = fitz.Rect(0, region_data["region_y_start"], page.rect.width, region_data["region_y_end"])

                            # Extract region as new PDF page
                            problem_pdf = fitz.open()
                            problem_page = problem_pdf.new_page(width=region.width, height=region.height)
                            problem_page.show_pdf_page(problem_page.rect, pdf_document, region_data["page_number"], clip=region)

                            # Convert to PNG
                            pix = problem_page.get_pixmap(dpi=150)
                            img_bytes = pix.tobytes("png")
                            image_data = base64.b64encode(img_bytes).decode("utf-8")

                            # Cleanup
                            problem_pdf.close()
                            pdf_document.close()

                    if not image_data:
                        log.warning(f"No image data available for problem {row['id']}, skipping")
                        continue

                    # Decipher the handwriting from the example
                    student_answer = self.decipher_handwriting(image_data)

                    examples.append({
                        'answer': student_answer,
                        'score': row["score"],
                        'feedback': row["feedback"]
                    })
                except Exception as e:
                    log.warning(f"Failed to decipher example submission: {e}")
                    continue

        log.info(f"Successfully prepared {len(examples)} grading examples")
        return examples

    def autograde_problem(self, session_id: int, problem_number: int,
                          max_points: float = None, progress_callback=None) -> Dict:
        """Autograde all ungraded submissions for a specific problem number.

        Args:
            session_id: Grading session ID
            problem_number: Problem number to grade
            max_points: Maximum points (optional, will query DB if not provided)
            progress_callback: Optional callback function(current, total, message)

        Returns:
            Dictionary with grading results
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # If max_points not provided, try to get from database
            if max_points is None:
                # Get max points for this problem - first check metadata
                cursor.execute("""
                    SELECT max_points FROM problem_metadata
                    WHERE session_id = ? AND problem_number = ?
                """, (session_id, problem_number))

                metadata_row = cursor.fetchone()

                if metadata_row and metadata_row["max_points"]:
                    max_points = metadata_row["max_points"]
                else:
                    # Fall back to max_points from problems table
                    cursor.execute("""
                        SELECT max_points FROM problems
                        WHERE session_id = ? AND problem_number = ?
                        LIMIT 1
                    """, (session_id, problem_number))

                    problem_row = cursor.fetchone()
                    if problem_row and problem_row["max_points"]:
                        max_points = problem_row["max_points"]

                        # Save to metadata for future use
                        cursor.execute("""
                            INSERT INTO problem_metadata (session_id, problem_number, max_points)
                            VALUES (?, ?, ?)
                            ON CONFLICT(session_id, problem_number)
                            DO UPDATE SET max_points = excluded.max_points
                        """, (session_id, problem_number, max_points))

                if not max_points:
                    raise ValueError(f"Max points not set for problem {problem_number}")

            # Get all ungraded problems for this problem number
            cursor.execute("""
                SELECT id, image_data, region_coords, submission_id
                FROM problems
                WHERE session_id = ? AND problem_number = ? AND graded = 0 AND is_blank = 0
                ORDER BY id
            """, (session_id, problem_number))

            problems = cursor.fetchall()
            total = len(problems)

            if total == 0:
                return {"graded": 0, "message": "No ungraded problems found"}

            log.info(f"Autograding {total} problems for problem number {problem_number}")

            # Get question text (use first problem's image as sample)
            # Extract image from first problem
            first_problem = problems[0]
            first_image_data = None
            if first_problem["image_data"]:
                first_image_data = first_problem["image_data"]
            elif first_problem["region_coords"]:
                import json
                import base64
                import fitz

                region_data = json.loads(first_problem["region_coords"])
                cursor.execute(
                    "SELECT exam_pdf_data FROM submissions WHERE id = ?",
                    (first_problem["submission_id"],)
                )
                submission_row = cursor.fetchone()

                if submission_row and submission_row["exam_pdf_data"]:
                    pdf_bytes = base64.b64decode(submission_row["exam_pdf_data"])
                    pdf_document = fitz.open("pdf", pdf_bytes)
                    page = pdf_document[region_data["page_number"]]
                    region = fitz.Rect(0, region_data["region_y_start"], page.rect.width, region_data["region_y_end"])

                    problem_pdf = fitz.open()
                    problem_page = problem_pdf.new_page(width=region.width, height=region.height)
                    problem_page.show_pdf_page(problem_page.rect, pdf_document, region_data["page_number"], clip=region)

                    pix = problem_page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    first_image_data = base64.b64encode(img_bytes).decode("utf-8")

                    problem_pdf.close()
                    pdf_document.close()

            question_text = self.get_or_extract_question(
                session_id, problem_number, first_image_data
            )

            if progress_callback:
                progress_callback(0, total, f"Extracted question for problem {problem_number}")

            # Get rubric from metadata if available
            rubric = None
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT grading_rubric FROM problem_metadata
                    WHERE session_id = ? AND problem_number = ?
                """, (session_id, problem_number))
                rubric_row = cursor.fetchone()
                if rubric_row and rubric_row["grading_rubric"]:
                    rubric = rubric_row["grading_rubric"]
                    log.info(f"Using rubric for problem {problem_number}")

            # Get grading examples for few-shot prompting
            if progress_callback:
                progress_callback(0, total, f"Fetching grading examples for problem {problem_number}")

            grading_examples = self.get_grading_examples(session_id, problem_number, limit=3)

            if progress_callback:
                if len(grading_examples) > 0:
                    progress_callback(0, total, f"Found {len(grading_examples)} grading examples")
                else:
                    progress_callback(0, total, f"No grading examples found, proceeding without")

            # Grade each problem
            graded_count = 0
            for idx, problem in enumerate(problems, 1):
                try:
                    if progress_callback:
                        progress_callback(
                            idx, total,
                            f"Autograding problem {problem_number}, submission {idx}/{total}"
                        )

                    # Get image data - either directly or extract from PDF
                    image_data = None
                    if problem["image_data"]:
                        image_data = problem["image_data"]
                    elif problem["region_coords"]:
                        import json
                        import base64
                        import fitz

                        region_data = json.loads(problem["region_coords"])

                        # Need a new DB connection since we're in a thread executor
                        with get_db_connection() as pdf_conn:
                            pdf_cursor = pdf_conn.cursor()
                            pdf_cursor.execute(
                                "SELECT exam_pdf_data FROM submissions WHERE id = ?",
                                (problem["submission_id"],)
                            )
                            submission_row = pdf_cursor.fetchone()

                            if submission_row and submission_row["exam_pdf_data"]:
                                pdf_bytes = base64.b64decode(submission_row["exam_pdf_data"])
                                pdf_document = fitz.open("pdf", pdf_bytes)
                                page = pdf_document[region_data["page_number"]]
                                region = fitz.Rect(0, region_data["region_y_start"], page.rect.width, region_data["region_y_end"])

                                problem_pdf = fitz.open()
                                problem_page = problem_pdf.new_page(width=region.width, height=region.height)
                                problem_page.show_pdf_page(problem_page.rect, pdf_document, region_data["page_number"], clip=region)

                                pix = problem_page.get_pixmap(dpi=150)
                                img_bytes = pix.tobytes("png")
                                image_data = base64.b64encode(img_bytes).decode("utf-8")

                                problem_pdf.close()
                                pdf_document.close()

                    if not image_data:
                        log.warning(f"No image data available for problem {problem['id']}, skipping")
                        continue

                    # Decipher handwriting
                    student_answer = self.decipher_handwriting(image_data)

                    # Grade the answer with rubric and examples
                    score, feedback = self.grade_problem(
                        question_text, student_answer, max_points,
                        grading_examples=grading_examples,
                        rubric=rubric
                    )

                    # Update problem with AI suggestion (score and feedback ready for instructor review)
                    # Need a new DB connection since we're in a thread executor
                    with get_db_connection() as update_conn:
                        update_cursor = update_conn.cursor()
                        update_cursor.execute("""
                            UPDATE problems
                            SET score = ?, feedback = ?, graded = 0
                            WHERE id = ?
                        """, (score, feedback, problem["id"]))

                    graded_count += 1
                    log.info(f"AI graded problem {problem['id']}: {score}/{max_points}")

                except Exception as e:
                    log.error(f"Failed to autograde problem {problem['id']}: {e}", exc_info=True)
                    continue

            if progress_callback:
                progress_callback(total, total, f"Completed autograding {graded_count} problems")

            return {
                "graded": graded_count,
                "total": total,
                "question_text": question_text,
                "message": f"AI graded {graded_count}/{total} problems"
            }
