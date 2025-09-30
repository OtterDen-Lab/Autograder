#!env python
"""
Text Submission Grader for Learning Log Assignments

Implements a 3-phase grading approach:
1. Aggregate Analysis - Identify core topics across all submissions
2. Individual Grading - Grade each submission using identified topics
3. Report Generation - Generate comprehensive insights and recommendations
"""

from typing import List, Dict
import logging

from Autograder.grader import Grader
from Autograder.registry import GraderRegistry
from Autograder.assignment import Assignment
from lms_interface.classes import Feedback, Submission, TextSubmission

log = logging.getLogger(__name__)


# AI Prompts for Text Submission Grading
def get_aggregate_analysis_prompt(submission_texts: List[str], assignment_name: str) -> str:
    """
    Get prompt for aggregate analysis of all submissions.

    Args:
        submission_texts: List of all submission text content
        assignment_name: Name of the assignment for context

    Returns:
        Formatted prompt string for aggregate analysis
    """
    num_submissions = len(submission_texts)

    return f"""
You are analyzing student learning log submissions for an assignment called "{assignment_name}".

Please analyze these {num_submissions} student submissions and return a JSON response with:

{{
  "common_themes": "What concepts or topics are most students discussing?",
  "key_insights": "What seems to be sticking with students vs. what they're struggling with?",
  "learning_patterns": "Are there recurring learning patterns or misconceptions?",
  "teaching_feedback": "Based on these submissions, what feedback would help the instructor improve their teaching?",
  "core_topics": ["exactly", "5", "most", "important", "general", "topics"]
}}

For core_topics, identify the 5 most important and general topics that best summarize what was covered in class this week. These should be:
- Broad enough to encompass multiple related concepts students discussed
- The most fundamental/important topics from the class session
- Topics that multiple students engaged with (directly or indirectly)
- General categories rather than very specific technical terms

Here are the submissions:

{chr(10).join([f"---SUBMISSION {i+1}---{chr(10)}{text}" for i, text in enumerate(submission_texts)])}

Return only valid JSON.
"""


def get_individual_grading_prompt(submission_text: str, core_topics: List[str]) -> str:
    """
    Get prompt for individual submission grading.

    Args:
        submission_text: The student's submission text
        core_topics: List of core topics identified from aggregate analysis

    Returns:
        Formatted prompt string for individual grading
    """
    topics_str = ", ".join(core_topics)

    return f"""
You are analyzing a student's learning log submission for grading and support identification. Learning logs are study tools where students explain topics to their future selves. The instructor emphasizes: "the best way to make a study guide and are for you, because I know this material already -- write it for your future self."

These GENERAL topics were covered in class: {topics_str}

GRADING RUBRIC (Total: 10 points):
- Completion (4 pts): Based on genuine effort and depth of reflection
- Length (2 pts): ≥250 words gets 2/2, <250 words gets 0/2
- Relevance (2 pts): Addresses class material (2=covers 3+ topics, 1=covers 1-2, 0=off-topic)
- Explanation Effort (2 pts): Attempts to explain concepts for future self, even if confused

Please analyze this submission and return a JSON response with:
{{
  "completion_score": "4, 3, 2, 1, or 0 based on depth of reflection and genuine effort",
  "relevance_score": "2, 1, or 0 based on topic coverage",
  "explanation_effort_score": "2, 1, or 0 based on attempt to explain vs. just list facts",
  "topics_covered": ["list", "of", "general", "class", "topics", "that", "relate", "to", "student", "content"],
  "topics_missing": ["list", "of", "general", "class", "topics", "not", "addressed"],
  "needs_support": "true/false - student shows significant confusion or struggle that warrants office hours suggestion",
  "support_reason": "brief explanation if needs_support is true, empty string if false",
  "feedback": "supportive guidance to help the student write more reflectively for better studying"
}}

SCORING GUIDELINES:
- Completion: Reward genuine engagement with learning, even if confused. Penalize only minimal effort.
- Explanation Effort: Full points for trying to work through concepts in their own words, even if incorrect.
- A confused student genuinely trying to understand should get high completion and explanation scores.

IMPORTANT:
- If the student wrote about completely unrelated topics (different subject area), note this gently in feedback
- Only mark topics as "covered" if they actually relate to the class material, even if mentioned indirectly
- For topics_covered, use ONLY the general topic names from the class list, not the specific student subtopics

For feedback, focus on:
- Suggesting how concepts could be explained more clearly
- Noting topics that might be worth reviewing
- Study strategies rather than corrections
- If content is off-topic, redirect toward class material without excessive praise
- Keep tone professional and direct, not overly enthusiastic

Student submission:
{submission_text}

Return only valid JSON.
"""


# Configuration constants for easy modification
DEFAULT_MAX_TOPICS = 5
DEFAULT_WORD_THRESHOLD = 250
DEFAULT_RUBRIC_TOTAL = 10

# Rubric component defaults
COMPLETION_POINTS = 4
LENGTH_POINTS = 2
RELEVANCE_POINTS = 2
EXPLANATION_POINTS = 2


@GraderRegistry.register("TextSubmissionGrader")
class TextSubmissionGrader(Grader):
  """
  Grader for text-based learning log submissions.

  Implements a 3-phase grading approach:
  1. Aggregate Analysis - Identify core topics across all submissions
  2. Individual Grading - Grade each submission using identified topics
  3. Report Generation - Generate comprehensive insights and recommendations
  """

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.core_topics = []
    self.aggregate_results = {}
    self.individual_results = []
    self.support_needed_students = []
    self.slack_channel = kwargs.get('slack_channel')

  def can_grade_submission(self, submission: Submission) -> bool:
    """
    Text-based graders can only grade TextSubmission objects.
    """
    return isinstance(submission, TextSubmission)

  def grade_assignment(self, assignment: Assignment, *args, **kwargs) -> None:
    """
    Override the main grading flow to implement 3-phase approach.
    """
    from Autograder.assignment import Assignment_TextAssignment

    if not isinstance(assignment, Assignment_TextAssignment):
      log.error(f"TextSubmissionGrader requires Assignment_TextAssignment, got {type(assignment)}")
      return

    assignment_name = assignment.lms_assignment.name
    submission_data = assignment.get_submission_data()
    submission_texts = assignment.get_all_submission_texts()

    log.info(f"Starting 3-phase grading for '{assignment_name}' with {len(submission_data)} submissions")

    # Phase 1: Aggregate Analysis
    log.info("="*60)
    log.info("PHASE 1: AGGREGATE ANALYSIS")
    log.info("="*60)
    self.aggregate_results = self.phase_1_aggregate_analysis(submission_texts, assignment_name)

    # Phase 2: Individual Grading
    log.info("="*60)
    log.info("PHASE 2: INDIVIDUAL GRADING")
    log.info("="*60)
    self.individual_results = self.phase_2_individual_grading(submission_data, self.core_topics)

    # Apply grades to submissions
    self._apply_grades_to_submissions(assignment.submissions, self.individual_results)

    # Phase 3: Report Generation
    log.info("="*60)
    log.info("PHASE 3: REPORT GENERATION")
    log.info("="*60)
    self.phase_3_generate_report(self.aggregate_results, self.individual_results)

  def phase_1_aggregate_analysis(self, submission_texts: List[str], assignment_name: str) -> Dict:
    """
    Phase 1: Analyze all submissions to identify core topics and patterns.

    Args:
        submission_texts: List of all submission text content
        assignment_name: Name of the assignment for context

    Returns:
        Dictionary containing aggregate analysis results
    """
    from Autograder.ai_helper import AI_Helper__OpenAI, AI_Helper__Anthropic
    import json
    import re

    log.info(f"Analyzing {len(submission_texts)} submissions for aggregate insights...")

    if not submission_texts:
      log.warning("No submissions to analyze")
      return {"core_topics": [], "common_themes": "", "key_insights": "", "learning_patterns": "", "teaching_feedback": ""}

    # Get the prompt
    prompt = get_aggregate_analysis_prompt(submission_texts, assignment_name)

    try:
      # Try OpenAI first for better JSON formatting
      log.debug("Attempting aggregate analysis with OpenAI...")
      ai_helper = AI_Helper__OpenAI()
      result = ai_helper.query_ai(prompt, [], max_response_tokens=2000)

      # Store core topics for use in Phase 2
      self.core_topics = result.get("core_topics", [])

      # Apply topic addition hook
      self.core_topics = self.add_manual_topics_hook(self.core_topics)

      log.info(f"✅ Aggregate analysis completed. Identified {len(self.core_topics)} core topics:")
      for i, topic in enumerate(self.core_topics, 1):
        log.info(f"   {i}. {topic}")

      return result

    except Exception as e:
      log.error(f"OpenAI aggregate analysis failed: {e}")
      log.info("Falling back to Anthropic...")

      try:
        # Fallback to Anthropic
        ai_helper = AI_Helper__Anthropic()
        analysis_text = ai_helper.query_ai(prompt, [], max_response_tokens=2000)

        # Try to parse JSON from Anthropic response
        json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
        if json_match:
          result = json.loads(json_match.group())
        else:
          # If no JSON found, create structured response from text
          result = {
            "common_themes": analysis_text,
            "key_insights": "",
            "learning_patterns": "",
            "teaching_feedback": "",
            "core_topics": []
          }

        # Store core topics for use in Phase 2
        self.core_topics = result.get("core_topics", [])

        # Apply topic addition hook
        self.core_topics = self.add_manual_topics_hook(self.core_topics)

        log.info(f"✅ Aggregate analysis completed (Anthropic). Identified {len(self.core_topics)} core topics:")
        for i, topic in enumerate(self.core_topics, 1):
          log.info(f"   {i}. {topic}")

        return result

      except Exception as fallback_error:
        log.error(f"Anthropic fallback also failed: {fallback_error}")
        return {
          "common_themes": f"Error performing analysis: {e}",
          "key_insights": "",
          "learning_patterns": "",
          "teaching_feedback": "",
          "core_topics": []
        }

  def phase_2_individual_grading(self, submission_data: List[Dict], core_topics: List[str]) -> List[Dict]:
    """
    Phase 2: Grade each submission individually using core topics.

    Args:
        submission_data: List of submission data dictionaries
        core_topics: Core topics identified from aggregate analysis

    Returns:
        List of individual grading results
    """
    from Autograder.ai_helper import AI_Helper__OpenAI, AI_Helper__Anthropic
    import json
    import re

    log.info(f"Grading {len(submission_data)} individual submissions...")

    if not core_topics:
      log.warning("No core topics available for individual grading")
      core_topics = ["General class content"]

    individual_results = []
    self.support_needed_students = []

    for i, submission_info in enumerate(submission_data, 1):
      student_id = submission_info.get('student_id')
      student_name = submission_info.get('student_name', 'Unknown')
      submission_text = submission_info.get('text', '')
      word_count = submission_info.get('word_count', 0)

      log.info(f"   Grading {i}/{len(submission_data)}: {student_name} ({word_count} words)")

      if not submission_text.strip():
        # Handle empty submissions
        result = {
          "student_id": student_id,
          "completion_score": 0,
          "relevance_score": 0,
          "explanation_effort_score": 0,
          "topics_covered": [],
          "topics_missing": core_topics,
          "word_count": 0,
          "needs_support": True,
          "support_reason": "No submission content",
          "feedback": "Please submit your learning log content for grading."
        }
      else:
        # Grade the submission using AI
        result = self._grade_individual_submission(submission_text, core_topics, student_id)

      # Calculate length score (separate from AI analysis) and store accurate word count
      result["length_score"] = 2 if word_count >= 250 else 0
      result["accurate_word_count"] = word_count  # Store our accurate count

      # Calculate total grade (ensure all scores are integers)
      total_grade = (
        int(result.get("completion_score", 0)) +
        int(result.get("length_score", 0)) +
        int(result.get("relevance_score", 0)) +
        int(result.get("explanation_effort_score", 0))
      )
      result["total_grade"] = total_grade

      # Track students needing support
      if result.get("needs_support", False):
        self.support_needed_students.append({
          "student_id": student_id,
          "student_name": student_name,
          "reason": result.get("support_reason", "Unknown reason")
        })

      individual_results.append(result)

    log.info(f"✅ Individual grading completed. {len(self.support_needed_students)} students may need support.")

    return individual_results

  def _grade_individual_submission(self, submission_text: str, core_topics: List[str], student_id: str) -> Dict:
    """
    Grade a single submission using AI analysis.

    Args:
        submission_text: The student's submission text
        core_topics: Core topics to check for coverage
        student_id: Student identifier for logging

    Returns:
        Dictionary with grading results
    """
    from Autograder.ai_helper import AI_Helper__OpenAI, AI_Helper__Anthropic
    import json
    import re

    prompt = get_individual_grading_prompt(submission_text, core_topics)

    try:
      # Try OpenAI first
      ai_helper = AI_Helper__OpenAI()
      result = ai_helper.query_ai(prompt, [], max_response_tokens=1000)
      result["student_id"] = student_id
      return result

    except Exception as e:
      log.debug(f"OpenAI failed for {student_id}: {e}. Trying Anthropic...")

      try:
        # Fallback to Anthropic
        ai_helper = AI_Helper__Anthropic()
        analysis_text = ai_helper.query_ai(prompt, [], max_response_tokens=1000)

        # Try to parse JSON from response
        json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
        if json_match:
          result = json.loads(json_match.group())
          result["student_id"] = student_id
          return result
        else:
          # If no JSON, create structured response from text
          return {
            "student_id": student_id,
            "completion_score": 3,  # Default to moderate score
            "relevance_score": 1,
            "explanation_effort_score": 1,
            "topics_covered": [],
            "topics_missing": core_topics,
            "needs_support": False,
            "support_reason": "",
            "feedback": analysis_text[:300] + "..." if len(analysis_text) > 300 else analysis_text
          }

      except Exception as fallback_error:
        log.error(f"Both AI providers failed for {student_id}: {fallback_error}")
        return {
          "student_id": student_id,
          "completion_score": 0,
          "relevance_score": 0,
          "explanation_effort_score": 0,
          "topics_covered": [],
          "topics_missing": core_topics,
          "needs_support": True,
          "support_reason": "Error analyzing submission",
          "feedback": f"Error analyzing submission: {e}"
        }

  def phase_3_generate_report(self, aggregate_results: Dict, individual_results: List[Dict]) -> None:
    """
    Phase 3: Generate comprehensive report with insights and recommendations.

    Args:
        aggregate_results: Results from aggregate analysis
        individual_results: Results from individual grading
    """
    log.info("Generating comprehensive class insights report...")

    # Compile report data
    report_data = self._compile_report_data(aggregate_results, individual_results)

    # Display the report
    self._display_aggregate_insights(report_data)
    self._display_grade_summary(report_data)
    self._display_support_recommendations(report_data)

    # Use output hook for custom delivery
    self.output_report_hook(report_data)

    log.info("✅ Report generation completed!")

  def _compile_report_data(self, aggregate_results: Dict, individual_results: List[Dict]) -> Dict:
    """
    Compile all data needed for the report.

    Args:
        aggregate_results: Results from aggregate analysis
        individual_results: Results from individual grading

    Returns:
        Dictionary containing all compiled report data
    """
    # Calculate grade statistics
    total_grades = [result.get("total_grade", 0) for result in individual_results]
    grade_stats = {
      "total_students": len(individual_results),
      "average_grade": sum(total_grades) / len(total_grades) if total_grades else 0,
      "grade_distribution": self._calculate_grade_distribution(total_grades),
      "students_below_70": sum(1 for grade in total_grades if grade < 7),  # Below 70%
    }

    # Topic coverage analysis
    topic_coverage = self._analyze_topic_coverage(individual_results)

    # Support needs
    support_summary = {
      "students_needing_support": len(self.support_needed_students),
      "support_details": self.support_needed_students
    }

    return {
      "aggregate_insights": aggregate_results,
      "grade_statistics": grade_stats,
      "topic_coverage": topic_coverage,
      "support_summary": support_summary,
      "core_topics": self.core_topics,
      "individual_results": individual_results
    }

  def _calculate_grade_distribution(self, grades: List[float]) -> Dict:
    """Calculate distribution of grades by letter grade ranges."""
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
    """Analyze how well topics were covered across all students."""
    if not self.core_topics:
      return {}

    topic_stats = {}
    for topic in self.core_topics:
      covered_count = sum(1 for result in individual_results
                         if topic in result.get("topics_covered", []))
      topic_stats[topic] = {
        "students_covered": covered_count,
        "coverage_percentage": (covered_count / len(individual_results)) * 100 if individual_results else 0
      }

    return topic_stats

  def _display_aggregate_insights(self, report_data: Dict) -> None:
    """Display aggregate analysis insights."""
    insights = report_data.get("aggregate_insights", {})

    log.info("\n" + "📊 CLASS-WIDE INSIGHTS")
    log.info("="*60)

    if insights.get("common_themes"):
      log.info(f"🎯 Common Themes:\n{insights['common_themes']}")

    if insights.get("key_insights"):
      log.info(f"\n💡 Key Learning Insights:\n{insights['key_insights']}")

    if insights.get("learning_patterns"):
      log.info(f"\n🔄 Learning Patterns:\n{insights['learning_patterns']}")

    if insights.get("teaching_feedback"):
      log.info(f"\n🎓 Teaching Recommendations:\n{insights['teaching_feedback']}")

    # Display core topics
    core_topics = report_data.get("core_topics", [])
    if core_topics:
      log.info(f"\n📝 Core Topics Identified ({len(core_topics)}):")
      for i, topic in enumerate(core_topics, 1):
        coverage = report_data["topic_coverage"].get(topic, {})
        coverage_pct = coverage.get("coverage_percentage", 0)
        log.info(f"   {i}. {topic} ({coverage_pct:.1f}% of students)")

  def _display_grade_summary(self, report_data: Dict) -> None:
    """Display grade summary statistics."""
    stats = report_data.get("grade_statistics", {})

    log.info("\n" + "📈 GRADE SUMMARY")
    log.info("="*60)

    log.info(f"Total Students: {stats.get('total_students', 0)}")
    log.info(f"Average Grade: {stats.get('average_grade', 0):.1f}/10 ({stats.get('average_grade', 0)*10:.1f}%)")

    distribution = stats.get("grade_distribution", {})
    if distribution:
      log.info("\nGrade Distribution:")
      for letter, count in distribution.items():
        percentage = (count / stats.get('total_students', 1)) * 100
        log.info(f"   {letter}: {count} students ({percentage:.1f}%)")

    below_70 = stats.get("students_below_70", 0)
    if below_70 > 0:
      log.info(f"\n⚠️  {below_70} students scored below 70%")

  def _display_support_recommendations(self, report_data: Dict) -> None:
    """Display support recommendations for students."""
    support = report_data.get("support_summary", {})
    students_needing_support = support.get("students_needing_support", 0)

    if students_needing_support > 0:
      log.info("\n" + "🆘 STUDENTS WHO MAY BENEFIT FROM OFFICE HOURS")
      log.info("="*60)

      for student_info in support.get("support_details", []):
        student_id = student_info.get("student_id", "Unknown")
        reason = student_info.get("reason", "No reason provided")
        log.info(f"• {student_id}: {reason}")
    else:
      log.info("\n📈 All students appear to be engaging well with the material!")

  def _apply_grades_to_submissions(self, submissions: List[Submission], individual_results: List[Dict]) -> None:
    """
    Apply calculated grades and feedback to Canvas submission objects.

    Args:
        submissions: Original Canvas submission objects
        individual_results: Grading results with scores and feedback
    """
    # Create a mapping from student_id to results for efficient lookup
    results_by_student = {result.get('student_id'): result for result in individual_results}

    for submission in submissions:
      result = results_by_student.get(submission.student.user_id)
      if result:
        # Use pre-calculated total grade
        total_grade = result.get('total_grade', 0)

        # Create detailed rubric feedback
        feedback_text = self._generate_rubric_feedback(result)
        submission.feedback = Feedback(total_grade, feedback_text)
      else:
        # Fallback for missing results
        submission.feedback = Feedback(0.0, "Error: Could not analyze submission")

  def _generate_rubric_feedback(self, result: Dict) -> str:
    """
    Generate detailed rubric breakdown for student feedback.

    Args:
        result: Individual grading result dictionary

    Returns:
        Formatted feedback string with rubric breakdown
    """
    completion_score = result.get('completion_score', 0)
    length_score = result.get('length_score', 0)
    relevance_score = result.get('relevance_score', 0)
    effort_score = result.get('explanation_effort_score', 0)
    total_score = result.get('total_grade', 0)
    # Always use our accurate word count
    word_count = result.get('accurate_word_count', 0)
    ai_feedback = result.get('feedback', '')

    feedback_lines = [
      "Learning Log Feedback",
      "=" * 50,
      "",
      "GRADE BREAKDOWN:",
      f"• Completion (4 pts): {completion_score}/4 - Depth of reflection and genuine effort",
      f"• Length (2 pts): {length_score}/2 - {'✓ Met 250+ word requirement' if length_score == 2 else '✗ Under 250 words required'}",
      f"• Relevance (2 pts): {relevance_score}/2 - Connection to class material",
      f"• Explanation Effort (2 pts): {effort_score}/2 - Attempt to explain concepts clearly",
      "",
      f"TOTAL SCORE: {total_score}/10 ({(total_score/10)*100:.0f}%)",
      f"Word Count: {word_count} words",
      "",
      "FEEDBACK:",
      ai_feedback
    ]

    return "\n".join(feedback_lines)

  # Hook methods for customization
  def add_manual_topics_hook(self, ai_topics: List[str]) -> List[str]:
    """
    Hook for manually adding or modifying topics after AI analysis.
    Override this method to customize topic selection.

    Args:
        ai_topics: Topics identified by AI analysis

    Returns:
        Final list of topics to use for grading
    """
    return ai_topics

  def output_report_hook(self, report_data: Dict) -> None:
    """
    Hook for customizing report output format.
    Override this method to change how reports are delivered.

    Args:
        report_data: Compiled report data
    """
    # Send to Slack if configured
    self._send_slack_notification(report_data)

    # Also print to console
    self._print_report_to_console(report_data)

  def _send_slack_notification(self, report_data: Dict) -> None:
    """
    Send summary notification to Slack if configured.

    Args:
        report_data: Report data to send
    """
    import os
    import requests

    # Check for Slack configuration
    slack_token = os.getenv('SLACK_BOT_TOKEN')
    slack_channel = self.slack_channel

    if not slack_token or not slack_channel:
      log.debug("Slack not configured (missing SLACK_BOT_TOKEN or course slack_channel)")
      return

    try:
      # Create concise summary
      message = self._create_slack_summary(report_data)

      # Send to Slack
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
        timeout=10
      )

      if response.json().get('ok'):
        log.info("Slack notification sent successfully")
      else:
        log.warning(f"Slack notification failed: {response.json().get('error')}")

    except Exception as e:
      log.warning(f"Failed to send Slack notification: {e}")

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

    # Build summary message with better Slack formatting
    lines = [
      "*Learning Log Grading Complete*",
      "",
      "*Summary:*",
      f"• {stats.get('total_students', 0)} students graded",
      f"• Average: {stats.get('average_grade', 0):.1f}/10 ({stats.get('average_grade', 0)*10:.1f}%)",
    ]

    # Add grade distribution summary
    distribution = stats.get("grade_distribution", {})
    if distribution:
      a_b_count = distribution.get("A", 0) + distribution.get("B", 0)
      c_d_f_count = distribution.get("C", 0) + distribution.get("D", 0) + distribution.get("F", 0)
      lines.append(f"• Grades: {a_b_count} A/B, {c_d_f_count} C/D/F")

    # Add support needs - show ALL students
    support_count = support.get("students_needing_support", 0)
    if support_count > 0:
      lines.append(f"\n*Office Hours Recommended ({support_count} students):*")
      for student_info in support.get("support_details", []):  # No limit - show all
        student_name = student_info.get("student_name", "Unknown Student")
        reason = student_info.get("reason", "")  # No truncation
        lines.append(f"• {student_name}: {reason}")
    else:
      lines.append("\n*Status:* All students engaging well")

    # Add topic insights as a list - show ALL topics
    if topics:
      lines.append(f"\n*Key Topics Mentioned:*")
      for topic in topics:
        lines.append(f"• {topic}")

    # Add teaching insights as a list
    teaching_feedback = insights.get("teaching_feedback", "").strip()
    if teaching_feedback:
      lines.append(f"\n*Teaching Suggestions:*")
      # Split into sentences and make each a bullet point
      sentences = [s.strip() for s in teaching_feedback.split('.') if s.strip()]
      for sentence in sentences:
        lines.append(f"• {sentence}")  # Don't add period since we split on periods

    return "\n".join(lines)

  def _print_report_to_console(self, report_data: Dict) -> None:
    """
    Default implementation for printing report to console.

    Args:
        report_data: Report data to display
    """
    log.info("Report generation completed - see Phase 3 output above")

  # Abstract method implementations (required by base Grader class)
  def execute_grading(self, *args, **kwargs) -> any:
    """
    Not used in text submission grading - phases handle execution.
    """
    return None

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    """
    Not used in text submission grading - phases handle scoring.
    """
    return Feedback(0.0, "TextSubmissionGrader uses phase-based grading")

  def assignment_needs_preparation(self) -> bool:
    """
    Text assignments need preparation to fetch submissions.
    """
    return True