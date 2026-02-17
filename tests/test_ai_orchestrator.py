import pytest

from Autograder import exceptions as autograder_exceptions
from Autograder.ai_orchestrator import (ProviderFallbackOrchestrator,
                                        extract_json_object,
                                        parse_anthropic_json_payload,
                                        query_anthropic_text,
                                        query_openai_structured)
from tests.fixtures.llm_mocks import MockRateLimitError


def test_extract_json_object_parses_first_object_from_text():
  payload = extract_json_object("prefix {\"x\": 1, \"y\": \"ok\"} suffix")
  assert payload == {"x": 1, "y": "ok"}


def test_extract_json_object_skips_non_json_braces_and_parses_next_payload():
  payload = extract_json_object(
    "prefix {not json} middle {\"x\": 1, \"y\": \"ok\"} suffix {ignored}")
  assert payload == {"x": 1, "y": "ok"}


def test_parse_anthropic_json_payload_handles_extra_braces_in_surrounding_text():
  text = (
    "analysis block {not-json}\n"
    "```json\n"
    "{\"common_themes\": \"threads\"}\n"
    "```\n"
    "trailer {still-not-json}"
  )
  parsed = parse_anthropic_json_payload(text, schema_name="aggregate_analysis")

  assert parsed is not None
  assert parsed["common_themes"] == "threads"


def test_provider_fallback_orchestrator_uses_fallback_on_primary_failure():
  events = []

  def _run_openai():
    events.append("openai")
    raise RuntimeError("openai down")

  def _run_anthropic():
    events.append("anthropic")
    return "fallback-result"

  orchestrator = ProviderFallbackOrchestrator(prefer_anthropic=False)
  result = orchestrator.run(run_openai=_run_openai,
                            run_anthropic=_run_anthropic)

  assert result == "fallback-result"
  assert events == ["openai", "anthropic"]


def test_provider_fallback_orchestrator_uses_both_fail_handler():
  def _run_openai():
    raise RuntimeError("openai down")

  def _run_anthropic():
    raise RuntimeError("anthropic down")

  orchestrator = ProviderFallbackOrchestrator(prefer_anthropic=False)
  result = orchestrator.run(run_openai=_run_openai,
                            run_anthropic=_run_anthropic,
                            on_both_fail=lambda primary, secondary: f"{primary}|{secondary}")

  assert "openai down" in result
  assert "anthropic down" in result


def test_parse_anthropic_json_payload_validates_schema():
  text = "```json\n{\"common_themes\": \"threads\"}\n```"
  parsed = parse_anthropic_json_payload(text, schema_name="aggregate_analysis")

  assert parsed is not None
  assert parsed["common_themes"] == "threads"
  assert parsed["core_topics"] == []


def test_query_openai_structured_wraps_provider_failures(monkeypatch):
  from Autograder import ai_helper

  class FailingOpenAI:
    def query_ai(self, *args, **kwargs):
      raise RuntimeError("provider outage")

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", FailingOpenAI)

  with pytest.raises(autograder_exceptions.AIProviderError,
                     match="OpenAI request failed"):
    query_openai_structured("hello",
                            schema_name="aggregate_analysis",
                            tier="small",
                            max_response_tokens=100)


def test_query_anthropic_text_wraps_provider_failures(monkeypatch):
  from Autograder import ai_helper

  class FailingAnthropic:
    def query_ai(self, *args, **kwargs):
      raise RuntimeError("provider outage")

  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", FailingAnthropic)

  with pytest.raises(autograder_exceptions.AIProviderError,
                     match="Anthropic request failed"):
    query_anthropic_text("hello", tier="small", max_response_tokens=100)


def test_query_openai_structured_retries_rate_limit_when_enabled(monkeypatch):
  from Autograder import ai_helper
  from Autograder import ai_orchestrator

  class FlakyOpenAI:
    calls = 0

    def query_ai(self, *args, **kwargs):
      del args, kwargs
      type(self).calls += 1
      if type(self).calls == 1:
        raise MockRateLimitError("rate limit")
      return {
        "common_themes": "Recovered",
        "core_topics": [],
        "related_topics": [],
        "off_topic_indicators": [],
        "commonly_misunderstood_topics": [],
        "misconception_details": "",
        "key_insights": "",
        "teaching_feedback": "",
        "student_questions": []
      }, {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2
      }

  monkeypatch.setattr(ai_helper, "AI_Helper__OpenAI", FlakyOpenAI)
  monkeypatch.setattr(ai_orchestrator.time, "sleep", lambda *_: None)
  monkeypatch.setattr(ai_orchestrator.random, "uniform", lambda *_: 0.0)

  result, _usage = query_openai_structured(
    "hello",
    schema_name="aggregate_analysis",
    tier="small",
    max_response_tokens=100,
    max_rate_limit_retries=1,
  )

  assert FlakyOpenAI.calls == 2
  assert result["common_themes"] == "Recovered"


def test_query_anthropic_text_rate_limit_fails_fast_by_default(monkeypatch):
  from Autograder import ai_helper

  class RateLimitedAnthropic:
    def query_ai(self, *args, **kwargs):
      raise MockRateLimitError("rate limit")

  monkeypatch.setattr(ai_helper, "AI_Helper__Anthropic", RateLimitedAnthropic)

  with pytest.raises(autograder_exceptions.AIProviderError,
                     match="Anthropic rate limited"):
    query_anthropic_text("hello", tier="small", max_response_tokens=100)
