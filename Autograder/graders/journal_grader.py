 """
Journal grader implementation.

Handles grading of text-based journal submissions by checking length requirements
and analyzing content for topic coverage.
"""
from typing import Dict, Any
import logging

from Autograder.grader import Grader
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback, Submission, TextSubmission

log = logging.getLogger(__name__)


class ExternalAnalyzer:
    """
    Placeholder for external content analysis.

    This will eventually integrate with ai_helper or other analysis systems
    to check if journal content covers required topics.
    """

    @staticmethod
    def analyze_content(text: str, analysis_prompt: str = None) -> Dict[str, Any]:
        """
        Noop external analysis endpoint.

        :param text: The student's journal text
        :param analysis_prompt: Instructions for what to analyze
        :return: Analysis results dictionary
        """
        # Placeholder implementation - returns success for any non-empty text
        word_count = len(text.split()) if text else 0

        # Simple mock analysis based on length
        if word_count == 0:
            coverage_score = 0.0
            topics_covered = []
            analysis_notes = "No content submitted."
        elif word_count < 50:
            coverage_score = 0.3
            topics_covered = ["minimal_effort"]
            analysis_notes = "Very brief submission - may not cover required depth."
        elif word_count < 150:
            coverage_score = 0.7
            topics_covered = ["basic_concepts", "personal_reflection"]
            analysis_notes = "Good length. Appears to cover basic concepts."
        else:
            coverage_score = 0.9
            topics_covered = ["basic_concepts", "personal_reflection", "detailed_analysis"]
            analysis_notes = "Comprehensive submission with good detail."

        return {
            'coverage_score': coverage_score,  # 0.0 to 1.0
            'topics_covered': topics_covered,
            'analysis_notes': analysis_notes,
            'word_count_analyzed': word_count
        }


@GraderRegistry.register("JournalGrader")
class JournalGrader(Grader):
    """
    Grader for text-based journal submissions.

    Checks length requirements and analyzes content for topic coverage
    using external analysis systems.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Configuration parameters
        self.min_words = kwargs.get('min_words', 200)
        self.max_words = kwargs.get('max_words', None)  # No max by default
        self.min_paragraphs = kwargs.get('min_paragraphs', 2)
        self.analysis_prompt = kwargs.get('analysis_prompt',
            "Analyze if this journal entry demonstrates understanding of course concepts.")
        self.length_weight = kwargs.get('length_weight', 0.3)  # 30% of grade for length
        self.content_weight = kwargs.get('content_weight', 0.7)  # 70% of grade for content

    def can_grade_submission(self, submission: Submission) -> bool:
        """
        Journal graders can only grade TextSubmission objects.
        """
        return isinstance(submission, TextSubmission)

    def execute_grading(self, submission: TextSubmission, *args, **kwargs) -> Dict[str, Any]:
        """
        Analyze the journal submission for length and content quality.

        :param submission: TextSubmission object with student's journal text
        :return: Dictionary containing analysis results
        """
        if not isinstance(submission, TextSubmission):
            raise ValueError("JournalGrader can only grade TextSubmission objects")

        text = submission.get_text()
        word_count = submission.get_word_count()
        paragraph_count = submission.get_paragraph_count()
        character_count = submission.get_character_count()

        # Length requirement analysis
        length_analysis = self._analyze_length_requirements(word_count, paragraph_count)

        # External content analysis
        content_analysis = ExternalAnalyzer.analyze_content(text, self.analysis_prompt)

        results = {
            'text_stats': {
                'word_count': word_count,
                'character_count': character_count,
                'paragraph_count': paragraph_count,
                'text_length': len(text)
            },
            'length_analysis': length_analysis,
            'content_analysis': content_analysis,
            'overall_metrics': {
                'length_score': length_analysis['score'],
                'content_score': content_analysis['coverage_score'],
                'combined_score': (length_analysis['score'] * self.length_weight +
                                 content_analysis['coverage_score'] * self.content_weight)
            }
        }

        log.debug(f"Journal analysis completed: {word_count} words, "
                 f"length score: {length_analysis['score']:.2f}, "
                 f"content score: {content_analysis['coverage_score']:.2f}")

        return results

    def _analyze_length_requirements(self, word_count: int, paragraph_count: int) -> Dict[str, Any]:
        """
        Analyze if submission meets length requirements.

        :param word_count: Number of words in submission
        :param paragraph_count: Number of paragraphs in submission
        :return: Length analysis results
        """
        issues = []
        score = 1.0  # Start with full points

        # Check minimum word count
        if word_count < self.min_words:
            word_deficit = self.min_words - word_count
            word_penalty = min(0.5, word_deficit / self.min_words)  # Cap penalty at 50%
            score -= word_penalty
            issues.append(f"Below minimum word count ({word_count}/{self.min_words} words)")

        # Check maximum word count if specified
        if self.max_words and word_count > self.max_words:
            word_excess = word_count - self.max_words
            word_penalty = min(0.2, word_excess / self.max_words)  # Cap penalty at 20%
            score -= word_penalty
            issues.append(f"Exceeds maximum word count ({word_count}/{self.max_words} words)")

        # Check minimum paragraph count
        if paragraph_count < self.min_paragraphs:
            paragraph_penalty = 0.2  # 20% penalty for insufficient paragraphs
            score -= paragraph_penalty
            issues.append(f"Insufficient paragraphs ({paragraph_count}/{self.min_paragraphs} paragraphs)")

        # Ensure score doesn't go below 0
        score = max(0.0, score)

        return {
            'score': score,
            'meets_requirements': score >= 0.8,  # 80% threshold for "meets requirements"
            'issues': issues,
            'word_count_status': 'sufficient' if word_count >= self.min_words else 'insufficient',
            'paragraph_count_status': 'sufficient' if paragraph_count >= self.min_paragraphs else 'insufficient'
        }

    def score_grading(self, execution_results: Dict[str, Any], *args, **kwargs) -> Feedback:
        """
        Generate feedback based on journal analysis results.

        :param execution_results: Results from execute_grading
        :return: Feedback object with score and comments
        """
        stats = execution_results['text_stats']
        length_analysis = execution_results['length_analysis']
        content_analysis = execution_results['content_analysis']
        metrics = execution_results['overall_metrics']

        # Calculate final score (0-100 scale)
        final_score = metrics['combined_score'] * 100

        # Generate detailed feedback
        feedback_lines = [
            "Journal Submission Feedback",
            "=" * 40,
            "",
            "Length Analysis:",
            f"• Word count: {stats['word_count']} words (minimum: {self.min_words})",
            f"• Paragraph count: {stats['paragraph_count']} paragraphs (minimum: {self.min_paragraphs})",
            f"• Length score: {length_analysis['score']:.1%}",
            ""
        ]

        # Add length issues if any
        if length_analysis['issues']:
            feedback_lines.extend([
                "Length Requirements Issues:",
                *[f"• {issue}" for issue in length_analysis['issues']],
                ""
            ])

        # Add content analysis
        feedback_lines.extend([
            "Content Analysis:",
            f"• Topic coverage score: {content_analysis['coverage_score']:.1%}",
            f"• Topics identified: {', '.join(content_analysis['topics_covered'])}",
            f"• Analysis notes: {content_analysis['analysis_notes']}",
            "",
            "Overall Results:",
            f"• Length component ({self.length_weight:.0%}): {metrics['length_score']:.1%}",
            f"• Content component ({self.content_weight:.0%}): {metrics['content_score']:.1%}",
            f"• Final score: {final_score:.1f}/100",
            ""
        ])

        # Add performance guidance
        if final_score >= 90:
            feedback_lines.append("Excellent work! Your journal entry meets all requirements and demonstrates strong engagement with the material.")
        elif final_score >= 80:
            feedback_lines.append("Good work! Your journal entry meets most requirements. Consider addressing any noted issues for future submissions.")
        elif final_score >= 70:
            feedback_lines.append("Satisfactory work. Please review the requirements and ensure future submissions address all criteria.")
        else:
            feedback_lines.append("This submission needs improvement. Please review the requirements and resubmit if possible.")

        feedback_text = "\n".join(feedback_lines)

        return Feedback(
            score=final_score,
            comments=feedback_text,
            attachments=[]
        )

    def assignment_needs_preparation(self) -> bool:
        """Journal grading doesn't require preparation like file-based assignments"""
        return False

    def prepare(self, *args, **kwargs) -> None:
        """No preparation needed for journal grading"""
        pass

    def finalize(self, *args, **kwargs) -> None:
        """No finalization needed for journal grading"""
        pass