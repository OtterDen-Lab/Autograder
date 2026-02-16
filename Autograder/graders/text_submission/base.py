"""
Base class for text submission grading.

This module provides the extensible BaseTextSubmissionGrader class that
implements a 3-phase grading approach:
1. Aggregate Analysis - Identify core topics, misconceptions, and student questions
2. Individual Grading - Grade each submission for engagement, relevance, and quality
3. Report Generation - Generate comprehensive insights and recommendations

To create a custom text grader:
1. Subclass BaseTextSubmissionGrader
2. Override prompt methods (_build_*_prompt) for different grading criteria
3. Override hook methods (add_manual_topics_hook, output_report_hook) for customization
4. Optionally customize ScoreCalculator and RubricGenerator via constructor
"""

import logging
import os
import requests
from datetime import datetime
from typing import Dict, List

from Autograder.grader import Grader
from Autograder import config_models
from lms_interface.classes import Feedback, Submission, TextSubmission

from .pii import SubmissionPIIRedactor
from .scoring import ScoreCalculator, RubricGenerator
from .prompts import (
    get_aggregate_analysis_prompt,
    get_individual_grading_prompt,
    get_question_consolidation_prompt,
    DEFAULT_MAX_WORDS,
    DEFAULT_MAX_CHARACTERS,
)
from .processors import (
    BatchProcessor,
    AggregateAnalyzer,
    IndividualGradingProcessor,
    QuestionConsolidator,
    IndividualSubmissionAnalyzer,
)
from .reports import ReportCompiler, ReportPresenter

log = logging.getLogger(__name__)


class BaseTextSubmissionGrader(Grader):
    """
    Grader for text-based submissions using a 3-phase approach.

    This is the extensible base class for text submission grading. It provides:
    - 3-phase grading pipeline (aggregate, individual, report)
    - AI provider fallback (Anthropic/OpenAI)
    - PII redaction before AI calls
    - Configurable scoring rubric
    - Slack notifications and file output
    - Multiple hook methods for customization

    Rubric (10 points total, customizable):
    - Engagement (4 pts): Effort to process and explain material
    - Length (2 pts): Meeting 250+ word requirement (calculated locally)
    - Relevance (2 pts): Coverage of class topics
    - Explanation Quality (2 pts): Depth of explanation, not correctness

    To customize for different assignment types, subclass this and override:
    - _build_aggregate_analysis_prompt(): Different aggregate analysis criteria
    - _build_individual_grading_prompt(): Different grading rubric/criteria
    - _build_question_consolidation_prompt(): Different question handling
    - add_manual_topics_hook(): Add/modify topics after AI analysis
    - output_report_hook(): Custom report delivery
    """

    COMPATIBLE_KINDS = {"TextAssignment"}

    @classmethod
    def normalize_settings(cls, settings: Dict, context_label: str) -> Dict:
        """Validate and normalize text submission grader settings."""
        return config_models._normalize_text_submission_grader_settings(
            settings, context_label)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Topic tracking
        self.core_topics = []
        self.related_topics = []
        self.off_topic_indicators = []

        # Results storage
        self.aggregate_results = {}
        self.individual_results = []
        self.support_needed_students = []
        self.consolidated_questions = []

        # Configuration from kwargs
        self.slack_channel = kwargs.get('slack_channel')
        self.records_dir = None
        self.reveal_identity = False

        # Scoring and presentation components (can be overridden by subclasses)
        self.score_calculator = ScoreCalculator()
        self.rubric_generator = RubricGenerator()

        # PII redaction
        self.pii_redactor = SubmissionPIIRedactor()
        self.redaction_events: List[Dict] = []

        # Workflow processors (inject self for customization hooks)
        self.batch_processor = BatchProcessor(self)
        self.question_consolidator = QuestionConsolidator(self)
        self.individual_grading_processor = IndividualGradingProcessor(self)
        self.aggregate_analyzer = AggregateAnalyzer(self)
        self.individual_submission_analyzer = IndividualSubmissionAnalyzer(self)
        self.report_compiler = ReportCompiler(self)
        self.report_presenter = ReportPresenter(self)

        # Model tier settings for each phase (small, medium, large)
        # Can be configured via grader settings in YAML
        self.phase1_tier = kwargs.get('phase1_tier', 'small')   # Aggregate analysis
        self.phase2_tier = kwargs.get('phase2_tier', 'small')   # Individual grading
        self.phase25_tier = kwargs.get('phase25_tier', 'small')  # Question consolidation

        log.info(
            f"{self.__class__.__name__} initialized with tiers: "
            f"phase1={self.phase1_tier}, phase2={self.phase2_tier}, phase25={self.phase25_tier}"
        )

    # =========================================================================
    # Prompt building methods - Override these for different grading criteria
    # =========================================================================

    def _build_aggregate_analysis_prompt(self, submission_texts: List[str],
                                         assignment_name: str,
                                         course_name: str) -> str:
        """
        Build the prompt for Phase 1 aggregate analysis.

        Override this method to customize how submissions are analyzed
        for identifying topics and patterns.

        Args:
            submission_texts: List of all submission texts
            assignment_name: Name of the assignment
            course_name: Name of the course

        Returns:
            Prompt string for the AI
        """
        return get_aggregate_analysis_prompt(submission_texts, assignment_name,
                                             course_name)

    def _build_individual_grading_prompt(self, submission_text: str,
                                         core_topics: List[str]) -> str:
        """
        Build the prompt for Phase 2 individual grading.

        Override this method to customize the grading rubric and criteria.

        Args:
            submission_text: The student's submission
            core_topics: Core topics from aggregate analysis

        Returns:
            Prompt string for the AI
        """
        return get_individual_grading_prompt(
            submission_text, core_topics,
            related_topics=self.related_topics,
            off_topic_indicators=self.off_topic_indicators
        )

    def _build_question_consolidation_prompt(self, all_questions: List[str]) -> str:
        """
        Build the prompt for question consolidation.

        Override this method to customize how questions are grouped.

        Args:
            all_questions: List of questions from students

        Returns:
            Prompt string for the AI
        """
        return get_question_consolidation_prompt(all_questions)

    # =========================================================================
    # PII redaction
    # =========================================================================

    def _redact_submission_text_for_ai(
            self,
            text: str,
            *,
            student_name: str | None = None,
            student_id: int | str | None = None) -> tuple[str, Dict[str, int]]:
        """
        Redact PII from submission text before sending to AI.

        Args:
            text: The submission text
            student_name: Optional student name to redact
            student_id: Optional student ID to redact

        Returns:
            Tuple of (redacted_text, redaction_counts)
        """
        redacted, counts = self.pii_redactor.redact(text,
                                                     student_name=student_name,
                                                     student_id=student_id)
        if counts.get("total_replacements", 0) > 0:
            self.redaction_events.append({
                "student_id": student_id,
                "student_name": student_name,
                "counts": counts,
            })
        return redacted, counts

    # =========================================================================
    # Submission validation and truncation
    # =========================================================================

    def can_grade_submission(self, submission: Submission) -> bool:
        """
        Check if this grader can handle the given submission type.

        Text-based graders can only grade TextSubmission objects.

        Args:
            submission: The submission to check

        Returns:
            True if this is a TextSubmission
        """
        return isinstance(submission, TextSubmission)

    def _truncate_submission_text(
            self,
            text: str,
            max_words: int = DEFAULT_MAX_WORDS,
            max_chars: int = DEFAULT_MAX_CHARACTERS) -> tuple[str, bool]:
        """
        Truncate submission text to max words or max characters.

        Args:
            text: The submission text to truncate
            max_words: Maximum number of words (default: 1000)
            max_chars: Maximum number of characters (default: 7500)

        Returns:
            Tuple of (truncated_text, was_truncated)
        """
        if not text:
            return text, False

        words = text.split()

        # Check word limit
        if len(words) > max_words:
            truncated = ' '.join(words[:max_words])
            return truncated, True

        # Check character limit
        if len(text) > max_chars:
            truncated = text[:max_chars]
            # Try to truncate at word boundary
            last_space = truncated.rfind(' ')
            if last_space > max_chars * 0.9:  # Only if we're not losing too much
                truncated = truncated[:last_space]
            return truncated, True

        return text, False

    # =========================================================================
    # Main grading flow
    # =========================================================================

    def grade_assignment(self, assignment, *args, **kwargs) -> None:
        """
        Override the main grading flow to implement 3-phase approach.

        Args:
            assignment: The assignment to grade
            **kwargs: Additional arguments including:
                - course_name: Name of the course
                - prefer_anthropic: Whether to prefer Anthropic AI
                - records_dir: Directory for saving records
                - reveal_identity: Whether to reveal student identities
        """
        from Autograder.assignment import Assignment_TextAssignment

        if not isinstance(assignment, Assignment_TextAssignment):
            log.error(
                f"TextSubmissionGrader requires Assignment_TextAssignment, got {type(assignment)}"
            )
            return

        # Store assignment and course info for Slack reporting
        self.assignment_name = assignment.lms_assignment.name
        self.course_name = kwargs.get('course_name', 'Unknown Course')

        # Store AI provider preference and records directory
        self.prefer_anthropic = kwargs.get('prefer_anthropic', False)
        self.records_dir = kwargs.get('records_dir')
        self.reveal_identity = kwargs.get('reveal_identity', False)

        # Initialize token tracking
        self.total_tokens = 0
        self.total_cost = 0.0
        self.usage_details = []

        assignment_name = assignment.lms_assignment.name
        self.batch_processor.run(assignment,
                                 assignment_name=assignment_name,
                                 course_name=self.course_name)

    # =========================================================================
    # Phase methods - Called by processors
    # =========================================================================

    def phase_1_aggregate_analysis(self, submission_texts: List[str],
                                   assignment_name: str,
                                   course_name: str = "Unknown Course") -> Dict:
        """
        Phase 1: Analyze all submissions to identify core topics and patterns.

        Args:
            submission_texts: List of all submission text content
            assignment_name: Name of the assignment for context
            course_name: Name of the course for context

        Returns:
            Dictionary containing aggregate analysis results
        """
        return self.aggregate_analyzer.analyze(submission_texts, assignment_name,
                                               course_name)

    def phase_2_individual_grading(self, submission_data: List[Dict],
                                   core_topics: List[str]) -> List[Dict]:
        """
        Phase 2: Grade each submission individually using core topics.

        Args:
            submission_data: List of submission data dictionaries
            core_topics: Core topics identified from aggregate analysis

        Returns:
            List of individual grading results
        """
        return self.individual_grading_processor.grade_batch(submission_data,
                                                             core_topics)

    def _consolidate_questions(self, all_questions: List[str]) -> List[Dict]:
        """
        Consolidate similar questions from all submissions.

        Args:
            all_questions: List of questions extracted from aggregate analysis

        Returns:
            List of consolidated question dictionaries
        """
        return self.question_consolidator.consolidate(all_questions)

    def _grade_individual_submission(self, submission_text: str,
                                     core_topics: List[str],
                                     student_id: str) -> Dict:
        """
        Grade a single submission using AI analysis.

        Args:
            submission_text: The student's submission text
            core_topics: Core topics to check for coverage
            student_id: Student identifier for logging

        Returns:
            Dictionary with grading results
        """
        return self.individual_submission_analyzer.analyze(submission_text,
                                                           core_topics, student_id)

    def phase_3_generate_report(self, aggregate_results: Dict,
                                individual_results: List[Dict]) -> None:
        """
        Phase 3: Generate comprehensive report with insights and recommendations.

        Args:
            aggregate_results: Results from aggregate analysis
            individual_results: Results from individual grading
        """
        log.info("Generating comprehensive class insights report...")

        # Compile report data
        report_data = self._compile_report_data(aggregate_results,
                                                individual_results)

        # Display the report
        self.report_presenter.present(report_data)

        # Use output hook for custom delivery
        self.output_report_hook(report_data)

        log.info("Report generation completed.")

    def _compile_report_data(self, aggregate_results: Dict,
                             individual_results: List[Dict]) -> Dict:
        """Compile all data needed for the report."""
        return self.report_compiler.compile(aggregate_results, individual_results)

    def _calculate_grade_distribution(self, grades: List[float]) -> Dict:
        """Calculate distribution of grades by letter grade ranges."""
        return self.report_compiler._calculate_grade_distribution(grades)

    def _analyze_topic_coverage(self, individual_results: List[Dict]) -> Dict:
        """Analyze how well topics were covered across all students."""
        return self.report_compiler._analyze_topic_coverage(individual_results)

    def _display_aggregate_insights(self, report_data: Dict) -> None:
        """Display aggregate analysis insights."""
        self.report_presenter.display_aggregate_insights(report_data)

    def _display_grade_summary(self, report_data: Dict) -> None:
        """Display grade summary statistics."""
        self.report_presenter.display_grade_summary(report_data)

    def _display_support_recommendations(self, report_data: Dict) -> None:
        """Display support recommendations for students."""
        self.report_presenter.display_support_recommendations(report_data)

    # =========================================================================
    # Grade application
    # =========================================================================

    def _apply_grades_to_submissions(self, submissions: List[Submission],
                                     individual_results: List[Dict]) -> None:
        """
        Apply calculated grades and feedback to Canvas submission objects.

        Args:
            submissions: Original Canvas submission objects
            individual_results: Grading results with scores and feedback
        """
        # Create a mapping from student_id to results for efficient lookup
        results_by_student = {
            result.get('student_id'): result
            for result in individual_results
        }

        for submission in submissions:
            result = results_by_student.get(submission.student.user_id)
            if result and not result.get("grading_failed", False):
                # Use pre-calculated total grade (out of 10) and convert to percentage
                total_grade = result.get('total_grade', 0)
                percentage_score = (total_grade / 10.0) * 100.0

                # Create detailed rubric feedback
                feedback_text = self.rubric_generator.generate(result)
                submission.feedback = Feedback(percentage_score, feedback_text)
            elif result and result.get("grading_failed", False):
                submission.feedback = None
                submission.set_extra({
                    "grading_error": "llm_grading_failed",
                    "grading_error_message": result.get(
                        "support_reason", "LLM grading failed")
                })
            else:
                # Keep ungraded if no result mapping is available.
                submission.feedback = None
                submission.set_extra({
                    "grading_error": "missing_grading_result",
                    "grading_error_message":
                    "Could not map grading result to submission."
                })

    def _generate_rubric_feedback(self, result: Dict) -> str:
        """Backward-compatible wrapper around RubricGenerator."""
        return self.rubric_generator.generate(result)

    # =========================================================================
    # Hook methods for customization - Override these in subclasses
    # =========================================================================

    def _store_topics_from_result(self, result: Dict) -> None:
        """
        Extract and store all topic types from aggregate analysis result.

        Args:
            result: The aggregate analysis result dictionary
        """
        # Store core topics
        self.core_topics = result.get("core_topics", [])
        # Apply topic addition hook
        self.core_topics = self.add_manual_topics_hook(self.core_topics)

        # Store related topics (tangential but valid)
        self.related_topics = result.get("related_topics", [])

        # Store off-topic indicators
        self.off_topic_indicators = result.get("off_topic_indicators", [])

        # Log topic counts
        log.debug(f"Core topics: {self.core_topics}")
        log.debug(f"Related topics: {self.related_topics}")
        if self.off_topic_indicators:
            log.debug(f"Off-topic indicators: {self.off_topic_indicators}")

    def add_manual_topics_hook(self, ai_topics: List[str]) -> List[str]:
        """
        Hook for manually adding or modifying topics after AI analysis.

        Override this method to customize topic selection, for example
        to add course-specific topics that should always be included.

        Args:
            ai_topics: Topics identified by AI analysis

        Returns:
            Final list of topics to use for grading
        """
        return ai_topics

    def output_report_hook(self, report_data: Dict) -> None:
        """
        Hook for customizing report output format.

        Override this method to change how reports are delivered (e.g.,
        to a different notification system, file format, or database).

        Args:
            report_data: Compiled report data
        """
        # Save questions to records directory if configured
        if self.records_dir and self.consolidated_questions:
            self._save_questions_to_records()

        # Send to Slack if configured (includes question file attachment)
        self._send_slack_notification(report_data)

        # Also print to console
        self._print_report_to_console(report_data)

    # =========================================================================
    # File and Slack output
    # =========================================================================

    def _save_questions_to_records(self) -> None:
        """
        Save consolidated questions to records directory as markdown file.

        Filename format: [course_name].[assignment_name].learning-log.md
        """
        if not self.records_dir or not self.consolidated_questions:
            return

        try:
            # Ensure records directory exists
            if not os.path.exists(self.records_dir):
                os.makedirs(self.records_dir)
                log.info(f"Created records directory: {self.records_dir}")

            # Sanitize course and assignment names for filename
            course_safe = self.course_name.replace(' ', '_').replace('/', '-')
            assignment_safe = self.assignment_name.replace(' ', '_').replace('/', '-')
            filename = f"{course_safe}.{assignment_safe}.learning-log.md"
            filepath = os.path.join(self.records_dir, filename)

            # Generate markdown content
            markdown_content = self._generate_questions_markdown()

            # Write to file
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(markdown_content)

            log.info(f"Saved questions to records: {filepath}")

        except Exception as e:
            log.error(f"Failed to save questions to records directory: {e}")

    def _send_slack_notification(self, report_data: Dict) -> None:
        """
        Send summary notification to Slack if configured.

        Includes markdown file attachment if there are student questions.

        Args:
            report_data: Report data to send
        """
        slack_token = os.getenv('SLACK_BOT_TOKEN')
        slack_channel = self.slack_channel

        if not slack_token or not slack_channel:
            log.debug(
                "Slack not configured (missing SLACK_BOT_TOKEN or course slack_channel)"
            )
            return

        try:
            # Create concise summary
            message = self._create_slack_summary(report_data)

            # Send message to Slack
            response = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}"},
                json={
                    "channel": slack_channel,
                    "text": message,
                    "mrkdwn": True,
                    "unfurl_links": False,
                    "unfurl_media": False
                },
                timeout=10)

            if not response.json().get('ok'):
                log.warning(
                    f"Slack notification failed: {response.json().get('error')}")
                return

            log.info("Slack notification sent successfully")

            # Upload questions markdown file if there are questions
            if self.consolidated_questions:
                self._upload_questions_to_slack(slack_token, slack_channel)

        except Exception as e:
            log.warning(f"Failed to send Slack notification: {e}")

    def _report_individual_grading_failure(self, student_id: int | str,
                                           reason: str) -> None:
        """
        Send a targeted Slack alert when AI grading fails for a submission.

        Args:
            student_id: Canvas user ID for the affected submission
            reason: Failure reason for operator triage
        """
        slack_token = os.getenv('SLACK_BOT_TOKEN')
        slack_channel = self.slack_channel
        if not slack_token or not slack_channel:
            return

        safe_reason = (reason or "").strip()
        if len(safe_reason) > 500:
            safe_reason = safe_reason[:500] + "... [truncated]"

        message = (
            f":warning: *LLM grading failed for one submission*\n"
            f"*Course:* {self.course_name}\n"
            f"*Assignment:* {self.assignment_name}\n"
            f"*Student ID:* {student_id}\n"
            f"*Reason:* {safe_reason}\n"
            "Submission left ungraded for manual follow-up.")

        try:
            response = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}"},
                json={
                    "channel": slack_channel,
                    "text": message,
                    "mrkdwn": True,
                    "unfurl_links": False,
                    "unfurl_media": False
                },
                timeout=10)
            if not response.json().get('ok'):
                log.warning(
                    f"Failed to send per-submission LLM failure alert: {response.json().get('error')}"
                )
        except Exception as e:
            log.warning(f"Failed to send per-submission LLM failure alert: {e}")

    def _upload_questions_to_slack(self, slack_token: str,
                                   slack_channel: str) -> None:
        """
        Upload student questions markdown file to Slack.

        Args:
            slack_token: Slack bot token
            slack_channel: Slack channel ID or name
        """
        try:
            # Generate markdown content
            markdown_content = self._generate_questions_markdown()

            # Generate filename
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            course_safe = self.course_name.replace(' ', '_').replace('/', '-')
            assignment_safe = self.assignment_name.replace(' ', '_').replace('/', '-')
            filename = f"questions_{course_safe}_{assignment_safe}_{timestamp}.md"

            # Upload file to Slack
            response = requests.post(
                "https://slack.com/api/files.upload",
                headers={"Authorization": f"Bearer {slack_token}"},
                data={
                    "channels": slack_channel,
                    "filename": filename,
                    "filetype": "markdown",
                    "initial_comment":
                    f"Student questions from {self.assignment_name} ({len(self.consolidated_questions)} topics)"
                },
                files={"file": (filename, markdown_content, "text/markdown")},
                timeout=30)

            if response.json().get('ok'):
                log.info(f"Questions file uploaded to Slack: {filename}")
            else:
                log.warning(
                    f"Failed to upload questions file: {response.json().get('error')}")

        except Exception as e:
            log.warning(f"Failed to upload questions to Slack: {e}")

    def _generate_questions_markdown(self) -> str:
        """
        Generate markdown content for student questions.

        Returns:
            Markdown-formatted string with all student questions
        """
        if not self.consolidated_questions:
            return ""

        lines = [
            f"# Student Questions: {self.course_name} - {self.assignment_name}",
            f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            "",
            f"Total unique question topics: {len(self.consolidated_questions)}",
            "",
            "---",
            ""
        ]

        # Add each consolidated question group
        for i, q_group in enumerate(self.consolidated_questions, 1):
            canonical = q_group.get("canonical_question", "")
            topic = q_group.get("topic", "General")
            original_questions = q_group.get("original_questions", [])
            student_count = len(original_questions)

            lines.append(f"## {i}. {topic}")
            lines.append("")
            lines.append(f"**Question:** {canonical}")
            lines.append("")
            lines.append(
                f"*Asked by {student_count} student{'s' if student_count > 1 else ''}*"
            )
            lines.append("")

            # Add space for instructor's answer
            lines.append("**Answer:**")
            lines.append("")
            lines.append("<!-- Your answer here -->")
            lines.append("")

            # Show original questions in a collapsible section if there are multiple
            if len(original_questions) > 1:
                lines.append("<details>")
                lines.append(
                    "<summary>Show original questions from students</summary>")
                lines.append("")
                for orig_q in original_questions:
                    lines.append(f"- {orig_q}")
                lines.append("")
                lines.append("</details>")
                lines.append("")

            lines.append("---")
            lines.append("")

        return '\n'.join(lines)

    def _create_slack_summary(self, report_data: Dict) -> str:
        """
        Create concise summary for Slack notification.

        Args:
            report_data: Report data to summarize

        Returns:
            Formatted message string
        """
        stats = report_data.get("grade_statistics", {})
        support = report_data.get("support_summary", {})
        insights = report_data.get("aggregate_insights", {})
        topics = report_data.get("core_topics", [])

        # Get course and assignment info from the grader instance
        course_name = getattr(self, 'course_name', 'Unknown Course')
        assignment_name = getattr(self, 'assignment_name', 'Unknown Assignment')

        # Add cost information if available, with phase breakdown
        cost_text = ""
        phase_breakdown = ""
        if self.total_cost > 0:
            # Calculate cost and model by phase
            phase1_entries = [u for u in self.usage_details if 'Phase 1' in u.get('operation', '')]
            phase2_entries = [u for u in self.usage_details if 'Phase 2 -' in u.get('operation', '')]
            phase25_entries = [u for u in self.usage_details if 'Phase 2.5' in u.get('operation', '')]

            phase1_cost = sum(u.get('cost', 0) for u in phase1_entries)
            phase2_cost = sum(u.get('cost', 0) for u in phase2_entries)
            phase25_cost = sum(u.get('cost', 0) for u in phase25_entries)

            # Get predominant model for each phase
            def get_model(entries):
                if not entries:
                    return "n/a"
                models = [u.get('model', u.get('provider', 'unknown')) for u in entries]
                # Return most common model, with light shortening
                model = max(set(models), key=models.count) if models else "unknown"
                # Shorten model names for display
                if 'claude' in model.lower():
                    for variant in ['haiku', 'sonnet', 'opus']:
                        if variant in model.lower():
                            parts = model.lower().split('-')
                            version_parts = [p for p in parts if p[0].isdigit()] if parts else []
                            if version_parts:
                                return f"{variant}-{version_parts[0]}"
                            return variant
                    return model[:20]
                elif 'gpt' in model.lower():
                    return model.replace('gpt-', '')
                return model[:15]

            phase1_model = get_model(phase1_entries)
            phase2_model = get_model(phase2_entries)

            cost_text = f" (${self.total_cost:.4f} - {self.total_tokens} tokens)"
            phase_breakdown = f"  _Phase 1: ${phase1_cost:.2f} ({phase1_model}) | Phase 2: ${phase2_cost:.2f} ({phase2_model}) | Q&A: ${phase25_cost:.2f}_"

        # Build summary message with header
        lines = [
            f"*{course_name} - {assignment_name}*",
            f"Grading Complete{cost_text}",
        ]
        if phase_breakdown:
            lines.append(phase_breakdown)
        lines.extend([
            "",
            "*Summary:*",
            f"- {stats.get('total_students', 0)} students graded",
            f"- Average: {stats.get('average_grade', 0):.1f}/10 ({stats.get('average_grade', 0)*10:.1f}%)",
        ])

        # Add grade distribution summary
        distribution = stats.get("grade_distribution", {})
        if distribution:
            a_b_count = distribution.get("A", 0) + distribution.get("B", 0)
            c_d_f_count = distribution.get("C", 0) + distribution.get("D", 0) + distribution.get("F", 0)
            lines.append(f"- Grades: {a_b_count} A/B, {c_d_f_count} C/D/F")

        # Add support needs - show ALL students with better formatting
        support_count = support.get("students_needing_support", 0)
        if support_count > 0:
            lines.append(f"\n*Office Hours Recommended ({support_count} students):*")
            for i, student_info in enumerate(support.get("support_details", []), 1):
                student_name = student_info.get("student_name", "Unknown Student")
                reason = student_info.get("reason", "")
                if reason.strip():
                    lines.append(f"{i}. `{student_name}` - {reason}")
                else:
                    lines.append(f"{i}. `{student_name}` - *(No specific reason provided)*")
        else:
            lines.append("\n*Status:* All students engaging well")

        # Add topic insights as a list - show ALL topics
        if topics:
            lines.append(f"\n*Core Topics:*")
            for topic in topics:
                lines.append(f"- {topic}")

        # Add related topics if any
        related = insights.get("related_topics", [])
        if related:
            lines.append(f"\n*Related Topics (also valid):*")
            for topic in related:
                lines.append(f"- {topic}")

        # Add commonly misunderstood topics
        misunderstood = insights.get("commonly_misunderstood_topics", [])
        if misunderstood:
            lines.append(f"\n*Topics Needing Review (common confusion):*")
            for topic in misunderstood:
                lines.append(f"- {topic}")
            misconception_details = insights.get("misconception_details", "").strip()
            if misconception_details:
                lines.append(f"_{misconception_details}_")

        # Add teaching insights as a list
        teaching_feedback = insights.get("teaching_feedback", "").strip()
        if teaching_feedback:
            lines.append(f"\n*Teaching Suggestions:*")
            sentences = [s.strip() for s in teaching_feedback.split('.') if s.strip()]
            for sentence in sentences:
                lines.append(f"- {sentence}")

        # Add consolidated questions section
        if self.consolidated_questions:
            lines.append(
                f"\n*Key Questions from Students ({len(self.consolidated_questions)} topics):*"
            )
            for q_group in self.consolidated_questions:
                canonical = q_group.get("canonical_question", "")
                topic = q_group.get("topic", "")
                original_count = len(q_group.get("original_questions", []))

                if topic:
                    lines.append(
                        f"- *{topic}*: {canonical} ({original_count} student{'s' if original_count > 1 else ''})"
                    )
                else:
                    lines.append(
                        f"- {canonical} ({original_count} student{'s' if original_count > 1 else ''})"
                    )

        return "\n".join(lines)

    # =========================================================================
    # Token usage tracking
    # =========================================================================

    def _track_token_usage(self, usage_info: Dict, operation: str) -> None:
        """
        Track token usage and calculate costs.

        Args:
            usage_info: Usage information from AI provider
            operation: Description of the operation
        """
        provider = usage_info.get("provider", "unknown")
        model = usage_info.get("model", "unknown")
        total_tokens = usage_info.get("total_tokens", 0)
        prompt_tokens = usage_info.get("prompt_tokens", 0)
        completion_tokens = usage_info.get("completion_tokens", 0)

        # Calculate cost based on provider
        cost = self._calculate_cost(usage_info)

        # Track totals
        self.total_tokens += total_tokens
        self.total_cost += cost

        # Store detailed usage
        self.usage_details.append({
            "operation": operation,
            "provider": provider,
            "model": model,
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost": cost
        })

        log.debug(
            f"{operation}: {total_tokens} tokens (${cost:.4f}) via {provider}/{model}")

    def _calculate_cost(self, usage_info: Dict) -> float:
        """
        Calculate cost based on provider and model pricing.

        Args:
            usage_info: Usage information with provider, model, and token counts

        Returns:
            Estimated cost in USD
        """
        from Autograder.ai_helper import get_model_pricing

        provider = usage_info.get("provider", "unknown")
        model = usage_info.get("model", "unknown")
        prompt_tokens = usage_info.get("prompt_tokens", 0)
        completion_tokens = usage_info.get("completion_tokens", 0)

        # Get pricing from centralized MODEL_CONFIG
        input_price, output_price = get_model_pricing(provider, model)

        # Calculate cost (prices are per million tokens)
        prompt_cost = (prompt_tokens / 1_000_000) * input_price
        completion_cost = (completion_tokens / 1_000_000) * output_price
        return prompt_cost + completion_cost

    def _print_report_to_console(self, report_data: Dict) -> None:
        """
        Default implementation for printing report to console.

        Args:
            report_data: Report data to display
        """
        log.info("Report generation completed (use --debug for full summary).")

    # =========================================================================
    # Abstract method implementations (required by base Grader class)
    # =========================================================================

    def execute_grading(self, *args, **kwargs):
        """Not used in text submission grading - phases handle execution."""
        return None

    def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
        """Not used in text submission grading - phases handle scoring."""
        return Feedback(0.0, "TextSubmissionGrader uses phase-based grading")

    def assignment_needs_preparation(self) -> bool:
        """Text assignments need preparation to fetch submissions."""
        return True
