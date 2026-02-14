import json

from Autograder.graders.text_submission_grader import TextSubmissionGrader
from lms_interface.classes import Student, TextSubmission


def _submission(name: str, user_id: int) -> TextSubmission:
  return TextSubmission(student=Student(name=name, user_id=user_id, _inner=None))


def test_apply_grades_to_submissions_maps_results_by_student_id():
  grader = TextSubmissionGrader()
  submissions = [_submission("Student A", 1), _submission("Student B", 2)]

  individual_results = [{
    "student_id": 1,
    "engagement_score": 3,
    "length_score": 2,
    "relevance_score": 2,
    "explanation_quality_score": 1,
    "total_grade": 8,
    "accurate_word_count": 300,
    "topics_needing_review": [],
    "feedback": "Good effort."
  }]

  grader._apply_grades_to_submissions(submissions, individual_results)

  assert submissions[0].feedback is not None
  assert submissions[0].feedback.percentage_score == 80.0

  assert submissions[1].feedback is not None
  assert submissions[1].feedback.percentage_score == 0.0
  assert "could not analyze" in submissions[1].feedback.comments.lower()


def test_truncate_submission_text_applies_word_limit_first():
  grader = TextSubmissionGrader()
  text = " ".join(["word"] * 1100)

  truncated, was_truncated = grader._truncate_submission_text(
    text, max_words=1000, max_chars=50000)

  assert was_truncated is True
  assert len(truncated.split()) == 1000


def test_phase_1_aggregate_analysis_falls_back_to_openai_when_anthropic_fails(
    monkeypatch):
  from Autograder import ai_helper

  class FailingAnthropic:
    def query_ai(self, *args, **kwargs):
      raise RuntimeError("Anthropic timeout")

  class WorkingOpenAI:
    def query_ai(self, *args, **kwargs):
      return {
        "common_themes": "Core concepts",
        "core_topics": ["Processes"],
        "related_topics": ["Scheduling"],
        "off_topic_indicators": [],
        "commonly_misunderstood_topics": [],
        "misconception_details": "",
        "key_insights": "",
        "teaching_feedback": "",
        "student_questions": []
      }, {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15
      }

  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", FailingAnthropic)
  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", WorkingOpenAI)

  grader = TextSubmissionGrader(phase1_tier="small")
  grader.prefer_anthropic = True
  grader.total_tokens = 0
  grader.total_cost = 0.0
  grader.usage_details = []

  result = grader.phase_1_aggregate_analysis(["notes"], "Weekly Notes", "CST")

  assert result["common_themes"] == "Core concepts"
  assert "Processes" in grader.core_topics
  assert any(detail["provider"] == "openai" for detail in grader.usage_details)


def test_phase_1_aggregate_analysis_falls_back_to_anthropic_when_openai_fails(
    monkeypatch):
  from Autograder import ai_helper

  class FailingOpenAI:
    def query_ai(self, *args, **kwargs):
      raise RuntimeError("OpenAI timeout")

  class WorkingAnthropic:
    def query_ai(self, *args, **kwargs):
      return json.dumps({
        "common_themes": "Fallback themes",
        "core_topics": ["Memory"],
        "related_topics": ["Pointers"],
        "off_topic_indicators": [],
        "commonly_misunderstood_topics": [],
        "misconception_details": "",
        "key_insights": "",
        "teaching_feedback": "",
        "student_questions": []
      }), {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "prompt_tokens": 8,
        "completion_tokens": 4,
        "total_tokens": 12
      }

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", FailingOpenAI)
  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", WorkingAnthropic)

  grader = TextSubmissionGrader(phase1_tier="small")
  grader.prefer_anthropic = False
  grader.total_tokens = 0
  grader.total_cost = 0.0
  grader.usage_details = []

  result = grader.phase_1_aggregate_analysis(["notes"], "Weekly Notes", "CST")

  assert result["common_themes"] == "Fallback themes"
  assert "Memory" in grader.core_topics
  assert any(detail["provider"] == "anthropic"
             for detail in grader.usage_details)
