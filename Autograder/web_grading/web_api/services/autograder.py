"""
Service for AI-assisted autograding of exam problems.
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


class AutograderService:
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

    def grade_problem(self, question_text: str, student_answer: str, max_points: float,
                     grading_examples: List[Dict] = None) -> Tuple[int, str]:
        """Grade a student's answer using AI.

        Args:
            question_text: The exam question
            student_answer: The student's handwritten answer
            max_points: Maximum points for this problem
            grading_examples: Optional list of dicts with 'answer', 'score', 'feedback' for few-shot prompting

        Returns:
            Tuple of (score, feedback)
        """
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
                SELECT image_data, score, feedback
                FROM problems
                WHERE session_id = ? AND problem_number = ? AND graded = 1
                      AND is_blank = 0 AND feedback IS NOT NULL AND feedback != ''
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
                # Decipher the handwriting from the example
                student_answer = self.decipher_handwriting(row["image_data"])

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
                SELECT id, image_data, submission_id
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
            first_problem = problems[0]
            question_text = self.get_or_extract_question(
                session_id, problem_number, first_problem["image_data"]
            )

            if progress_callback:
                progress_callback(0, total, f"Extracted question for problem {problem_number}")

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

                    # Decipher handwriting
                    student_answer = self.decipher_handwriting(problem["image_data"])

                    # Grade the answer with examples
                    score, feedback = self.grade_problem(
                        question_text, student_answer, max_points, grading_examples=grading_examples
                    )

                    # Update problem with AI suggestion (score and feedback ready for instructor review)
                    cursor.execute("""
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
