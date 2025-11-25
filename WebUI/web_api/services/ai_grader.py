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

from ..repositories import ProblemRepository, ProblemMetadataRepository, SubmissionRepository

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
      "Return only the question text without any additional commentary.")

    attachments = [("png", image_base64)]
    question_text, usage = self.ai_helper.query_ai(message,
                                                   attachments,
                                                   max_response_tokens=2000)

    log.info(
      f"Extracted question text ({usage['total_tokens']} tokens): {question_text[:100]}..."
    )
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
      "Return only the handwritten text without any additional commentary.")

    attachments = [("png", image_base64)]
    handwriting_text, usage = self.ai_helper.query_ai(message,
                                                      attachments,
                                                      max_response_tokens=2000)

    log.info(
      f"Deciphered handwriting ({usage['total_tokens']} tokens): {handwriting_text[:100]}..."
    )
    return handwriting_text.strip()

  def generate_rubric(self,
                      question_text: str,
                      max_points: float,
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
        examples_section += (f"Example {i}:\n"
                             f"Student Answer: {example['answer']}\n"
                             f"Your Score: {example['score']}/{max_points}\n"
                             f"Your Feedback: {example['feedback']}\n\n")

    message = (
      f"Create a grading rubric for this {max_points}-point exam problem.\n\n"
      f"Question:\n{question_text}"
      f"{examples_section}\n\n"
      f"Requirements:\n"
      f"- Break down into key components with integer point values (sum = {max_points})\n"
      f"- Be concise and specific (brief descriptions, no extra commentary)\n"
      f"- Align with the grading standards in the examples above\n"
      f"- Return ONLY valid JSON (no markdown, no code blocks):\n\n"
      f"{{\n"
      f'  "items": [\n'
      f'    {{"points": 2, "description": "Correct identification of X"}},\n'
      f'    {{"points": 3, "description": "Shows calculation for Y"}}\n'
      f"  ]\n"
      f"}}")

    response, usage = self.ai_helper.query_ai(message, [],
                                              max_response_tokens=2000)

    log.info(
      f"Generated rubric ({usage['total_tokens']} tokens): {response[:200]}..."
    )

    # Parse and validate JSON, then re-serialize to ensure clean format
    import json
    try:
      # Try to extract JSON if the AI wrapped it in markdown code blocks
      response_clean = response.strip()
      if response_clean.startswith('```'):
        # Extract content between code fences
        lines = response_clean.split('\n')
        json_lines = []
        in_code_block = False
        for line in lines:
          if line.startswith('```'):
            in_code_block = not in_code_block
            continue
          if in_code_block:
            json_lines.append(line)
        response_clean = '\n'.join(json_lines)

      rubric_data = json.loads(response_clean)

      # Validate structure
      if 'items' not in rubric_data or not isinstance(rubric_data['items'],
                                                      list):
        raise ValueError("Invalid rubric structure: missing 'items' array")

      # Return clean JSON string
      return json.dumps(rubric_data)

    except json.JSONDecodeError as e:
      log.error(f"Failed to parse rubric JSON: {e}. Raw response: {response}")
      # Fallback: return a simple valid JSON structure
      return json.dumps({
        "items": [{
          "points": max_points,
          "description": "Complete and correct answer"
        }]
      })

  def grade_problem(self,
                    question_text: str,
                    student_answer: str,
                    max_points: float,
                    grading_examples: List[Dict] = None,
                    rubric: str = None) -> Tuple[int, str]:
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
      # Try to parse as JSON, fall back to treating as text
      import json
      try:
        rubric_data = json.loads(rubric)
        if 'items' in rubric_data and isinstance(rubric_data['items'], list):
          # Convert JSON rubric to readable format
          rubric_text = "Grading Rubric:\n"
          for item in rubric_data['items']:
            points = item.get('points', 0)
            description = item.get('description', '')
            rubric_text += f"- {description} ({points} points)\n"
          rubric_section = f"\n\n{rubric_text}\nPlease follow this rubric when grading.\n"
        else:
          rubric_section = f"\n\nGrading Rubric:\n{rubric}\n\nPlease follow this rubric when grading.\n"
      except json.JSONDecodeError:
        # Not JSON, treat as plain text
        rubric_section = f"\n\nGrading Rubric:\n{rubric}\n\nPlease follow this rubric when grading.\n"

    # Build few-shot examples section
    examples_section = ""
    if grading_examples and len(grading_examples) > 0:
      examples_section = "\n\nHere are examples of how you previously graded similar answers to this question:\n\n"
      for i, example in enumerate(grading_examples, 1):
        examples_section += (f"Example {i}:\n"
                             f"Student Answer: {example['answer']}\n"
                             f"Your Score: {example['score']}/{max_points}\n"
                             f"Your Feedback: {example['feedback']}\n\n")
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
      f"IMPORTANT: The feedback should be concise, direct, constructive, and helpful for the student to understand what they did well and what could be improved.\n"
      f"IMPORTANT: If the answer is blank, minimal, or shows no understanding, score it 0 and provide constructive feedback on how to approach the problem correctly. Focus on what a correct answer would include.\n\n"
      f"Format your response as:\n"
      f"SCORE: [integer]\n"
      f"FEEDBACK: [clear and constructive feedback for the student]")

    response, usage = self.ai_helper.query_ai(message, [],
                                              max_response_tokens=1000)

    log.info(
      f"AI grading response ({usage['total_tokens']} tokens): {response[:200]}..."
    )

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
      log.warning(
        f"No score found in AI response, defaulting to 0. Response: {response[:200]}"
      )

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
    metadata_repo = ProblemMetadataRepository()

    # Check if question already extracted
    question_text = metadata_repo.get_question_text(session_id, problem_number)

    if question_text:
      log.info(f"Using cached question text for problem {problem_number}")
      return question_text

    # Extract question text
    log.info(f"Extracting question text for problem {problem_number}")
    question_text = self.extract_question_text(sample_image_base64)

    # Store in metadata
    metadata_repo.upsert_question_text(session_id, problem_number, question_text)

    return question_text

  def get_grading_examples(self,
                           session_id: int,
                           problem_number: int,
                           limit: int = 3) -> List[Dict]:
    """Fetch examples of previously graded submissions for few-shot prompting.

        Args:
            session_id: Grading session ID
            problem_number: Problem number
            limit: Maximum number of examples to return

        Returns:
            List of dicts with 'answer', 'score', 'feedback'
        """
    examples = []
    problem_repo = ProblemRepository()
    submission_repo = SubmissionRepository()

    # Get graded problems (exclude blanks and problems without feedback)
    rows = problem_repo.get_grading_examples(session_id, problem_number, limit)

    if not rows:
      log.info(f"No graded examples found for problem {problem_number}")
      return examples

    log.info(
      f"Found {len(rows)} graded examples for problem {problem_number}, deciphering..."
    )

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

          region_data = row["region_coords"]

          # Get PDF data from submission
          submission = submission_repo.get_by_id(row["submission_id"])

          if submission and submission.exam_pdf_data:
            # Extract region from PDF
            pdf_bytes = base64.b64decode(submission.exam_pdf_data)
            pdf_document = fitz.open("pdf", pdf_bytes)
            page = pdf_document[region_data["page_number"]]

            region = fitz.Rect(0, region_data["region_y_start"],
                               page.rect.width, region_data["region_y_end"])

            # Extract region as new PDF page
            problem_pdf = fitz.open()
            problem_page = problem_pdf.new_page(width=region.width,
                                                height=region.height)
            problem_page.show_pdf_page(problem_page.rect,
                                       pdf_document,
                                       region_data["page_number"],
                                       clip=region)

            # Convert to PNG
            pix = problem_page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            image_data = base64.b64encode(img_bytes).decode("utf-8")

            # Cleanup
            problem_pdf.close()
            pdf_document.close()

        if not image_data:
          log.warning(
            f"No image data available for problem {row['id']}, skipping")
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

  def autograde_problem(self,
                        session_id: int,
                        problem_number: int,
                        max_points: float = None,
                        progress_callback=None) -> Dict:
    """Autograde all ungraded submissions for a specific problem number.

        Args:
            session_id: Grading session ID
            problem_number: Problem number to grade
            max_points: Maximum points (optional, will query DB if not provided)
            progress_callback: Optional callback function(current, total, message)

        Returns:
            Dictionary with grading results
        """
    metadata_repo = ProblemMetadataRepository()
    problem_repo = ProblemRepository()

    # If max_points not provided, try to get from database
    if max_points is None:
      # Get max points for this problem - first check metadata
      max_points = metadata_repo.get_max_points(session_id, problem_number)

      if not max_points:
        # Fall back to max_points from problems table
        sample_problem = problem_repo.get_sample_for_problem_number(
          session_id, problem_number)

        if sample_problem and sample_problem.max_points:
          max_points = sample_problem.max_points

          # Save to metadata for future use
          metadata_repo.upsert_max_points(session_id, problem_number,
                                          max_points)

      if not max_points:
        raise ValueError(f"Max points not set for problem {problem_number}")

    # Get all ungraded problems for this problem number (include blanks for feedback)
    problems = problem_repo.get_ungraded_for_problem_number(
      session_id, problem_number)
    total = len(problems)

    if total == 0:
      return {"graded": 0, "message": "No ungraded problems found"}

    log.info(
      f"Autograding {total} problems for problem number {problem_number}")

    submission_repo = SubmissionRepository()

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

      region_data = first_problem["region_coords"]
      submission = submission_repo.get_by_id(first_problem["submission_id"])

      if submission and submission.exam_pdf_data:
        pdf_bytes = base64.b64decode(submission.exam_pdf_data)
        pdf_document = fitz.open("pdf", pdf_bytes)
        page = pdf_document[region_data["page_number"]]
        region = fitz.Rect(0, region_data["region_y_start"], page.rect.width,
                           region_data["region_y_end"])

        problem_pdf = fitz.open()
        problem_page = problem_pdf.new_page(width=region.width,
                                            height=region.height)
        problem_page.show_pdf_page(problem_page.rect,
                                   pdf_document,
                                   region_data["page_number"],
                                   clip=region)

        pix = problem_page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        first_image_data = base64.b64encode(img_bytes).decode("utf-8")

        problem_pdf.close()
        pdf_document.close()

    question_text = self.get_or_extract_question(session_id, problem_number,
                                                 first_image_data)

    if progress_callback:
      progress_callback(0, total,
                        f"Extracted question for problem {problem_number}")

    # Get rubric from metadata if available
    rubric = metadata_repo.get_grading_rubric(session_id, problem_number)
    if rubric:
      log.info(f"Using rubric for problem {problem_number}")

      # Get grading examples for few-shot prompting
      if progress_callback:
        progress_callback(
          0, total, f"Fetching grading examples for problem {problem_number}")

      grading_examples = self.get_grading_examples(session_id,
                                                   problem_number,
                                                   limit=3)

      if progress_callback:
        if len(grading_examples) > 0:
          progress_callback(0, total,
                            f"Found {len(grading_examples)} grading examples")
        else:
          progress_callback(0, total,
                            f"No grading examples found, proceeding without")

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

            region_data = problem["region_coords"]

            # Get submission PDF data
            submission = submission_repo.get_by_id(problem["submission_id"])

            if submission and submission.exam_pdf_data:
              pdf_bytes = base64.b64decode(submission.exam_pdf_data)
              pdf_document = fitz.open("pdf", pdf_bytes)
              page = pdf_document[region_data["page_number"]]
              region = fitz.Rect(0, region_data["region_y_start"],
                                 page.rect.width,
                                 region_data["region_y_end"])

              problem_pdf = fitz.open()
              problem_page = problem_pdf.new_page(width=region.width,
                                                  height=region.height)
              problem_page.show_pdf_page(problem_page.rect,
                                         pdf_document,
                                         region_data["page_number"],
                                         clip=region)

              pix = problem_page.get_pixmap(dpi=150)
              img_bytes = pix.tobytes("png")
              image_data = base64.b64encode(img_bytes).decode("utf-8")

              problem_pdf.close()
              pdf_document.close()

          if not image_data:
            log.warning(
              f"No image data available for problem {problem['id']}, skipping")
            continue

          # For blank submissions, use placeholder text instead of deciphering
          if problem["is_blank"]:
            student_answer = "[No answer provided]"
            log.info(
              f"Problem {problem['id']} marked as blank, skipping handwriting extraction"
            )
          else:
            # Decipher handwriting for non-blank submissions
            student_answer = self.decipher_handwriting(image_data)

          # Grade the answer with rubric and examples
          score, feedback = self.grade_problem(
            question_text,
            student_answer,
            max_points,
            grading_examples=grading_examples,
            rubric=rubric)

          # Update problem with AI suggestion (score and feedback ready for instructor review)
          problem_repo.update_ai_grade(problem["id"], score, feedback)

          graded_count += 1
          log.info(f"AI graded problem {problem['id']}: {score}/{max_points}")

        except Exception as e:
          log.error(f"Failed to autograde problem {problem['id']}: {e}",
                    exc_info=True)
          continue

      if progress_callback:
        progress_callback(total, total,
                          f"Completed autograding {graded_count} problems")

      return {
        "graded": graded_count,
        "total": total,
        "question_text": question_text,
        "message": f"AI graded {graded_count}/{total} problems"
      }
