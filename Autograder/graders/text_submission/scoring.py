"""
Score calculation and rubric feedback generation for text submissions.

This module provides:
- ScoreCalculator: Normalizes scores and calculates totals
- RubricGenerator: Creates student-facing rubric feedback

These classes are designed to be subclassed for different grading rubrics.
"""

from typing import Dict, List


# Default configuration constants
DEFAULT_WORD_THRESHOLD = 250
DEFAULT_RUBRIC_TOTAL = 10

# Default rubric component points
ENGAGEMENT_POINTS = 4      # Effort to process and explain material
LENGTH_POINTS = 2          # Meeting word count requirement (calculated locally)
RELEVANCE_POINTS = 2       # Coverage of class topics
EXPLANATION_QUALITY_POINTS = 2  # Depth of explanation


class ScoreCalculator:
    """
    Encapsulates score normalization and total-grade calculation.

    This calculator is designed for rubric-based grading where different
    components contribute to a total score. Subclass this to customize
    the scoring algorithm for different assignment types.

    Default rubric (10 points total):
    - Engagement (4 pts): From AI grading
    - Length (2 pts): Calculated locally from word count
    - Relevance (2 pts): From AI grading
    - Explanation Quality (2 pts): From AI grading
    """

    def __init__(self,
                 *,
                 word_threshold: int = DEFAULT_WORD_THRESHOLD,
                 length_points: int = LENGTH_POINTS):
        """
        Initialize the score calculator.

        Args:
            word_threshold: Minimum words required for full length points
            length_points: Points awarded for meeting word threshold
        """
        self.word_threshold = word_threshold
        self.length_points = length_points

    def apply_scores(self, result: Dict, *, word_count: int,
                     student_name: str) -> Dict:
        """
        Apply local score calculations and compute total grade.

        This method adds length_score based on word count and calculates
        the total_grade from all rubric components.

        Args:
            result: Grading result dict from AI (must contain engagement_score,
                    relevance_score, explanation_quality_score)
            word_count: Actual word count of the submission
            student_name: Student's display name

        Returns:
            Updated result dict with length_score, accurate_word_count,
            student_name, and total_grade added
        """
        # Length score is computed locally from measured word count
        result["length_score"] = (
            self.length_points if word_count >= self.word_threshold else 0)
        result["accurate_word_count"] = word_count
        result["student_name"] = student_name

        # Sum all rubric components
        total_grade = (int(result.get("engagement_score", 0)) +
                       int(result.get("length_score", 0)) +
                       int(result.get("relevance_score", 0)) +
                       int(result.get("explanation_quality_score", 0)))
        result["total_grade"] = total_grade
        return result

    @staticmethod
    def needs_support(result: Dict) -> bool:
        """
        Check if a student needs additional support based on grading result.

        Args:
            result: Grading result dict

        Returns:
            True if the student is flagged as needing support
        """
        value = result.get("needs_support", False)
        if isinstance(value, str):
            value = value.lower() in ['true', '1', 'yes']
        return bool(value)


class RubricGenerator:
    """
    Generates student-facing rubric feedback from grading results.

    This class creates a formatted feedback string that shows the student
    their scores broken down by rubric component, plus any AI-generated
    feedback and topic review suggestions.

    Subclass this to customize the feedback format for different assignment types.
    """

    def __init__(self,
                 *,
                 engagement_points: int = ENGAGEMENT_POINTS,
                 length_points: int = LENGTH_POINTS,
                 relevance_points: int = RELEVANCE_POINTS,
                 explanation_quality_points: int = EXPLANATION_QUALITY_POINTS,
                 rubric_total: int = DEFAULT_RUBRIC_TOTAL):
        """
        Initialize the rubric generator.

        Args:
            engagement_points: Maximum points for engagement
            length_points: Maximum points for length
            relevance_points: Maximum points for relevance
            explanation_quality_points: Maximum points for explanation quality
            rubric_total: Total possible points on the rubric
        """
        self.engagement_points = engagement_points
        self.length_points = length_points
        self.relevance_points = relevance_points
        self.explanation_quality_points = explanation_quality_points
        self.rubric_total = rubric_total

    def generate(self, result: Dict) -> str:
        """
        Generate formatted rubric feedback for a student.

        Args:
            result: Grading result dict containing scores and feedback

        Returns:
            Formatted feedback string
        """
        engagement_score = result.get('engagement_score', 0)
        length_score = result.get('length_score', 0)
        relevance_score = result.get('relevance_score', 0)
        quality_score = result.get('explanation_quality_score', 0)
        total_score = result.get('total_grade', 0)
        word_count = result.get('accurate_word_count', 0)
        ai_feedback = result.get('feedback', '')
        topics_needing_review = result.get('topics_needing_review', [])

        feedback_lines = [
            "Study Notes Feedback",
            "=" * 50,
            "",
            "GRADE BREAKDOWN:",
            f"- Engagement ({self.engagement_points} pts): {engagement_score}/{self.engagement_points} - Effort to process and explain material",
            f"- Length ({self.length_points} pts): {length_score}/{self.length_points} - {'Met 250+ word requirement' if length_score == self.length_points else 'Under 250 words required'}",
            f"- Relevance ({self.relevance_points} pts): {relevance_score}/{self.relevance_points} - Coverage of class topics",
            f"- Explanation Quality ({self.explanation_quality_points} pts): {quality_score}/{self.explanation_quality_points} - Depth of explanation",
            "",
            f"TOTAL SCORE: {total_score}/{self.rubric_total} ({(total_score/self.rubric_total)*100:.0f}%)",
            f"Word Count: {word_count} words"
        ]

        if topics_needing_review:
            feedback_lines.append("")
            feedback_lines.append("TOPICS TO REVIEW:")
            for topic in topics_needing_review:
                feedback_lines.append(f"- {topic}")

        feedback_lines.extend(["", "FEEDBACK:", ai_feedback])
        return "\n".join(feedback_lines)
