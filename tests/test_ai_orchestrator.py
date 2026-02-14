from Autograder.ai_orchestrator import (ProviderFallbackOrchestrator,
                                        extract_json_object,
                                        parse_anthropic_json_payload)


def test_extract_json_object_parses_first_object_from_text():
  payload = extract_json_object("prefix {\"x\": 1, \"y\": \"ok\"} suffix")
  assert payload == {"x": 1, "y": "ok"}


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
