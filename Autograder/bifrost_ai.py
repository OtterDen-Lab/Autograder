"""Unified structured AI requests through the Bifrost gateway."""
import json
import os
import re

import dotenv
from openai import OpenAI, APIConnectionError, APITimeoutError

from Autograder.ai_helper import validate_response_payload
from Autograder.exceptions import AIProviderError


def _response_metadata(response):
  """Return diagnostic metadata without exposing response or submission text."""
  choices = getattr(response, 'choices', None) or []
  choice = choices[0] if choices else None
  reason = getattr(choice, 'finish_reason', None)
  if reason is not None and (not isinstance(reason, str)
      or reason not in {'stop', 'length', 'tool_calls', 'function_call', 'content_filter'}):
    reason = 'other'
  usage = getattr(response, 'usage', None)
  tokens = getattr(usage, 'completion_tokens', None)
  return f'finish_reason={reason}, completion_tokens={tokens if type(tokens) is int else None}'


def _failure_details(error):
  details = [f'error_type={type(error).__name__}']
  status = getattr(error, 'status_code', None)
  if type(status) is int:
    details.append(f'http_status={status}')
  request_id = getattr(error, 'request_id', None)
  if isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
    details.append(f'request_id={request_id}')
  cause = error.__cause__
  causes = []
  for _ in range(4):
    if cause is None:
      break
    causes.append(type(cause).__name__)
    cause = cause.__cause__
  if causes:
    details.append('cause_types=' + '/'.join(causes))
  return ', '.join(details)


def query_ai(prompt, *, schema_name, tier, max_response_tokens):
  """Return a validated response and token usage; Bifrost owns model fallback."""
  dotenv.load_dotenv(os.path.expanduser("~/.tokens/autograder.env"))
  key = os.getenv("BIFROST_API_KEY")
  model = os.getenv(f"BIFROST_MODEL_{tier.upper()}")
  if not key or not model:
    raise AIProviderError(
      f"BIFROST_API_KEY and BIFROST_MODEL_{tier.upper()} must be configured")
  stage = 'request'
  response = None
  try:
    client = OpenAI(api_key=key, base_url=os.getenv(
      "BIFROST_BASE_URL", "https://bifrost.p.zxqw.dev/v1"), max_retries=0)
    response = client.chat.completions.create(
      model=model, messages=[{"role": "user", "content": prompt}],
      response_format={"type": "json_object"}, max_tokens=max_response_tokens)
    choices = response.choices or []
    choice = choices[0] if choices else None
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
      finish_reason = getattr(choice, "finish_reason", None)
      usage = response.usage
      completion_tokens = getattr(usage, "completion_tokens", None)
      details = getattr(usage, "completion_tokens_details", None)
      reasoning_tokens = getattr(details, "reasoning_tokens", None)
      refused = bool(getattr(message, "refusal", None))
      raise AIProviderError(
        f"Bifrost returned no JSON content (tier={tier}, schema={schema_name}, "
        f"finish_reason={finish_reason}, completion_tokens={completion_tokens}, "
        f"reasoning_tokens={reasoning_tokens}, refused={refused}, "
        f"max_response_tokens={max_response_tokens}). "
        "Inspect the Bifrost request log for model routing and response details; "
        "a length finish reason can indicate the output budget was exhausted.")
    stage = 'JSON decoding'
    payload = json.loads(content)
    stage = 'response validation'
    result = validate_response_payload(payload, schema_name=schema_name, strict=True)
    usage = getattr(response, 'usage', None)
    return result, {
      "provider": "bifrost", "model": getattr(response, 'model', None) or model,
      "prompt_tokens": getattr(usage, 'prompt_tokens', 0) or 0,
      "completion_tokens": getattr(usage, 'completion_tokens', 0) or 0,
      "total_tokens": getattr(usage, 'total_tokens', 0) or 0,
    }
  except AIProviderError:
    raise
  except Exception as e:
    details = _failure_details(e)
    if stage != 'request':
      label = 'returned invalid JSON' if stage == 'JSON decoding' else 'response validation failed'
      raise AIProviderError(
        f'Bifrost {label} (tier={tier}, schema={schema_name}, {details}, '
        f'{_response_metadata(response)}, max_response_tokens={max_response_tokens}); '
        'the request returned a response, but the grader could not use it. '
        'Check the response format and completion budget in the Bifrost request log.') from None
    if isinstance(e, APITimeoutError):
      hint = 'request timed out; check gateway reachability and upstream response latency'
    elif isinstance(e, APIConnectionError):
      hint = 'check connectivity, DNS, TLS certificates, proxy settings, and gateway reachability'
    else:
      hint = 'check gateway availability, model routing, and credentials'
    raise AIProviderError(
      f'Bifrost request failed (tier={tier}, schema={schema_name}, {details}); {hint}') from None
