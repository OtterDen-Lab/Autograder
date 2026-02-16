"""
Workflow processors and analyzers for text submission grading.

This module contains the classes that orchestrate the 3-phase grading pipeline:
- BatchProcessor: Coordinates the entire grading workflow
- AggregateAnalyzer: Phase 1 - Analyzes all submissions for patterns
- IndividualGradingProcessor: Phase 2 - Grades each submission
- QuestionConsolidator: Phase 2.5 - Groups similar student questions
- IndividualSubmissionAnalyzer: Grades a single submission via AI

These processors use dependency injection (receiving a grader instance)
to allow customization of prompts and scoring through grader subclasses.
"""

import logging
from typing import Dict, List, TYPE_CHECKING

from Autograder.ai_orchestrator import (
    ProviderFallbackOrchestrator,
    parse_anthropic_json_payload,
    query_anthropic_text,
    query_anthropic_structured,
    query_openai_structured,
)

from .prompts import DEFAULT_MAX_WORDS, DEFAULT_MAX_CHARACTERS

if TYPE_CHECKING:
    from .base import BaseTextSubmissionGrader
    from Autograder.assignment import Assignment

log = logging.getLogger(__name__)


class BatchProcessor:
    """
    Coordinates truncation and the 3-phase text grading pipeline.

    This is the main orchestrator that runs the complete grading workflow:
    1. Truncates submissions to reasonable length
    2. Redacts PII before sending to AI
    3. Runs Phase 1 (aggregate analysis)
    4. Runs Phase 2 (individual grading)
    5. Applies grades to submissions
    6. Runs Phase 3 (report generation)
    """

    def __init__(self, grader: "BaseTextSubmissionGrader"):
        """
        Initialize the batch processor.

        Args:
            grader: The grader instance to coordinate
        """
        self.grader = grader

    def run(self, assignment: "Assignment", *, assignment_name: str,
            course_name: str) -> bool:
        """
        Run the complete grading pipeline for an assignment.

        Args:
            assignment: The assignment to grade
            assignment_name: Name of the assignment for logging/context
            course_name: Name of the course for context

        Returns:
            True if grading completed successfully, False if no submissions
        """
        submission_data = assignment.get_submission_data()

        if not submission_data:
            log.info(
                f"No submissions to grade for '{assignment_name}' - assignment may be unlocked"
            )
            return False

        truncated_texts, truncation_count, redaction_count = self._truncate_batch(
            submission_data)

        if truncation_count > 0:
            log.info(
                f"Truncated {truncation_count} submission(s) exceeding {DEFAULT_MAX_WORDS} words or {DEFAULT_MAX_CHARACTERS} characters"
            )
        if redaction_count > 0:
            log.info(
                f"Redacted potential PII in {redaction_count} submission(s) before LLM analysis"
            )

        log.info(
            f"Starting 3-phase grading for '{assignment_name}' with {len(submission_data)} submissions"
        )

        # Phase 1: Aggregate Analysis
        log.info("Phase 1/3: Aggregate analysis")
        self.grader.aggregate_results = self.grader.phase_1_aggregate_analysis(
            truncated_texts, assignment_name, course_name)

        # Phase 2: Individual Grading
        log.info("Phase 2/3: Individual grading")
        self.grader.individual_results = self.grader.phase_2_individual_grading(
            submission_data, self.grader.core_topics)

        # Apply grades to submissions
        self.grader._apply_grades_to_submissions(assignment.submissions,
                                                 self.grader.individual_results)

        # Phase 3: Report Generation
        log.info("Phase 3/3: Report generation")
        self.grader.phase_3_generate_report(self.grader.aggregate_results,
                                            self.grader.individual_results)
        return True

    def _truncate_batch(self, submission_data: List[Dict]
                        ) -> tuple[List[str], int, int]:
        """
        Truncate and redact all submissions in the batch.

        Args:
            submission_data: List of submission dictionaries

        Returns:
            Tuple of (truncated_texts, truncation_count, redaction_count)
        """
        truncated_texts = []
        truncation_count = 0
        redaction_count = 0

        for submission_info in submission_data:
            original_text = submission_info.get('text', '')
            truncated, was_truncated = self.grader._truncate_submission_text(
                original_text)
            prepared_text = truncated
            if was_truncated:
                submission_info['was_truncated'] = True
                truncation_count += 1

            if hasattr(self.grader, "_redact_submission_text_for_ai"):
                prepared_text, redaction_meta = self.grader._redact_submission_text_for_ai(
                    prepared_text,
                    student_name=submission_info.get("student_name"),
                    student_id=submission_info.get("student_id"))
                if redaction_meta.get("total_replacements", 0) > 0:
                    submission_info["was_redacted"] = True
                    redaction_count += 1
            submission_info['text'] = prepared_text
            if prepared_text:
                truncated_texts.append(prepared_text)

        return truncated_texts, truncation_count, redaction_count


class AggregateAnalyzer:
    """
    Phase 1: Aggregate analysis with provider fallback.

    Analyzes all submissions together to identify:
    - Core topics covered this week
    - Related topics (tangential but valid)
    - Off-topic indicators
    - Common themes and misconceptions
    - Student questions
    """

    def __init__(self, grader: "BaseTextSubmissionGrader"):
        """
        Initialize the aggregate analyzer.

        Args:
            grader: The grader instance for prompt building and token tracking
        """
        self.grader = grader

    def analyze(self, submission_texts: List[str], assignment_name: str,
                course_name: str = "Unknown Course") -> Dict:
        """
        Perform aggregate analysis on all submissions.

        Args:
            submission_texts: List of all submission texts
            assignment_name: Name of the assignment
            course_name: Name of the course

        Returns:
            Dictionary containing aggregate analysis results
        """
        log.info(
            f"Analyzing {len(submission_texts)} submissions for aggregate insights..."
        )

        if not submission_texts:
            log.warning("No submissions to analyze")
            return {
                "core_topics": [],
                "common_themes": "",
                "key_insights": "",
                "commonly_misunderstood_topics": [],
                "misconception_details": "",
                "teaching_feedback": "",
                "student_questions": []
            }

        prompt = self.grader._build_aggregate_analysis_prompt(
            submission_texts, assignment_name, course_name)
        orchestrator = ProviderFallbackOrchestrator(self.grader.prefer_anthropic)

        def _run_anthropic() -> Dict:
            operation = "Phase 1 - Aggregate Analysis (Anthropic)"
            if not self.grader.prefer_anthropic:
                operation = "Phase 1 - Aggregate Analysis (Anthropic fallback)"
            analysis_text, usage = query_anthropic_text(
                prompt, tier=self.grader.phase1_tier, max_response_tokens=2000)
            self.grader._track_token_usage(usage, operation)

            result = parse_anthropic_json_payload(analysis_text,
                                                  schema_name="aggregate_analysis")
            if result is None:
                # Keep text fallback behavior when Anthropic returns no parseable JSON
                result = {
                    "common_themes": analysis_text,
                    "commonly_misunderstood_topics": [],
                    "misconception_details": "",
                    "key_insights": "",
                    "teaching_feedback": "",
                    "core_topics": [],
                    "related_topics": [],
                    "off_topic_indicators": [],
                    "student_questions": []
                }

            self.grader._store_topics_from_result(result)
            provider_label = "Anthropic" if self.grader.prefer_anthropic else "Anthropic fallback"
            log.info(
                f"Aggregate analysis completed ({provider_label}). Identified {len(self.grader.core_topics)} core topics, {len(self.grader.related_topics)} related topics"
            )
            return result

        def _run_openai() -> Dict:
            log.debug(
                f"Attempting aggregate analysis with OpenAI (tier={self.grader.phase1_tier})..."
            )
            result, usage = query_openai_structured(
                prompt,
                schema_name="aggregate_analysis",
                tier=self.grader.phase1_tier,
                max_response_tokens=2000)
            self.grader._track_token_usage(usage,
                                           "Phase 1 - Aggregate Analysis (OpenAI)")
            self.grader._store_topics_from_result(result)
            log.info(
                f"Aggregate analysis completed (OpenAI). Identified {len(self.grader.core_topics)} core topics, {len(self.grader.related_topics)} related topics"
            )
            return result

        def _on_openai_error(error: Exception, is_fallback: bool) -> None:
            log.error(f"OpenAI aggregate analysis failed: {error}")
            if not is_fallback:
                log.info("Falling back to Anthropic...")

        def _on_anthropic_error(error: Exception, is_fallback: bool) -> None:
            if not is_fallback:
                log.error(f"Anthropic aggregate analysis failed: {error}")
                log.info("Falling back to OpenAI...")
            else:
                log.error(f"Anthropic fallback also failed: {error}")

        def _on_both_fail(primary_error: Exception, _secondary_error: Exception) -> Dict:
            if self.grader.prefer_anthropic:
                return {
                    "common_themes": "Error performing analysis",
                    "key_insights": "",
                    "misconception_details": "",
                    "teaching_feedback": "",
                    "core_topics": [],
                    "related_topics": [],
                    "off_topic_indicators": [],
                    "commonly_misunderstood_topics": [],
                    "student_questions": []
                }

            return {
                "common_themes": f"Error performing analysis: {primary_error}",
                "key_insights": "",
                "misconception_details": "",
                "teaching_feedback": "",
                "core_topics": [],
                "related_topics": [],
                "off_topic_indicators": [],
                "commonly_misunderstood_topics": [],
                "student_questions": []
            }

        return orchestrator.run(run_openai=_run_openai,
                                run_anthropic=_run_anthropic,
                                on_openai_error=_on_openai_error,
                                on_anthropic_error=_on_anthropic_error,
                                on_both_fail=_on_both_fail)


class IndividualGradingProcessor:
    """
    Phase 2: Individual grading orchestration.

    Grades each submission individually using the core topics identified
    in Phase 1, then consolidates student questions.
    """

    def __init__(self, grader: "BaseTextSubmissionGrader"):
        """
        Initialize the individual grading processor.

        Args:
            grader: The grader instance for scoring and AI calls
        """
        self.grader = grader

    def grade_batch(self, submission_data: List[Dict],
                    core_topics: List[str]) -> List[Dict]:
        """
        Grade all submissions individually.

        Args:
            submission_data: List of submission dictionaries
            core_topics: Core topics from aggregate analysis

        Returns:
            List of individual grading results
        """
        log.info(f"Grading {len(submission_data)} individual submissions...")

        if not core_topics:
            log.warning("No core topics available for individual grading")
            core_topics = ["General class content"]

        individual_results = []
        self.grader.support_needed_students = []

        for i, submission_info in enumerate(submission_data, 1):
            student_id = submission_info.get('student_id')
            student_name = submission_info.get('student_name', 'Unknown')
            submission_text = submission_info.get('text', '')
            word_count = submission_info.get('word_count', 0)
            display_name = student_name
            if self.grader.reveal_identity and student_id is not None and str(
                    student_id) not in str(student_name):
                display_name = f"{student_name} [canvas_user_id={student_id}]"

            log.debug(
                f"   Grading {i}/{len(submission_data)}: {display_name} ({word_count} words)"
            )

            if not submission_text.strip():
                # Handle empty submissions
                result = {
                    "student_id": student_id,
                    "engagement_score": 0,
                    "relevance_score": 0,
                    "explanation_quality_score": 0,
                    "topics_covered": [],
                    "topics_missing": core_topics,
                    "topics_needing_review": [],
                    "misconception_notes": "",
                    "word_count": 0,
                    "needs_support": True,
                    "support_reason": "No submission content",
                    "feedback": "Please submit your study notes for grading."
                }
            else:
                # Grade the submission using AI
                result = self.grader._grade_individual_submission(
                    submission_text, core_topics, student_id)

            result = self.grader.score_calculator.apply_scores(
                result, word_count=word_count, student_name=student_name)

            # Track students needing support
            if self.grader.score_calculator.needs_support(result):
                self.grader.support_needed_students.append({
                    "student_id": student_id,
                    "student_name": student_name,
                    "reason": result.get("support_reason", "Unknown reason")
                })

            individual_results.append(result)

        log.info(
            f"Individual grading completed. {len(self.grader.support_needed_students)} students may need support."
        )

        # Phase 2.5: Consolidate questions (using questions from aggregate analysis)
        log.info("Phase 2.5/3: Question consolidation")
        student_questions = self.grader.aggregate_results.get("student_questions", [])
        self.grader.consolidated_questions = self.grader._consolidate_questions(
            student_questions)

        return individual_results


class QuestionConsolidator:
    """
    Phase 2.5: Question consolidation with provider fallback.

    Groups similar student questions into canonical versions for
    easier instructor review.
    """

    def __init__(self, grader: "BaseTextSubmissionGrader"):
        """
        Initialize the question consolidator.

        Args:
            grader: The grader instance for prompt building and AI calls
        """
        self.grader = grader

    def consolidate(self, all_questions: List[str]) -> List[Dict]:
        """
        Consolidate similar questions into canonical versions.

        Args:
            all_questions: List of all questions asked by students

        Returns:
            List of consolidated question dictionaries with canonical_question,
            original_questions, and topic keys
        """
        if not all_questions:
            log.info("No questions found to consolidate")
            return []

        log.info(f"Consolidating {len(all_questions)} questions from students...")

        prompt = self.grader._build_question_consolidation_prompt(all_questions)
        orchestrator = ProviderFallbackOrchestrator(self.grader.prefer_anthropic)

        def _run_anthropic() -> List[Dict]:
            operation = "Phase 2.5 - Question Consolidation (Anthropic)"
            if not self.grader.prefer_anthropic:
                operation = "Phase 2.5 - Question Consolidation (Anthropic fallback)"

            # Use query_anthropic_structured which has built-in JSON retry logic
            result, usage = query_anthropic_structured(
                prompt,
                schema_name="question_consolidation",
                tier=self.grader.phase25_tier,
                max_response_tokens=2000)
            self.grader._track_token_usage(usage, operation)

            consolidated = result.get("consolidated_questions", [])
            log.info(
                f"Consolidated {len(all_questions)} questions into {len(consolidated)} canonical questions"
            )
            return consolidated

        def _run_openai() -> List[Dict]:
            result, usage = query_openai_structured(
                prompt,
                schema_name="question_consolidation",
                tier=self.grader.phase25_tier,
                max_response_tokens=2000)
            self.grader._track_token_usage(
                usage, "Phase 2.5 - Question Consolidation (OpenAI)")
            consolidated = result.get("consolidated_questions", [])
            log.info(
                f"Consolidated {len(all_questions)} questions into {len(consolidated)} canonical questions"
            )
            return consolidated

        def _on_openai_error(error: Exception, is_fallback: bool) -> None:
            log.debug(f"OpenAI question consolidation failed: {error}")
            if not is_fallback:
                log.debug("Trying Anthropic as fallback...")

        def _on_anthropic_error(error: Exception, is_fallback: bool) -> None:
            if not is_fallback:
                log.debug(
                    f"Anthropic question consolidation failed: {error}. Trying OpenAI..."
                )
            else:
                log.debug(f"Anthropic question consolidation failed: {error}")

        def _on_both_fail(_primary_error: Exception,
                          secondary_error: Exception) -> List[Dict]:
            log.error(
                f"Both AI providers failed for question consolidation: {secondary_error}"
            )
            return []

        return orchestrator.run(run_openai=_run_openai,
                                run_anthropic=_run_anthropic,
                                on_openai_error=_on_openai_error,
                                on_anthropic_error=_on_anthropic_error,
                                on_both_fail=_on_both_fail)


class IndividualSubmissionAnalyzer:
    """
    AI evaluation for a single student submission.

    Handles the AI call with provider fallback to grade one submission
    against the core topics.
    """

    def __init__(self, grader: "BaseTextSubmissionGrader"):
        """
        Initialize the individual submission analyzer.

        Args:
            grader: The grader instance for prompt building and token tracking
        """
        self.grader = grader

    def analyze(self, submission_text: str, core_topics: List[str],
                student_id: str) -> Dict:
        """
        Analyze a single submission using AI.

        Args:
            submission_text: The student's submission text
            core_topics: Core topics to grade against
            student_id: Student identifier for logging

        Returns:
            Dictionary containing grading results
        """
        prompt = self.grader._build_individual_grading_prompt(submission_text,
                                                              core_topics)
        orchestrator = ProviderFallbackOrchestrator(self.grader.prefer_anthropic)

        def _default_from_text(analysis_text: str) -> Dict:
            return {
                "student_id": student_id,
                "engagement_score": 3,  # Default to moderate score
                "relevance_score": 1,
                "explanation_quality_score": 1,
                "topics_covered": [],
                "topics_missing": core_topics,
                "topics_needing_review": [],
                "misconception_notes": "",
                "needs_support": False,
                "support_reason": "",
                "feedback": analysis_text[:300] + "..." if len(analysis_text) > 300 else analysis_text
            }

        def _run_anthropic() -> Dict:
            operation = f"Phase 2 - Individual Grading ({student_id}) - Anthropic"
            if not self.grader.prefer_anthropic:
                operation = f"Phase 2 - Individual Grading ({student_id}) - Anthropic fallback"
            analysis_text, usage = query_anthropic_text(
                prompt, tier=self.grader.phase2_tier, max_response_tokens=1000)
            self.grader._track_token_usage(usage, operation)

            result = parse_anthropic_json_payload(analysis_text,
                                                  schema_name="individual_grading")
            if result is None:
                return _default_from_text(analysis_text)
            result["student_id"] = student_id
            return result

        def _run_openai() -> Dict:
            result, usage = query_openai_structured(
                prompt,
                schema_name="individual_grading",
                tier=self.grader.phase2_tier,
                max_response_tokens=1000)
            self.grader._track_token_usage(
                usage, f"Phase 2 - Individual Grading ({student_id}) - OpenAI")
            result["student_id"] = student_id
            return result

        def _on_openai_error(error: Exception, is_fallback: bool) -> None:
            if is_fallback and self.grader.prefer_anthropic:
                log.debug(f"OpenAI failed for {student_id}: {error}")
            else:
                log.debug(f"OpenAI failed for {student_id}: {error}")
                if not is_fallback:
                    log.debug("Trying Anthropic...")

        def _on_anthropic_error(error: Exception, is_fallback: bool) -> None:
            if is_fallback and not self.grader.prefer_anthropic:
                log.error(f"Both AI providers failed for {student_id}: {error}")
            elif not is_fallback:
                log.debug(f"Anthropic failed for {student_id}: {error}. Trying OpenAI...")
            else:
                log.debug(f"Anthropic failed for {student_id}: {error}")

        def _on_both_fail(primary_error: Exception, _secondary_error: Exception) -> Dict:
            if not self.grader.prefer_anthropic:
                return {
                    "student_id": student_id,
                    "engagement_score": 0,
                    "relevance_score": 0,
                    "explanation_quality_score": 0,
                    "topics_covered": [],
                    "topics_missing": core_topics,
                    "topics_needing_review": [],
                    "misconception_notes": "",
                    "needs_support": True,
                    "support_reason": "Error analyzing submission",
                    "feedback": f"Error analyzing submission: {primary_error}"
                }

            return {
                "student_id": student_id,
                "engagement_score": 0,
                "relevance_score": 0,
                "explanation_quality_score": 0,
                "topics_covered": [],
                "topics_missing": core_topics,
                "topics_needing_review": [],
                "misconception_notes": "",
                "needs_support": True,
                "support_reason": "Error analyzing submission",
                "feedback": "Error analyzing submission"
            }

        return orchestrator.run(run_openai=_run_openai,
                                run_anthropic=_run_anthropic,
                                on_openai_error=_on_openai_error,
                                on_anthropic_error=_on_anthropic_error,
                                on_both_fail=_on_both_fail)
