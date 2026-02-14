#!env python
"""
Text Submission Grader for Weekly Study Notes

Implements a 3-phase grading approach:
1. Aggregate Analysis - Identify core topics, common misconceptions, and student questions
2. Individual Grading - Grade each submission for engagement, relevance, and explanation quality
3. Report Generation - Generate comprehensive insights and recommendations for instruction

Grading Philosophy:
- Students are graded on effort and engagement, not correctness
- A good faith effort typically results in at least 6/10
- Confusion is not penalized; lack of effort is
- Verbosity is acceptable if the student is genuinely engaging with the material
"""

from typing import List, Dict
import logging
import os
import requests
from datetime import datetime

from Autograder.grader import Grader
from Autograder.registry import GraderRegistry
from Autograder.assignment import Assignment
from Autograder.ai_orchestrator import (ProviderFallbackOrchestrator,
                                         parse_anthropic_json_payload,
                                         query_anthropic_text,
                                         query_openai_structured)
from lms_interface.classes import Feedback, Submission, TextSubmission

log = logging.getLogger(__name__)


# AI Prompts for Text Submission Grading
def get_aggregate_analysis_prompt(submission_texts: List[str],
                                  assignment_name: str,
                                  course_name: str = "Unknown Course") -> str:
  """
    Get prompt for aggregate analysis of all submissions.

    Args:
        submission_texts: List of all submission text content
        assignment_name: Name of the assignment for context
        course_name: Name of the course for context

    Returns:
        Formatted prompt string for aggregate analysis
    """
  num_submissions = len(submission_texts)

  return f"""
You are analyzing student weekly study notes for "{assignment_name}" in {course_name}.

These notes help students prepare for exams by explaining topics to their future selves. Students were asked to list topics, explain what each is and why it matters, and note anything unclear.

Analyze these {num_submissions} submissions and return JSON:

{{
  "common_themes": "What concepts are most students engaging with?",
  "commonly_misunderstood_topics": ["topics", "where", "students", "show", "confusion"],
  "misconception_details": "What are students getting wrong or confused about?",
  "key_insights": "What's clicking well vs. needs more coverage?",
  "teaching_feedback": "What topics might benefit from additional review?",
  "core_topics": ["5", "most", "important", "topics", "covered"],
  "related_topics": ["tangential", "topics", "that", "connect", "to", "core", "material"],
  "off_topic_indicators": ["topics", "that", "suggest", "wrong", "lecture", "or", "not", "attending"],
  "student_questions": ["actual", "questions", "students", "asked", "verbatim"]
}}

For core_topics: Identify the 5 most important general topics from class this week.

For related_topics: Identify topics that are tangential but legitimately connected to this week's material. These are topics students might reasonably discuss because they relate to the core material (e.g., IO devices when discussing file systems, or page tables when discussing virtual memory). Students mentioning these should get credit.

For off_topic_indicators: Identify topics that would suggest a student attended the wrong lecture or is looking at old/future material. These are topics from completely different units that don't connect to this week's content. Only include if you notice any in submissions.

For commonly_misunderstood_topics: Look for topics where explanations contain errors, are vague, or where students express confusion.

For student_questions: Extract actual questions students asked (with '?' or clear interrogative phrasing like "I wonder why..."). Include verbatim. Do NOT include statements about wanting to study more or rhetorical questions.

Submissions:

{chr(10).join([f"---SUBMISSION {i+1}---{chr(10)}{text}" for i, text in enumerate(submission_texts)])}

Return only valid JSON.
"""


def get_individual_grading_prompt(submission_text: str,
                                  core_topics: List[str],
                                  related_topics: List[str] = None,
                                  off_topic_indicators: List[str] = None) -> str:
  """
    Get prompt for individual submission grading.

    Args:
        submission_text: The student's submission text
        core_topics: List of core topics identified from aggregate analysis
        related_topics: List of tangential but valid topics that should get credit
        off_topic_indicators: List of topics that suggest wrong lecture/not attending

    Returns:
        Formatted prompt string for individual grading
    """
  core_str = ", ".join(core_topics)
  related_str = ", ".join(related_topics) if related_topics else ""
  off_topic_str = ", ".join(off_topic_indicators) if off_topic_indicators else ""

  # Build the topics section
  topics_section = f"Core topics from this week: {core_str}"
  if related_str:
    topics_section += f"\nRelated topics (also valid, give credit): {related_str}"
  if off_topic_str:
    topics_section += f"\nOff-topic indicators (suggest wrong lecture - flag if student ONLY discusses these): {off_topic_str}"

  return f"""
Grade this student's weekly study notes. Students explain topics to help their future selves study for exams.

{topics_section}

RUBRIC (8 points - length scored separately):

- Engagement (4 pts): Genuine effort to process and explain material
  - 4: Thorough - worked to understand and explain multiple topics in depth
  - 3: Solid effort - engaged meaningfully, even if some explanations incomplete
  - 2: Superficial - lists topics without real explanation
  - 1: Minimal - barely addresses material
  - 0: No meaningful content

- Relevance (2 pts): Coverage of class material (core OR related topics both count)
  - 2: Covers 3+ topics from core or related lists
  - 1: Covers 1-2 topics
  - 0: Completely off-topic (discusses only unrelated material)

- Explanation Quality (2 pts): Depth of explanation, not correctness
  - 2: Explains WHY concepts matter and HOW they connect
  - 1: Mostly surface-level definitions
  - 0: Just lists terms with no explanation

SCORING EXAMPLES:
- Score 4 engagement: Student covers multiple topics, explains concepts in own words, shows how ideas connect, gives examples or walks through scenarios. Minor gaps or confusion about details are fine.

- Score 3 engagement: "Round-robin gives each process equal time slices. I think this prevents starvation but causes more context switches. Not sure how the OS picks the time quantum though."
  → Engaged and trying to work through concepts, even if some details unclear

- Score 2 engagement: "Round-robin is a scheduling algorithm. Context switching happens when processes change."
  → Technically correct but shallow, no evidence of processing the material

- Score 1 engagement: "We learned about scheduling."
  → Minimal effort

IMPORTANT SCORING GUIDANCE:
- A good faith effort typically results in at least 6/10 overall
- Excellent, thorough work should get 9-10/10 - don't be stingy with top scores for engaged students
- Don't penalize confusion - penalize lack of effort
- Verbosity is fine
- Minor gaps in understanding are NORMAL and should not significantly lower scores
- Students discussing RELATED topics should get full relevance credit - these connect to the material

For needs_support: Set to TRUE only for students who are:
- Clearly disengaged or putting in minimal effort, OR
- Fundamentally lost on most concepts (not just minor gaps), OR
- Discussing completely unrelated material (suggests didn't attend class)
Do NOT flag students who are engaged but have some confusion - that's normal learning. Most students should NOT need support.

Return JSON:
{{
  "engagement_score": "0-4",
  "relevance_score": "0-2",
  "explanation_quality_score": "0-2",
  "topics_covered": ["topics", "addressed", "from", "core", "or", "related"],
  "topics_missing": ["core", "topics", "not", "addressed"],
  "topics_needing_review": ["any", "topics", "with", "confusion", "or", "empty", "list"],
  "off_topic_content": "If student discussed unrelated material, note what. Empty string if on-topic.",
  "misconception_notes": "Brief note if confusion exists, or empty string if understanding seems solid",
  "needs_support": "true/false - see criteria above, most students should be false",
  "support_reason": "reason if needs_support true, else empty",
  "feedback": "Constructive guidance addressed directly to the student using 'you' (not 'the student'): acknowledge what they did well, suggest any topics to review"
}}

Note: topics_needing_review and misconception_notes are informational for the instructor - they do NOT lower the student's score. A student can have minor misconceptions and still earn 10/10 if they engaged thoroughly.

Submission:
{submission_text}

Return only valid JSON.
"""


def get_question_consolidation_prompt(questions_list: List[str]) -> str:
  """
    Get prompt for consolidating similar questions into canonical versions.

    Args:
        questions_list: List of all questions asked by students

    Returns:
        Formatted prompt string for question consolidation
    """
  questions_str = "\n".join(
    [f"{i+1}. {q}" for i, q in enumerate(questions_list)])

  return f"""
You are analyzing questions from student learning logs. Students have asked various questions, many of which are similar but phrased differently. Your task is to consolidate similar questions into clearly phrased canonical versions.

Here are the questions students asked:

{questions_str}

Please consolidate these questions by:
1. Identifying questions that ask about the same underlying concept or topic
2. Grouping similar questions together
3. Creating a single, clearly phrased canonical question for each group
4. Making the canonical questions professional and precise

Return a JSON response with:
{{
  "consolidated_questions": [
    {{
      "canonical_question": "The clearly phrased version of the question",
      "original_questions": ["list", "of", "original", "questions", "that", "map", "to", "this"],
      "topic": "Brief topic name describing the question subject"
    }}
  ]
}}

IMPORTANT:
- Each canonical question should be clear, professional, and well-phrased
- Group questions that are asking about the same concept, even if phrased very differently
- Keep the canonical questions concise but complete
- If a question is unique and doesn't group with others, still include it but with only one original question
- Preserve the intent and scope of the original questions

Return only valid JSON.
"""


# Configuration constants for easy modification
DEFAULT_MAX_TOPICS = 5
DEFAULT_WORD_THRESHOLD = 250
DEFAULT_RUBRIC_TOTAL = 10
DEFAULT_MAX_WORDS = 1000
DEFAULT_MAX_CHARACTERS = 7500

# Rubric component defaults
ENGAGEMENT_POINTS = 4  # Effort to process and explain material
LENGTH_POINTS = 2      # Meeting word count requirement (calculated locally)
RELEVANCE_POINTS = 2   # Coverage of class topics
EXPLANATION_QUALITY_POINTS = 2  # Depth of explanation


class ScoreCalculator:
  """
  Encapsulates score normalization and total-grade calculation for phase 2.
  """

  def __init__(self,
               *,
               word_threshold: int = DEFAULT_WORD_THRESHOLD,
               length_points: int = LENGTH_POINTS):
    self.word_threshold = word_threshold
    self.length_points = length_points

  def apply_scores(self, result: Dict, *, word_count: int,
                   student_name: str) -> Dict:
    # Length score is computed locally from measured word count.
    result["length_score"] = (
      self.length_points if word_count >= self.word_threshold else 0)
    result["accurate_word_count"] = word_count
    result["student_name"] = student_name

    total_grade = (int(result.get("engagement_score", 0)) +
                   int(result.get("length_score", 0)) +
                   int(result.get("relevance_score", 0)) +
                   int(result.get("explanation_quality_score", 0)))
    result["total_grade"] = total_grade
    return result

  @staticmethod
  def needs_support(result: Dict) -> bool:
    value = result.get("needs_support", False)
    if isinstance(value, str):
      value = value.lower() in ['true', '1', 'yes']
    return bool(value)


class RubricGenerator:
  """
  Encapsulates student-facing rubric feedback rendering.
  """

  def __init__(self,
               *,
               engagement_points: int = ENGAGEMENT_POINTS,
               length_points: int = LENGTH_POINTS,
               relevance_points: int = RELEVANCE_POINTS,
               explanation_quality_points: int = EXPLANATION_QUALITY_POINTS,
               rubric_total: int = DEFAULT_RUBRIC_TOTAL):
    self.engagement_points = engagement_points
    self.length_points = length_points
    self.relevance_points = relevance_points
    self.explanation_quality_points = explanation_quality_points
    self.rubric_total = rubric_total

  def generate(self, result: Dict) -> str:
    engagement_score = result.get('engagement_score', 0)
    length_score = result.get('length_score', 0)
    relevance_score = result.get('relevance_score', 0)
    quality_score = result.get('explanation_quality_score', 0)
    total_score = result.get('total_grade', 0)
    word_count = result.get('accurate_word_count', 0)
    ai_feedback = result.get('feedback', '')
    topics_needing_review = result.get('topics_needing_review', [])

    feedback_lines = [
      "Study Notes Feedback", "=" * 50, "", "GRADE BREAKDOWN:",
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


class BatchProcessor:
  """
  Coordinates truncation and the 3-phase text grading pipeline.
  """

  def __init__(self, grader: "BaseTextSubmissionGrader"):
    self.grader = grader

  def run(self, assignment: Assignment, *, assignment_name: str,
          course_name: str) -> bool:
    submission_data = assignment.get_submission_data()
    submission_texts = assignment.get_all_submission_texts()

    if not submission_data:
      log.info(
        f"No submissions to grade for '{assignment_name}' - assignment may be unlocked"
      )
      return False

    truncated_texts, truncation_count = self._truncate_batch(
      submission_texts, submission_data)

    if truncation_count > 0:
      log.info(
        f"Truncated {truncation_count} submission(s) exceeding {DEFAULT_MAX_WORDS} words or {DEFAULT_MAX_CHARACTERS} characters"
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

  def _truncate_batch(self, submission_texts: List[str],
                      submission_data: List[Dict]) -> tuple[List[str], int]:
    truncated_texts = []
    truncation_count = 0

    for text in submission_texts:
      truncated, was_truncated = self.grader._truncate_submission_text(text)
      truncated_texts.append(truncated)
      if was_truncated:
        truncation_count += 1

    for submission_info in submission_data:
      original_text = submission_info.get('text', '')
      truncated, was_truncated = self.grader._truncate_submission_text(
        original_text)
      if was_truncated:
        submission_info['text'] = truncated
        submission_info['was_truncated'] = True

    return truncated_texts, truncation_count


class QuestionConsolidator:
  """
  Encapsulates Phase 2.5 question consolidation with provider fallback.
  """

  def __init__(self, grader: "BaseTextSubmissionGrader"):
    self.grader = grader

  def consolidate(self, all_questions: List[str]) -> List[Dict]:
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
      analysis_text, usage = query_anthropic_text(
        prompt, tier=self.grader.phase25_tier, max_response_tokens=2000)
      self.grader._track_token_usage(usage, operation)

      result = parse_anthropic_json_payload(
        analysis_text, schema_name="question_consolidation")
      if result is None:
        if self.grader.prefer_anthropic:
          log.warning("Could not parse JSON from Anthropic response")
        else:
          log.warning("Could not parse JSON from Anthropic fallback response")
        return []

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


class IndividualGradingProcessor:
  """
  Encapsulates Phase 2 individual grading orchestration.
  """

  def __init__(self, grader: "BaseTextSubmissionGrader"):
    self.grader = grader

  def grade_batch(self, submission_data: List[Dict],
                  core_topics: List[str]) -> List[Dict]:
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

      # Track students needing support.
      if self.grader.score_calculator.needs_support(result):
        self.grader.support_needed_students.append({
          "student_id":
          student_id,
          "student_name":
          student_name,
          "reason":
          result.get("support_reason", "Unknown reason")
        })

      individual_results.append(result)

    log.info(
      f"Individual grading completed. {len(self.grader.support_needed_students)} students may need support."
    )

    # Phase 2.5: Consolidate questions (using questions from aggregate analysis)
    log.info("Phase 2.5/3: Question consolidation")
    student_questions = self.grader.aggregate_results.get("student_questions",
                                                          [])
    self.grader.consolidated_questions = self.grader._consolidate_questions(
      student_questions)

    return individual_results


class AggregateAnalyzer:
  """
  Encapsulates Phase 1 aggregate analysis with provider fallback.
  """

  def __init__(self, grader: "BaseTextSubmissionGrader"):
    self.grader = grader

  def analyze(self, submission_texts: List[str], assignment_name: str,
              course_name: str = "Unknown Course") -> Dict:
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
        # Keep text fallback behavior when Anthropic returns no parseable JSON.
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


class IndividualSubmissionAnalyzer:
  """
  Encapsulates AI evaluation for a single student submission.
  """

  def __init__(self, grader: "BaseTextSubmissionGrader"):
    self.grader = grader

  def analyze(self, submission_text: str, core_topics: List[str],
              student_id: str) -> Dict:
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


class ReportCompiler:
  """
  Encapsulates report-data compilation and summary statistics.
  """

  def __init__(self, grader: "BaseTextSubmissionGrader"):
    self.grader = grader

  def compile(self, aggregate_results: Dict,
              individual_results: List[Dict]) -> Dict:
    total_grades = [
      result.get("total_grade", 0) for result in individual_results
    ]
    grade_stats = {
      "total_students":
      len(individual_results),
      "average_grade":
      sum(total_grades) / len(total_grades) if total_grades else 0,
      "grade_distribution":
      self._calculate_grade_distribution(total_grades),
      "students_below_70":
      sum(1 for grade in total_grades if grade < 7),  # Below 70%
    }

    topic_coverage = self._analyze_topic_coverage(individual_results)

    support_summary = {
      "students_needing_support": len(self.grader.support_needed_students),
      "support_details": self.grader.support_needed_students
    }

    return {
      "aggregate_insights": aggregate_results,
      "grade_statistics": grade_stats,
      "topic_coverage": topic_coverage,
      "support_summary": support_summary,
      "core_topics": self.grader.core_topics,
      "individual_results": individual_results
    }

  def _calculate_grade_distribution(self, grades: List[float]) -> Dict:
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
    if not self.grader.core_topics:
      return {}

    topic_stats = {}
    for topic in self.grader.core_topics:
      covered_count = sum(1 for result in individual_results
                          if topic in result.get("topics_covered", []))
      topic_stats[topic] = {
        "students_covered":
        covered_count,
        "coverage_percentage": (covered_count / len(individual_results)) *
        100 if individual_results else 0
      }

    return topic_stats


class ReportPresenter:
  """
  Encapsulates report presentation/logging output.
  """

  def __init__(self, grader: "BaseTextSubmissionGrader"):
    self.grader = grader

  def present(self, report_data: Dict) -> None:
    self.display_aggregate_insights(report_data)
    self.display_grade_summary(report_data)
    self.display_support_recommendations(report_data)

  def display_aggregate_insights(self, report_data: Dict) -> None:
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


class BaseTextSubmissionGrader(Grader):
  COMPATIBLE_KINDS = {"TextAssignment"}
  """
  Grader for text-based weekly study notes submissions.

  Implements a 3-phase grading approach:
  1. Aggregate Analysis - Identify core topics, misconceptions, and student questions
  2. Individual Grading - Grade each submission for engagement, relevance, and explanation quality
  3. Report Generation - Generate comprehensive insights and recommendations

  Rubric (10 points total):
  - Engagement (4 pts): Effort to process and explain material
  - Length (2 pts): Meeting 250+ word requirement (calculated locally)
  - Relevance (2 pts): Coverage of class topics
  - Explanation Quality (2 pts): Depth of explanation, not correctness
  """

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.core_topics = []
    self.related_topics = []
    self.off_topic_indicators = []
    self.aggregate_results = {}
    self.individual_results = []
    self.support_needed_students = []
    self.consolidated_questions = []
    self.slack_channel = kwargs.get('slack_channel')
    self.records_dir = None
    self.reveal_identity = False
    self.score_calculator = ScoreCalculator()
    self.rubric_generator = RubricGenerator()
    self.batch_processor = BatchProcessor(self)
    self.question_consolidator = QuestionConsolidator(self)
    self.individual_grading_processor = IndividualGradingProcessor(self)
    self.aggregate_analyzer = AggregateAnalyzer(self)
    self.individual_submission_analyzer = IndividualSubmissionAnalyzer(self)
    self.report_compiler = ReportCompiler(self)
    self.report_presenter = ReportPresenter(self)

    # Model tier settings for each phase (small, medium, large)
    # Can be configured via grader settings in YAML
    self.phase1_tier = kwargs.get('phase1_tier', 'small')  # Aggregate analysis
    self.phase2_tier = kwargs.get('phase2_tier', 'small')  # Individual grading
    self.phase25_tier = kwargs.get('phase25_tier', 'small')  # Question consolidation

    log.info(f"{self.__class__.__name__} initialized with tiers: phase1={self.phase1_tier}, phase2={self.phase2_tier}, phase25={self.phase25_tier}")

  def _build_aggregate_analysis_prompt(self, submission_texts: List[str],
                                       assignment_name: str,
                                       course_name: str) -> str:
    return get_aggregate_analysis_prompt(submission_texts, assignment_name,
                                         course_name)

  def _build_individual_grading_prompt(self,
                                       submission_text: str,
                                       core_topics: List[str]) -> str:
    return get_individual_grading_prompt(
      submission_text, core_topics,
      related_topics=self.related_topics,
      off_topic_indicators=self.off_topic_indicators
    )

  def _build_question_consolidation_prompt(self,
                                           all_questions: List[str]) -> str:
    return get_question_consolidation_prompt(all_questions)

  def can_grade_submission(self, submission: Submission) -> bool:
    """
    Text-based graders can only grade TextSubmission objects.
    """
    return isinstance(submission, TextSubmission)

  def _truncate_submission_text(
      self,
      text: str,
      max_words: int = DEFAULT_MAX_WORDS,
      max_chars: int = DEFAULT_MAX_CHARACTERS) -> tuple[str, bool]:
    """
    Truncate submission text to max words or max characters, whichever is shorter.

    Args:
        text: The submission text to truncate
        max_words: Maximum number of words (default: 1000)
        max_chars: Maximum number of characters (default: 7500)

    Returns:
        Tuple of (truncated_text, was_truncated)
    """
    if not text:
      return text, False

    # Split into words
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

  def grade_assignment(self, assignment: Assignment, *args, **kwargs) -> None:
    """
    Override the main grading flow to implement 3-phase approach.
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

  def _consolidate_questions(self,
                             all_questions: List[str]) -> List[Dict]:
    """
    Consolidate similar questions from all submissions into canonical versions.

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
    """
    Compile all data needed for the report.

    Args:
        aggregate_results: Results from aggregate analysis
        individual_results: Results from individual grading

    Returns:
        Dictionary containing all compiled report data
    """
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
      if result:
        # Use pre-calculated total grade (out of 10) and convert to percentage
        total_grade = result.get('total_grade', 0)
        percentage_score = (total_grade / 10.0) * 100.0

        # Create detailed rubric feedback
        feedback_text = self.rubric_generator.generate(result)
        submission.feedback = Feedback(percentage_score, feedback_text)
      else:
        # Fallback for missing results
        submission.feedback = Feedback(0.0,
                                       "Error: Could not analyze submission")

  def _generate_rubric_feedback(self, result: Dict) -> str:
    """Backward-compatible wrapper around RubricGenerator."""
    return self.rubric_generator.generate(result)

  # Hook methods for customization
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
    # Save questions to records directory if configured
    if self.records_dir and self.consolidated_questions:
      self._save_questions_to_records()

    # Send to Slack if configured (includes question file attachment)
    self._send_slack_notification(report_data)

    # Also print to console
    self._print_report_to_console(report_data)

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
      assignment_safe = self.assignment_name.replace(' ',
                                                     '_').replace('/', '-')
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
      assignment_safe = self.assignment_name.replace(' ',
                                                     '_').replace('/', '-')
      filename = f"questions_{course_safe}_{assignment_safe}_{timestamp}.md"

      # Upload file to Slack
      response = requests.post(
        "https://slack.com/api/files.upload",
        headers={"Authorization": f"Bearer {slack_token}"},
        data={
          "channels":
          slack_channel,
          "filename":
          filename,
          "filetype":
          "markdown",
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
      f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*", "",
      f"Total unique question topics: {len(self.consolidated_questions)}", "",
      "---", ""
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
        # Shorten model names for display but keep distinguishing info
        # e.g., "claude-sonnet-4-5-20250514" -> "sonnet-4-5"
        #       "claude-haiku-4-5-20250514" -> "haiku-4-5"
        #       "gpt-4.1-nano" -> "4.1-nano"
        if 'claude' in model.lower():
          # Extract the model variant (haiku, sonnet, opus) and version
          for variant in ['haiku', 'sonnet', 'opus']:
            if variant in model.lower():
              # Try to get version info too
              parts = model.lower().split('-')
              version_parts = [p for p in parts if p[0].isdigit()] if parts else []
              if version_parts:
                return f"{variant}-{version_parts[0]}"
              return variant
          return model[:20]  # Fallback
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
      c_d_f_count = distribution.get("C", 0) + distribution.get(
        "D", 0) + distribution.get("F", 0)
      lines.append(f"- Grades: {a_b_count} A/B, {c_d_f_count} C/D/F")

    # Add support needs - show ALL students with better formatting
    support_count = support.get("students_needing_support", 0)
    if support_count > 0:
      lines.append(f"\n*Office Hours Recommended ({support_count} students):*")
      for i, student_info in enumerate(support.get("support_details", []),
                                       1):  # No limit - show all
        student_name = student_info.get("student_name", "Unknown Student")
        reason = student_info.get("reason", "")  # No truncation
        if reason.strip():
          lines.append(f"{i}. `{student_name}` - {reason}")
        else:
          lines.append(
            f"{i}. `{student_name}` - *(No specific reason provided)*")
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
      # Split into sentences and make each a bullet point
      sentences = [
        s.strip() for s in teaching_feedback.split('.') if s.strip()
      ]
      for sentence in sentences:
        lines.append(
          f"- {sentence}")  # Don't add period since we split on periods

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


@GraderRegistry.register("WeeklyStudyNotesGrader")
class WeeklyStudyNotesGrader(BaseTextSubmissionGrader):
  """
  Concrete weekly-study-notes implementation of the text grading pipeline.
  """
  COMPATIBLE_KINDS = {"TextAssignment"}


@GraderRegistry.register("TextSubmissionGrader")
class TextSubmissionGrader(WeeklyStudyNotesGrader):
  """
  Backward-compatible alias for the weekly study notes grader.
  """
  COMPATIBLE_KINDS = {"TextAssignment"}
