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
  with pytest.raises(AIProviderError, match="Bifrost returned invalid JSON"):
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


@pytest.mark.parametrize('kind', ['http', 'connection', 'timeout'])
def test_request_failures_report_safe_diagnostics(monkeypatch, kind):
  import httpx
  from openai import APIStatusError, APIConnectionError, APITimeoutError
  monkeypatch.setenv('BIFROST_API_KEY', 'private-key')
  monkeypatch.setenv('BIFROST_MODEL_MEDIUM', 'medium')
  request = httpx.Request('POST', 'https://gateway.example/v1/chat/completions')
  if kind == 'http':
    response = httpx.Response(400, request=request, headers={'x-request-id': 'req-123'})
    error = APIStatusError('private response text', response=response, body={'private': 'submission'})
  elif kind == 'timeout':
    error = APITimeoutError(request=request)
  else:
    error = APIConnectionError(request=request)
    error.__cause__ = httpx.ConnectError('private connection details')
  client = MagicMock()
  client.chat.completions.create.side_effect = error
  monkeypatch.setattr(bifrost_ai, 'OpenAI', lambda **kwargs: client)
  with pytest.raises(AIProviderError) as raised:
    bifrost_ai.query_ai('private submission', schema_name='aggregate_analysis',
                        tier='medium', max_response_tokens=2000)
  message = str(raised.value)
  assert f'error_type={type(error).__name__}' in message
  assert 'private' not in message
  assert raised.value.__suppress_context__
  if kind == 'http':
    assert 'http_status=400' in message and 'request_id=req-123' in message
  elif kind == 'connection':
    assert 'cause_types=ConnectError' in message and 'DNS' in message
  else:
    assert 'timed out' in message


@pytest.mark.parametrize('content,label', [
  ('private invalid response', 'returned invalid JSON'),
  ('[]', 'response validation failed'),
])
def test_response_failures_are_distinguished_from_request_errors(monkeypatch, content, label):
  monkeypatch.setenv('BIFROST_API_KEY', 'test-key')
  monkeypatch.setenv('BIFROST_MODEL_MEDIUM', 'medium')
  client = MagicMock()
  client.chat.completions.create.return_value = SimpleNamespace(
    choices=[SimpleNamespace(finish_reason='length', message=SimpleNamespace(content=content))],
    usage=SimpleNamespace(completion_tokens=2000), model='medium')
  monkeypatch.setattr(bifrost_ai, 'OpenAI', lambda **kwargs: client)
  with pytest.raises(AIProviderError) as raised:
    bifrost_ai.query_ai('private submission', schema_name='aggregate_analysis',
                        tier='medium', max_response_tokens=2000)
  message = str(raised.value)
  assert label in message and 'finish_reason=length' in message
  assert 'completion_tokens=2000' in message and 'private' not in message
  assert raised.value.__suppress_context__


def test_optional_response_metadata_does_not_fail_valid_grading(monkeypatch):
  monkeypatch.setenv('BIFROST_API_KEY', 'test-key')
  monkeypatch.setenv('BIFROST_MODEL_MEDIUM', 'medium')
  client = MagicMock()
  client.chat.completions.create.return_value = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content='{"core_topics": ["Processes"]}'))])
  monkeypatch.setattr(bifrost_ai, 'OpenAI', lambda **kwargs: client)
  result, usage = bifrost_ai.query_ai('notes', schema_name='aggregate_analysis',
                                    tier='medium', max_response_tokens=2000)
  assert result['core_topics'] == ['Processes']
  assert usage['model'] == 'medium' and usage['total_tokens'] == 0
