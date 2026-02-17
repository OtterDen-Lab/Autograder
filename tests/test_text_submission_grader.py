import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from Autograder.graders.text_submission_grader import (
  BatchProcessor,
  IndividualGradingProcessor,
  RubricGenerator,
  ScoreCalculator,
  TextSubmissionGrader,
  WeeklyStudyNotesGrader,
)
from Autograder.grader_context import GraderContext
from lms_interface.classes import Student, TextSubmission
from tests.fixtures.llm_responses import (
  CONTENT_FILTER_REFUSAL_TEXT,
  EMPTY_TEXT_RESPONSE,
  PROVIDER_UNAVAILABLE_ERROR,
)


def _submission(name: str, user_id: int) -> TextSubmission:
  return TextSubmission(student=Student(name=name, user_id=user_id, _inner=None))


def test_score_calculator_applies_length_and_total_grade():
  calculator = ScoreCalculator(word_threshold=250, length_points=2)
  result = {
    "engagement_score": 3,
    "relevance_score": 2,
    "explanation_quality_score": 1,
  }

  scored = calculator.apply_scores(result, word_count=300, student_name="Anon 0001")

  assert scored["length_score"] == 2
  assert scored["accurate_word_count"] == 300
  assert scored["student_name"] == "Anon 0001"
  assert scored["total_grade"] == 8


def test_score_calculator_needs_support_handles_string_and_bool():
  assert ScoreCalculator.needs_support({"needs_support": "yes"}) is True
  assert ScoreCalculator.needs_support({"needs_support": "false"}) is False
  assert ScoreCalculator.needs_support({"needs_support": True}) is True
  assert ScoreCalculator.needs_support({}) is False


def test_rubric_generator_includes_topics_needing_review_and_totals():
  generator = RubricGenerator()
  result = {
    "engagement_score": 4,
    "length_score": 2,
    "relevance_score": 2,
    "explanation_quality_score": 2,
    "total_grade": 10,
    "accurate_word_count": 280,
    "topics_needing_review": ["Deadlock"],
    "feedback": "Great synthesis.",
  }

  rendered = generator.generate(result)

  assert "Study Notes Feedback" in rendered
  assert "TOTAL SCORE: 10/10 (100%)" in rendered
  assert "TOPICS TO REVIEW:" in rendered
  assert "- Deadlock" in rendered
  assert "FEEDBACK:" in rendered


def test_batch_processor_truncates_and_runs_three_phase_pipeline():
  class FakeGrader:
    def __init__(self):
      self.aggregate_results = {}
      self.individual_results = []
      self.core_topics = []
      self.calls = []
      self.phase2_submission_data = None

    def _truncate_submission_text(self, text):
      return ("trimmed text", True) if "very long" in text else (text, False)

    def phase_1_aggregate_analysis(self, submission_texts, assignment_name, course_name):
      self.calls.append(("phase1", assignment_name, course_name))
      self.core_topics = ["Processes"]
      return {"core_topics": ["Processes"], "student_questions": []}

    def phase_2_individual_grading(self, submission_data, core_topics):
      self.calls.append(("phase2", list(core_topics)))
      self.phase2_submission_data = submission_data
      return [{
        "student_id": 1,
        "total_grade": 10,
        "feedback": "ok",
      }]

    def _apply_grades_to_submissions(self, submissions, individual_results):
      self.calls.append(("apply", len(submissions), len(individual_results)))

    def phase_3_generate_report(self, aggregate_results, individual_results):
      self.calls.append(("phase3", bool(aggregate_results), len(individual_results)))

  class FakeAssignment:
    def __init__(self):
      self.submissions = [object()]
      self.lms_assignment = SimpleNamespace(name="Weekly Notes")

    def get_submission_data(self):
      return [{
        "student_id": 1,
        "student_name": "Anon 0001",
        "text": "very long notes",
        "word_count": 350,
      }]

    def get_all_submission_texts(self):
      return ["very long notes"]

  grader = FakeGrader()
  processor = BatchProcessor(grader)
  assignment = FakeAssignment()

  ran = processor.run(assignment,
                      assignment_name="Weekly Notes",
                      course_name="CST334")

  assert ran is True
  assert grader.calls[0][0] == "phase1"
  assert grader.calls[1][0] == "phase2"
  assert grader.calls[2][0] == "apply"
  assert grader.calls[3][0] == "phase3"
  assert grader.phase2_submission_data[0]["text"] == "trimmed text"
  assert grader.phase2_submission_data[0]["was_truncated"] is True


def test_batch_processor_returns_false_when_no_submissions():
  class FakeGrader:
    def _truncate_submission_text(self, text):
      return text, False

    def phase_1_aggregate_analysis(self, *_args, **_kwargs):
      raise AssertionError("phase_1 should not run")

  class EmptyAssignment:
    lms_assignment = SimpleNamespace(name="Weekly Notes")
    submissions = []

    def get_submission_data(self):
      return []

    def get_all_submission_texts(self):
      return []

  processor = BatchProcessor(FakeGrader())
  ran = processor.run(EmptyAssignment(),
                      assignment_name="Weekly Notes",
                      course_name="CST334")
  assert ran is False


def test_batch_processor_redacts_pii_before_ai_calls():
  class FakeGrader:
    def __init__(self):
      self.aggregate_results = {}
      self.individual_results = []
      self.core_topics = []
      self.captured_phase1_texts = None
      self.captured_phase2_data = None
      self.redaction_events = []

    def _truncate_submission_text(self, text):
      return text, False

    def _redact_submission_text_for_ai(self, text, **kwargs):
      redacted = text.replace("alice@example.edu", "[REDACTED_EMAIL]")
      if redacted != text:
        self.redaction_events.append({"student_id": kwargs.get("student_id")})
        return redacted, {"total_replacements": 1}
      return redacted, {"total_replacements": 0}

    def phase_1_aggregate_analysis(self, submission_texts, assignment_name, course_name):
      self.captured_phase1_texts = submission_texts
      self.core_topics = ["Processes"]
      return {"core_topics": ["Processes"], "student_questions": []}

    def phase_2_individual_grading(self, submission_data, core_topics):
      self.captured_phase2_data = submission_data
      return [{
        "student_id": 1,
        "total_grade": 10,
        "feedback": "ok",
      }]

    def _apply_grades_to_submissions(self, submissions, individual_results):
      return None

    def phase_3_generate_report(self, aggregate_results, individual_results):
      return None

  class FakeAssignment:
    def __init__(self):
      self.submissions = [object()]
      self.lms_assignment = SimpleNamespace(name="Weekly Notes")

    def get_submission_data(self):
      return [{
        "student_id": 1,
        "student_name": "Alice Example",
        "text": "Reach me at alice@example.edu",
        "word_count": 5,
      }]

  grader = FakeGrader()
  processor = BatchProcessor(grader)

  ran = processor.run(FakeAssignment(),
                      assignment_name="Weekly Notes",
                      course_name="CST334")

  assert ran is True
  assert "[REDACTED_EMAIL]" in grader.captured_phase1_texts[0]
  assert "[REDACTED_EMAIL]" in grader.captured_phase2_data[0]["text"]
  assert grader.redaction_events == [{"student_id": 1}]


def test_individual_grading_processor_tracks_support_and_consolidates_questions():
  class FakeGrader:
    def __init__(self):
      self.reveal_identity = False
      self.score_calculator = ScoreCalculator(word_threshold=250, length_points=2)
      self.support_needed_students = []
      self.aggregate_results = {
        "student_questions": ["How does paging differ from segmentation?"]
      }
      self.consolidated_questions = []

    def _grade_individual_submission(self, submission_text, core_topics, student_id):
      return {
        "student_id": student_id,
        "engagement_score": 3,
        "relevance_score": 2,
        "explanation_quality_score": 1,
        "topics_covered": core_topics,
        "topics_missing": [],
        "topics_needing_review": [],
        "misconception_notes": "",
        "needs_support": "true" if student_id == 2 else "false",
        "support_reason": "Needs follow-up" if student_id == 2 else "",
        "feedback": "Reasonable effort."
      }

    def _consolidate_questions(self, questions):
      return [{"topic": "Memory", "questions": questions}]

  processor = IndividualGradingProcessor(FakeGrader())
  results = processor.grade_batch([
    {
      "student_id": 1,
      "student_name": "Anon 0001",
      "text": "Detailed study notes about paging and segmentation.",
      "word_count": 280,
    },
    {
      "student_id": 2,
      "student_name": "Anon 0002",
      "text": "Short note.",
      "word_count": 120,
    },
  ], ["Memory"])

  assert len(results) == 2
  assert processor.grader.support_needed_students == [{
    "student_id": 2,
    "student_name": "Anon 0002",
    "reason": "Needs follow-up"
  }]
  assert processor.grader.consolidated_questions[0]["topic"] == "Memory"
  assert results[0]["length_score"] == 2
  assert results[1]["length_score"] == 0


def test_text_submission_grader_redacts_common_pii_patterns():
  grader = TextSubmissionGrader()
  text = ("Name: Alice Example\n"
          "email alice@example.edu\n"
          "phone 831-555-1212\n"
          "student id: 1234567")
  redacted, counts = grader._redact_submission_text_for_ai(
    text,
    student_name="Alice Example",
    student_id=1234567,
  )

  assert "alice@example.edu" not in redacted
  assert "831-555-1212" not in redacted
  assert "Alice Example" not in redacted
  assert "1234567" not in redacted
  assert counts["total_replacements"] >= 4


def test_text_submission_grader_does_not_redact_substrings_inside_words():
  grader = TextSubmissionGrader()
  text = "I studied algorithm design today."
  redacted, counts = grader._redact_submission_text_for_ai(
    text,
    student_name="Al",
    student_id=None,
  )

  assert redacted == text
  assert counts["total_replacements"] == 0


def test_text_submission_grader_is_alias_of_weekly_notes_grader():
  assert issubclass(TextSubmissionGrader, WeeklyStudyNotesGrader)


def test_compile_report_data_calculates_grade_and_topic_summaries():
  grader = TextSubmissionGrader()
  grader.core_topics = ["Processes", "Memory"]
  grader.support_needed_students = [{
    "student_id": 2,
    "student_name": "Anon 0002",
    "reason": "Needs follow-up"
  }]

  report = grader._compile_report_data({
    "common_themes": "Students discussed scheduling",
  }, [
    {
      "student_id": 1,
      "total_grade": 10,
      "topics_covered": ["Processes", "Memory"]
    },
    {
      "student_id": 2,
      "total_grade": 7,
      "topics_covered": ["Processes"]
    },
  ])

  assert report["grade_statistics"]["total_students"] == 2
  assert report["grade_statistics"]["average_grade"] == 8.5
  assert report["grade_statistics"]["grade_distribution"] == {
    "A": 1,
    "B": 0,
    "C": 1,
    "D": 0,
    "F": 0
  }
  assert report["topic_coverage"]["Processes"]["students_covered"] == 2
  assert report["topic_coverage"]["Memory"]["students_covered"] == 1
  assert report["support_summary"]["students_needing_support"] == 1


def test_compile_report_data_excludes_failed_submissions_from_grade_aggregates():
  grader = TextSubmissionGrader()
  grader.core_topics = ["Processes"]

  report = grader._compile_report_data({}, [
    {
      "student_id": 1,
      "total_grade": 9,
      "topics_covered": ["Processes"]
    },
    {
      "student_id": 2,
      "grading_failed": True,
      "support_reason": "Provider retries exhausted",
      "topics_covered": []
    },
  ])

  stats = report["grade_statistics"]
  assert stats["total_students"] == 2
  assert stats["graded_students"] == 1
  assert stats["ungraded_students"] == 1
  assert stats["average_grade"] == 9
  assert stats["grade_distribution"] == {
    "A": 1,
    "B": 0,
    "C": 0,
    "D": 0,
    "F": 0
  }


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

  assert submissions[1].feedback is None
  assert submissions[1].extra_info.get("grading_error") == "missing_grading_result"


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


def test_grade_individual_submission_falls_back_to_openai_when_anthropic_fails(
    monkeypatch):
  from Autograder import ai_helper

  class FailingAnthropic:
    calls = 0

    def query_ai(self, *args, **kwargs):
      type(self).calls += 1
      raise RuntimeError("Anthropic timeout")

  class WorkingOpenAI:
    calls = 0

    def query_ai(self, *args, **kwargs):
      type(self).calls += 1
      return {
        "engagement_score": 4,
        "relevance_score": 2,
        "explanation_quality_score": 2,
        "topics_covered": ["Processes"],
        "topics_missing": [],
        "topics_needing_review": [],
        "off_topic_content": "",
        "misconception_notes": "",
        "needs_support": False,
        "support_reason": "",
        "feedback": "Strong effort."
      }, {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19
      }

  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", FailingAnthropic)
  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", WorkingOpenAI)

  grader = TextSubmissionGrader(phase2_tier="small")
  grader.prefer_anthropic = True
  grader.total_tokens = 0
  grader.total_cost = 0.0
  grader.usage_details = []
  grader.related_topics = []
  grader.off_topic_indicators = []

  result = grader._grade_individual_submission(
    "I studied processes and scheduling.",
    ["Processes"],
    student_id=42,
  )

  assert result["student_id"] == 42
  assert result["engagement_score"] == 4
  assert FailingAnthropic.calls == 1
  assert WorkingOpenAI.calls == 1
  assert any(detail["provider"] == "openai" for detail in grader.usage_details)


def test_grade_individual_submission_falls_back_to_anthropic_when_openai_fails(
    monkeypatch):
  from Autograder import ai_helper

  class FailingOpenAI:
    calls = 0

    def query_ai(self, *args, **kwargs):
      type(self).calls += 1
      raise RuntimeError("OpenAI timeout")

  class WorkingAnthropic:
    calls = 0

    def query_ai(self, *args, **kwargs):
      type(self).calls += 1
      return json.dumps({
        "engagement_score": 3,
        "relevance_score": 1,
        "explanation_quality_score": 1,
        "topics_covered": ["Memory"],
        "topics_missing": [],
        "topics_needing_review": [],
        "off_topic_content": "",
        "misconception_notes": "",
        "needs_support": False,
        "support_reason": "",
        "feedback": "Decent start."
      }), {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "prompt_tokens": 9,
        "completion_tokens": 5,
        "total_tokens": 14
      }

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", FailingOpenAI)
  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", WorkingAnthropic)

  grader = TextSubmissionGrader(phase2_tier="small")
  grader.prefer_anthropic = False
  grader.total_tokens = 0
  grader.total_cost = 0.0
  grader.usage_details = []
  grader.related_topics = []
  grader.off_topic_indicators = []

  result = grader._grade_individual_submission(
    "I worked through memory management examples.",
    ["Memory"],
    student_id=99,
  )

  assert result["student_id"] == 99
  assert result["engagement_score"] == 3
  assert FailingOpenAI.calls == 1
  assert WorkingAnthropic.calls == 1
  assert any(detail["provider"] == "anthropic"
             for detail in grader.usage_details)


def test_grade_individual_submission_returns_safe_default_when_both_providers_fail(
    monkeypatch):
  from Autograder import ai_helper

  class FailingOpenAI:
    def query_ai(self, *args, **kwargs):
      raise RuntimeError("OpenAI timeout")

  class FailingAnthropic:
    def query_ai(self, *args, **kwargs):
      raise RuntimeError("Anthropic timeout")

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", FailingOpenAI)
  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", FailingAnthropic)

  grader = TextSubmissionGrader(phase2_tier="small")
  grader.prefer_anthropic = False
  grader.related_topics = []
  grader.off_topic_indicators = []

  result = grader._grade_individual_submission(
    "I tried to write about scheduling.",
    ["Scheduling"],
    student_id=7,
  )

  assert result["student_id"] == 7
  assert result["grading_failed"] is True
  assert result["needs_support"] is True
  assert result["support_reason"] == "LLM grading failed after retries"


def test_question_consolidation_falls_back_to_anthropic_when_openai_fails(
    monkeypatch):
  from Autograder import ai_helper

  class FailingOpenAI:
    calls = 0

    def query_ai(self, *args, **kwargs):
      type(self).calls += 1
      raise RuntimeError("OpenAI timeout")

  class WorkingAnthropic:
    calls = 0

    def query_ai(self, *args, **kwargs):
      type(self).calls += 1
      return json.dumps({
        "consolidated_questions": [{
          "canonical_question": "How does round-robin avoid starvation?",
          "original_questions": [
            "Does round-robin prevent starvation?",
            "How does RR avoid starvation?"
          ],
          "topic": "Scheduling"
        }]
      }), {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "prompt_tokens": 11,
        "completion_tokens": 6,
        "total_tokens": 17
      }

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", FailingOpenAI)
  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", WorkingAnthropic)

  grader = TextSubmissionGrader(phase25_tier="small")
  grader.prefer_anthropic = False
  grader.total_tokens = 0
  grader.total_cost = 0.0
  grader.usage_details = []

  consolidated = grader._consolidate_questions([
    "Does round-robin prevent starvation?",
    "How does RR avoid starvation?"
  ])

  assert len(consolidated) == 1
  assert consolidated[0]["topic"] == "Scheduling"
  assert FailingOpenAI.calls == 1
  assert WorkingAnthropic.calls == 1
  assert any(detail["provider"] == "anthropic"
             for detail in grader.usage_details)


def test_phase_1_aggregate_analysis_degrades_gracefully_on_invalid_json(
    monkeypatch):
  from Autograder import ai_helper

  class InvalidJsonAnthropic:
    def query_ai(self, *args, **kwargs):
      return "{ definitely not valid json", {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13
      }

  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", InvalidJsonAnthropic)

  grader = TextSubmissionGrader(phase1_tier="small")
  grader.prefer_anthropic = True
  grader.total_tokens = 0
  grader.total_cost = 0.0
  grader.usage_details = []

  result = grader.phase_1_aggregate_analysis(["notes"], "Weekly Notes", "CST")

  assert result["common_themes"] == "{ definitely not valid json"
  assert result["core_topics"] == []
  assert grader.core_topics == []


def test_grade_individual_submission_empty_response_marks_failed_and_reports(
    monkeypatch):
  from Autograder import ai_helper

  class EmptyOpenAI:
    def query_ai(self, *args, **kwargs):
      raise ValueError("Empty response from provider")

  class EmptyAnthropic:
    def query_ai(self, *args, **kwargs):
      return EMPTY_TEXT_RESPONSE, {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "prompt_tokens": 8,
        "completion_tokens": 0,
        "total_tokens": 8
      }

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", EmptyOpenAI)
  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", EmptyAnthropic)

  grader = TextSubmissionGrader(phase2_tier="small")
  grader.prefer_anthropic = False
  grader.related_topics = []
  grader.off_topic_indicators = []
  grader._report_individual_grading_failure = MagicMock()

  result = grader._grade_individual_submission(
    "I tried to submit notes.",
    ["Scheduling"],
    student_id=8,
  )

  assert result["student_id"] == 8
  assert result["grading_failed"] is True
  assert result["support_reason"] == "LLM grading failed after retries"
  grader._report_individual_grading_failure.assert_called_once()


def test_phase_2_individual_grading_continues_after_single_timeout(
    monkeypatch):
  from Autograder import ai_helper

  class ConditionalOpenAI:
    def query_ai(self, prompt, *args, **kwargs):
      if "timeout-marker" in prompt:
        raise TimeoutError("request timed out")
      return {
        "engagement_score": 3,
        "relevance_score": 2,
        "explanation_quality_score": 1,
        "topics_covered": ["Scheduling"],
        "topics_missing": [],
        "topics_needing_review": [],
        "off_topic_content": "",
        "misconception_notes": "",
        "needs_support": False,
        "support_reason": "",
        "feedback": "Good effort."
      }, {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "prompt_tokens": 9,
        "completion_tokens": 5,
        "total_tokens": 14
      }

  class TimeoutAnthropic:
    def query_ai(self, prompt, *args, **kwargs):
      if "timeout-marker" in prompt:
        raise TimeoutError("request timed out")
      return json.dumps({
        "engagement_score": 3,
        "relevance_score": 2,
        "explanation_quality_score": 1,
        "topics_covered": ["Scheduling"],
        "topics_missing": [],
        "topics_needing_review": [],
        "off_topic_content": "",
        "misconception_notes": "",
        "needs_support": False,
        "support_reason": "",
        "feedback": "Good effort."
      }), {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "prompt_tokens": 9,
        "completion_tokens": 5,
        "total_tokens": 14
      }

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", ConditionalOpenAI)
  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", TimeoutAnthropic)

  grader = TextSubmissionGrader(phase2_tier="small")
  grader.prefer_anthropic = False
  grader.related_topics = []
  grader.off_topic_indicators = []
  grader.aggregate_results = {"student_questions": []}
  grader.total_tokens = 0
  grader.total_cost = 0.0
  grader.usage_details = []
  grader._report_individual_grading_failure = MagicMock()

  results = grader.phase_2_individual_grading([
    {
      "student_id": 1,
      "student_name": "Anon 0001",
      "text": "timeout-marker",
      "word_count": 280,
    },
    {
      "student_id": 2,
      "student_name": "Anon 0002",
      "text": "normal submission content",
      "word_count": 280,
    },
  ], ["Scheduling"])

  assert len(results) == 2
  assert results[0]["grading_failed"] is True
  assert results[1].get("grading_failed", False) is False
  assert results[1]["total_grade"] >= 0
  assert any(s["student_id"] == 1 for s in grader.support_needed_students)
  grader._report_individual_grading_failure.assert_called_once()


def test_phase_2_individual_grading_logs_content_filter_refusal(
    monkeypatch):
  from Autograder import ai_helper
  from Autograder.graders.text_submission import grader as text_grader_module

  class RefusalOpenAI:
    def query_ai(self, *args, **kwargs):
      raise RuntimeError(CONTENT_FILTER_REFUSAL_TEXT)

  class RefusalAnthropic:
    def query_ai(self, *args, **kwargs):
      raise RuntimeError(CONTENT_FILTER_REFUSAL_TEXT)

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", RefusalOpenAI)
  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", RefusalAnthropic)
  mock_log_error = MagicMock()
  monkeypatch.setattr(text_grader_module.log, "error", mock_log_error)

  grader = TextSubmissionGrader(phase2_tier="small")
  grader.prefer_anthropic = False
  grader.related_topics = []
  grader.off_topic_indicators = []
  grader.aggregate_results = {"student_questions": []}
  grader.total_tokens = 0
  grader.total_cost = 0.0
  grader.usage_details = []
  grader._report_individual_grading_failure = MagicMock()

  results = grader.phase_2_individual_grading([
    {
      "student_id": 77,
      "student_name": "Anon 0077",
      "text": "notes that trigger refusal",
      "word_count": 300,
    },
  ], ["Processes"])

  assert results[0]["grading_failed"] is True
  assert any("Both AI providers failed for 77" in str(c.args[0])
             for c in mock_log_error.call_args_list)
  grader._report_individual_grading_failure.assert_called_once()


def test_grade_individual_submission_provider_unavailable_reports_clear_error(
    monkeypatch):
  from Autograder import ai_helper

  class UnavailableOpenAI:
    def query_ai(self, *args, **kwargs):
      raise ConnectionError(PROVIDER_UNAVAILABLE_ERROR)

  class UnavailableAnthropic:
    def query_ai(self, *args, **kwargs):
      raise ConnectionError(PROVIDER_UNAVAILABLE_ERROR)

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", UnavailableOpenAI)
  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", UnavailableAnthropic)

  captured_failure_reasons = []

  def _capture_failure(_student_id, reason):
    captured_failure_reasons.append(reason)

  grader = TextSubmissionGrader(phase2_tier="small")
  grader.prefer_anthropic = False
  grader.related_topics = []
  grader.off_topic_indicators = []
  grader._report_individual_grading_failure = _capture_failure

  result = grader._grade_individual_submission(
    "submission text",
    ["Processes"],
    student_id=55,
  )

  assert result["grading_failed"] is True
  assert len(captured_failure_reasons) == 1
  assert "OpenAI request failed" in captured_failure_reasons[0]
  assert "Check API credentials, connectivity, and provider availability" in captured_failure_reasons[
    0]


def test_text_submission_grader_uses_custom_prompt_template():
  grader = TextSubmissionGrader(
    prompt_templates={
      "aggregate_analysis":
      "Course={course_name}; Assignment={assignment_name}; Count={num_submissions}"
    })

  rendered = grader._build_aggregate_analysis_prompt(["one", "two"],
                                                     "Weekly Notes", "CST334")
  assert rendered == "Course=CST334; Assignment=Weekly Notes; Count=2"


def test_text_submission_grader_uses_custom_rubric_points_and_descriptions():
  grader = TextSubmissionGrader(
    rubric={
      "engagement": {
        "points": 5,
        "description": "Engagement depth"
      },
      "length": {
        "points": 1,
        "description": "Minimum-length completion"
      },
      "relevance": {
        "points": 3,
        "description": "Topic alignment"
      },
      "explanation_quality": {
        "points": 1,
        "description": "Explanation clarity"
      },
      "word_threshold": 100,
      "total_points": 10,
    })

  scored = grader.score_calculator.apply_scores({
    "engagement_score": 5,
    "relevance_score": 3,
    "explanation_quality_score": 1,
  }, word_count=120, student_name="Anon 0001")

  assert scored["length_score"] == 1
  assert scored["total_grade"] == 10

  feedback = grader.rubric_generator.generate(scored)
  assert "Engagement depth" in feedback
  assert "Minimum-length completion" in feedback
  assert "met 100 words" in feedback


def test_text_submission_grader_prefers_typed_grader_context_for_runtime_metadata():
  from Autograder.assignment import Assignment_TextAssignment

  grader = TextSubmissionGrader()

  context = GraderContext(
    course_name="CST-Context",
    assignment_name="Context Assignment",
    reveal_identity=True,
    prefer_anthropic=True,
    records_dir="/tmp/context-records",
    slack_channel="C-CONTEXT",
  )

  assignment = Assignment_TextAssignment(
    lms_assignment=SimpleNamespace(name="Weekly Notes"))
  assignment.submissions = []
  assignment.get_submission_data = lambda: []

  grader.grade_assignment(assignment, grader_context=context)

  assert grader.course_name == "CST-Context"
  assert grader.assignment_name == "Context Assignment"
  assert grader.reveal_identity is True
  assert grader.prefer_anthropic is True
  assert grader.records_dir == "/tmp/context-records"
  assert grader.slack_channel == "C-CONTEXT"
