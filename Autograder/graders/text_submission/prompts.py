"""
AI prompts for text submission grading.

This module contains the default prompts used for the 3-phase grading approach:
1. Aggregate analysis - Identify topics and patterns across all submissions
2. Individual grading - Grade each submission against identified topics
3. Question consolidation - Group similar student questions

These prompts are designed for weekly study notes grading. To customize
prompts for different assignment types, subclass BaseTextSubmissionGrader
and override the _build_*_prompt methods.

Configuration constants are also defined here for easy customization.
"""

from typing import List


# Configuration constants for easy modification
DEFAULT_MAX_TOPICS = 5
DEFAULT_WORD_THRESHOLD = 250
DEFAULT_RUBRIC_TOTAL = 10
DEFAULT_MAX_WORDS = 1000
DEFAULT_MAX_CHARACTERS = 7500

# Rubric component defaults
ENGAGEMENT_POINTS = 4      # Effort to process and explain material
LENGTH_POINTS = 2          # Meeting word count requirement (calculated locally)
RELEVANCE_POINTS = 2       # Coverage of class topics
EXPLANATION_QUALITY_POINTS = 2  # Depth of explanation


def get_aggregate_analysis_prompt(submission_texts: List[str],
                                  assignment_name: str,
                                  course_name: str = "Unknown Course") -> str:
    """
    Get prompt for aggregate analysis of all submissions.

    This prompt asks the AI to analyze all submissions together to identify:
    - Core topics covered this week
    - Related topics (tangential but valid)
    - Off-topic indicators (suggest wrong lecture)
    - Common themes and misconceptions
    - Actual student questions

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

    This prompt grades a single submission against the core topics identified
    in aggregate analysis, using a rubric focused on engagement, relevance,
    and explanation quality.

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

    This prompt groups similar student questions and creates a single,
    clearly-phrased canonical question for each group.

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
