from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from Autograder import bifrost_ai
from Autograder.exceptions import AIProviderError


def test_unified_query_routes_and_validates(monkeypatch):
  monkeypatch.setenv("BIFROST_API_KEY", "test-key")
  monkeypatch.setenv("BIFROST_MODEL_SMALL", "openai/example")
  client = MagicMock()
  client.chat.completions.create.return_value = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content='{"core_topics": ["A"]}'))],
    usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
    model="openai/example")
  factory = MagicMock(return_value=client)
  monkeypatch.setattr(bifrost_ai, "OpenAI", factory)
  result, usage = bifrost_ai.query_ai("test", schema_name="aggregate_analysis", tier="small", max_response_tokens=10)
  assert result["core_topics"] == ["A"]
  assert usage["total_tokens"] == 3
  assert factory.call_args.kwargs["base_url"] == "https://bifrost.p.zxqw.dev/v1"
  assert client.chat.completions.create.call_args.kwargs["model"] == "openai/example"


def test_unified_query_wraps_gateway_failure(monkeypatch):
  monkeypatch.setenv("BIFROST_API_KEY", "test-key")
  monkeypatch.setenv("BIFROST_MODEL_SMALL", "model")
  client = MagicMock()
  client.chat.completions.create.side_effect = RuntimeError("secret error")
  monkeypatch.setattr(bifrost_ai, "OpenAI", lambda **kwargs: client)
  with pytest.raises(AIProviderError, match="Bifrost request failed") as exc:
    bifrost_ai.query_ai("test", schema_name="aggregate_analysis", tier="small", max_response_tokens=10)
  assert "secret error" not in str(exc.value)


def test_invalid_response_is_a_grading_error(monkeypatch):
  monkeypatch.setenv("BIFROST_API_KEY", "test-key")
  monkeypatch.setenv("BIFROST_MODEL_SMALL", "model")
  client = MagicMock()
  client.chat.completions.create.return_value = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
    usage=None, model="model")
  monkeypatch.setattr(bifrost_ai, "OpenAI", lambda **kwargs: client)
  with pytest.raises(AIProviderError, match="Bifrost request failed"):
    bifrost_ai.query_ai("test", schema_name="aggregate_analysis", tier="small", max_response_tokens=10)


@pytest.mark.parametrize('finish_reason,refusal', [('length', None), ('stop', None), ('stop', 'private refusal')])
def test_empty_completion_reports_metadata_without_response_content(monkeypatch, finish_reason, refusal):
  monkeypatch.setenv('BIFROST_API_KEY', 'test-key')
  monkeypatch.setenv('BIFROST_MODEL_MEDIUM', 'medium')
  client = MagicMock()
  client.chat.completions.create.return_value = SimpleNamespace(
    choices=[SimpleNamespace(finish_reason=finish_reason,
      message=SimpleNamespace(content=None, refusal=refusal))],
    usage=SimpleNamespace(completion_tokens=2000,
      completion_tokens_details=SimpleNamespace(reasoning_tokens=2000)))
  monkeypatch.setattr(bifrost_ai, 'OpenAI', lambda **kwargs: client)
  with pytest.raises(AIProviderError) as exc:
    bifrost_ai.query_ai('private student submission', schema_name='aggregate_analysis',
                        tier='medium', max_response_tokens=2000)
  error = str(exc.value)
  assert f'finish_reason={finish_reason}' in error
  assert 'completion_tokens=2000' in error
  assert 'reasoning_tokens=2000' in error
  assert f'refused={bool(refusal)}' in error
  assert 'private' not in error


def test_missing_choices_reports_empty_response(monkeypatch):
  monkeypatch.setenv('BIFROST_API_KEY', 'test-key')
  monkeypatch.setenv('BIFROST_MODEL_SMALL', 'small')
  client = MagicMock()
  client.chat.completions.create.return_value = SimpleNamespace(choices=[], usage=None)
  monkeypatch.setattr(bifrost_ai, 'OpenAI', lambda **kwargs: client)
  with pytest.raises(AIProviderError, match='no JSON content'):
    bifrost_ai.query_ai('test', schema_name='aggregate_analysis', tier='small', max_response_tokens=10)
