"""
Report compilation and presentation for text submission grading.

This module provides:
- ReportCompiler: Compiles grading data into a report structure
- ReportPresenter: Displays report data via logging

These classes are designed to be subclassed for custom report formats.
"""

import logging
from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseTextSubmissionGrader

log = logging.getLogger(__name__)


class ReportCompiler:
    """
    Compiles report data and summary statistics from grading results.

    Gathers data from aggregate analysis and individual grading into
    a structured report format suitable for display or export.
    """

    def __init__(self, grader: "BaseTextSubmissionGrader"):
        """
        Initialize the report compiler.

        Args:
            grader: The grader instance to get data from
        """
        self.grader = grader

    def compile(self, aggregate_results: Dict,
                individual_results: List[Dict]) -> Dict:
        """
        Compile all grading data into a report structure.

        Args:
            aggregate_results: Results from Phase 1 aggregate analysis
            individual_results: Results from Phase 2 individual grading

        Returns:
            Dictionary containing compiled report data
        """
        total_grades = [
            result.get("total_grade", 0) for result in individual_results
        ]
        grade_stats = {
            "total_students": len(individual_results),
            "average_grade": sum(total_grades) / len(total_grades) if total_grades else 0,
            "grade_distribution": self._calculate_grade_distribution(total_grades),
            "students_below_70": sum(1 for grade in total_grades if grade < 7),
        }

        topic_coverage = self._analyze_topic_coverage(individual_results)

        support_summary = {
            "students_needing_support": len(self.grader.support_needed_students),
            "support_details": self.grader.support_needed_students
        }
        privacy_summary = {
            "redacted_submission_count": len(self.grader.redaction_events),
            "redaction_events": self.grader.redaction_events,
        }

        return {
            "aggregate_insights": aggregate_results,
            "grade_statistics": grade_stats,
            "topic_coverage": topic_coverage,
            "support_summary": support_summary,
            "privacy_summary": privacy_summary,
            "core_topics": self.grader.core_topics,
            "individual_results": individual_results
        }

    def _calculate_grade_distribution(self, grades: List[float]) -> Dict:
        """
        Calculate distribution of grades by letter grade ranges.

        Args:
            grades: List of numeric grades (out of 10)

        Returns:
            Dictionary with counts for A, B, C, D, F grades
        """
        if not grades:
            return {}

        distribution = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
        for grade in grades:
            percentage = (grade / 10) * 100  # Convert to percentage
            if percentage >= 90:
                distribution["A"] += 1
            elif percentage >= 80:
                distribution["B"] += 1
            elif percentage >= 70:
                distribution["C"] += 1
            elif percentage >= 60:
                distribution["D"] += 1
            else:
                distribution["F"] += 1

        return distribution

    def _analyze_topic_coverage(self, individual_results: List[Dict]) -> Dict:
        """
        Analyze how well topics were covered across all students.

        Args:
            individual_results: Results from individual grading

        Returns:
            Dictionary mapping topic names to coverage statistics
        """
        if not self.grader.core_topics:
            return {}

        topic_stats = {}
        for topic in self.grader.core_topics:
            covered_count = sum(1 for result in individual_results
                                if topic in result.get("topics_covered", []))
            topic_stats[topic] = {
                "students_covered": covered_count,
                "coverage_percentage": (covered_count / len(individual_results)) * 100
                if individual_results else 0
            }

        return topic_stats


class ReportPresenter:
    """
    Presents report data via logging output.

    This class formats and logs the compiled report data. Subclass this
    to customize the presentation format (e.g., for different output
    channels like web UI, PDF, etc.).
    """

    def __init__(self, grader: "BaseTextSubmissionGrader"):
        """
        Initialize the report presenter.

        Args:
            grader: The grader instance (for accessing configuration)
        """
        self.grader = grader

    def present(self, report_data: Dict) -> None:
        """
        Present all sections of the report.

        Args:
            report_data: Compiled report data from ReportCompiler
        """
        self.display_aggregate_insights(report_data)
        self.display_grade_summary(report_data)
        self.display_support_recommendations(report_data)

    def display_aggregate_insights(self, report_data: Dict) -> None:
        """
        Display aggregate analysis insights.

        Args:
            report_data: Compiled report data
        """
        insights = report_data.get("aggregate_insights", {})

        log.debug("\nCLASS-WIDE INSIGHTS")
        log.debug("=" * 60)

        if insights.get("common_themes"):
            log.debug(f"Common themes:\n{insights['common_themes']}")

        if insights.get("key_insights"):
            log.debug(f"\nKey learning insights:\n{insights['key_insights']}")

        misunderstood = insights.get("commonly_misunderstood_topics", [])
        if misunderstood:
            log.debug(f"\nTopics needing review ({len(misunderstood)}):")
            for topic in misunderstood:
                log.debug(f"   - {topic}")
            if insights.get("misconception_details"):
                log.debug(f"   Details: {insights['misconception_details']}")

        if insights.get("teaching_feedback"):
            log.debug(
                f"\nTeaching recommendations:\n{insights['teaching_feedback']}")

        core_topics = report_data.get("core_topics", [])
        if core_topics:
            log.debug(f"\nCore topics identified ({len(core_topics)}):")
            for i, topic in enumerate(core_topics, 1):
                coverage = report_data["topic_coverage"].get(topic, {})
                coverage_pct = coverage.get("coverage_percentage", 0)
                log.debug(f"   {i}. {topic} ({coverage_pct:.1f}% of students)")

    def display_grade_summary(self, report_data: Dict) -> None:
        """
        Display grade summary statistics.

        Args:
            report_data: Compiled report data
        """
        stats = report_data.get("grade_statistics", {})

        log.debug("\nGRADE SUMMARY")
        log.debug("=" * 60)

        log.debug(f"Total Students: {stats.get('total_students', 0)}")
        log.debug(
            f"Average Grade: {stats.get('average_grade', 0):.1f}/10 ({stats.get('average_grade', 0)*10:.1f}%)"
        )

        distribution = stats.get("grade_distribution", {})
        if distribution:
            log.debug("\nGrade Distribution:")
            for letter, count in distribution.items():
                percentage = (count / stats.get('total_students', 1)) * 100
                log.debug(f"   {letter}: {count} students ({percentage:.1f}%)")

        below_70 = stats.get("students_below_70", 0)
        if below_70 > 0:
            log.debug(f"\n{below_70} students scored below 70%")

    def display_support_recommendations(self, report_data: Dict) -> None:
        """
        Display support recommendations for students.

        Args:
            report_data: Compiled report data
        """
        support = report_data.get("support_summary", {})
        students_needing_support = support.get("students_needing_support", 0)

        if students_needing_support > 0:
            log.debug("\nSTUDENTS WHO MAY BENEFIT FROM OFFICE HOURS")
            log.debug("=" * 60)

            for student_info in support.get("support_details", []):
                student_id = student_info.get("student_id", "Unknown")
                reason = student_info.get("reason", "No reason provided")
                log.debug(f"- {student_id}: {reason}")
        else:
            log.debug(
                "\nAll students appear to be engaging well with the material.")
