"""
Quiz grader implementation.

Handles grading of Canvas quiz submissions by analyzing student responses
and generating feedback reports.
"""
from typing import Dict, Any
import json

from Autograder.grader import Grader
from Autograder.registry import GraderRegistry
from Autograder.lms_interface.classes import Feedback, Submission, QuizSubmission

import logging

log = logging.getLogger(__name__)


@GraderRegistry.register("QuizGrader")
class QuizGrader(Grader):
  """
    Grader for Canvas quiz submissions.

    Analyzes student responses to quiz questions and generates
    detailed feedback reports on performance.
    """

  def can_grade_submission(self, submission: Submission) -> bool:
    """
        Quiz graders can only grade QuizSubmission objects that have responses.
        """
    return isinstance(submission, QuizSubmission) and bool(
      submission.responses)

  def execute_grading(self, submission: QuizSubmission, *args,
                      **kwargs) -> Dict[str, Any]:
    """
        Analyze the quiz submission and extract grading information.

        :param submission: QuizSubmission object with student responses
        :return: Dictionary containing analysis results
        """
    if not isinstance(submission, QuizSubmission):
      raise ValueError("QuizGrader can only grade QuizSubmission objects")

    results = {
      'total_questions': len(submission.responses),
      'responses_analyzed': 0,
      'question_analysis': {},
      'score_breakdown': {},
      'response_summary': []
    }

    total_points_earned = 0
    total_points_possible = 0

    # Analyze each question response
    for question_id, response_data in submission.responses.items():
      question_info = submission.get_question(question_id)

      if question_info:
        question_analysis = {
          'question_id': question_id,
          'question_type': response_data.get('question_type', 'unknown'),
          'question_text': question_info.get('question_text', ''),
          'student_answer': response_data.get('answer', ''),
          'points_earned': response_data.get('points', 0),
          'points_possible': question_info.get('points_possible', 0),
          'correct': response_data.get('correct', False)
        }

        results['question_analysis'][question_id] = question_analysis
        results['response_summary'].append({
          'question':
          f"Q{question_id}",
          'type':
          question_analysis['question_type'],
          'points':
          f"{question_analysis['points_earned']}/{question_analysis['points_possible']}",
          'correct':
          question_analysis['correct']
        })

        total_points_earned += question_analysis['points_earned']
        total_points_possible += question_analysis['points_possible']
        results['responses_analyzed'] += 1

    # Calculate overall score breakdown
    percentage = (total_points_earned / total_points_possible *
                  100) if total_points_possible > 0 else 0
    results['score_breakdown'] = {
      'points_earned': total_points_earned,
      'points_possible': total_points_possible,
      'percentage': percentage
    }

    log.debug(
      f"Quiz analysis completed: {results['responses_analyzed']} responses analyzed, "
      f"{percentage:.1f}% score")

    return results

  def score_grading(self, execution_results: Dict[str, Any], *args,
                    **kwargs) -> Feedback:
    """
        Generate feedback based on quiz analysis results.

        :param execution_results: Results from execute_grading
        :return: Feedback object with score and comments
        """
    score_breakdown = execution_results['score_breakdown']
    response_summary = execution_results['response_summary']

    percentage = score_breakdown['percentage']

    # Generate detailed feedback comments
    comments_lines = [
      f"Quiz Results Summary", f"=" * 40,
      f"Total Score: {score_breakdown['points_earned']}/{score_breakdown['points_possible']} ({percentage:.1f}%)",
      f"Questions Answered: {execution_results['responses_analyzed']}/{execution_results['total_questions']}",
      "", "Question-by-Question Breakdown:", "-" * 30
    ]

    # Add summary of each question
    for response in response_summary:
      status = "✓ Correct" if response['correct'] else "✗ Incorrect"
      comments_lines.append(
        f"{response['question']} ({response['type']}): {response['points']} - {status}"
      )

    # Add detailed analysis if requested
    if kwargs.get('detailed_feedback', True):
      comments_lines.extend(["", "Detailed Analysis:", "-" * 15])

      for question_id, analysis in execution_results[
          'question_analysis'].items():
        comments_lines.extend([
          f"Question {question_id} ({analysis['question_type']}):",
          f"  Points: {analysis['points_earned']}/{analysis['points_possible']}",
          f"  Student Answer: {str(analysis['student_answer'])[:100]}{'...' if len(str(analysis['student_answer'])) > 100 else ''}",
          ""
        ])

    # Add performance insights
    if percentage >= 90:
      comments_lines.append(
        "Excellent work! You demonstrated strong mastery of the material.")
    elif percentage >= 80:
      comments_lines.append(
        "Good performance! Review any missed questions for improvement.")
    elif percentage >= 70:
      comments_lines.append(
        "Satisfactory work. Consider reviewing the material for better understanding."
      )
    else:
      comments_lines.append(
        "Please review the material and consider additional practice.")

    feedback_text = "\n".join(comments_lines)

    return Feedback(
      percentage_score=percentage,
      comments=feedback_text,
      attachments=[]  # Quiz grading typically doesn't include file attachments
    )

  def assignment_needs_preparation(self) -> bool:
    """Quiz grading doesn't require preparation like file-based assignments"""
    return False

  def prepare(self, *args, **kwargs) -> None:
    """No preparation needed for quiz grading"""
    pass

  def finalize(self, *args, **kwargs) -> None:
    """No finalization needed for quiz grading"""
    pass
