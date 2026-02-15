import pytest

from Autograder import exceptions as autograder_exceptions
from Autograder.ai_orchestrator import (ProviderFallbackOrchestrator,
                                        extract_json_object,
                                        parse_anthropic_json_payload,
                                        query_anthropic_text,
                                        query_openai_structured)


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
