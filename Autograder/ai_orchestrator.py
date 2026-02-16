import json
import logging
from typing import Any, Callable, Dict, TypeVar

from Autograder import ai_helper
from Autograder import exceptions as autograder_exceptions

log = logging.getLogger(__name__)

T = TypeVar("T")


def extract_json_object(text: str) -> Dict[str, Any] | None:
  """Extract the first JSON object from model text output."""
  if not text:
    return None

  decoder = json.JSONDecoder()
  for start_index, char in enumerate(text):
    if char != "{":
      continue
    try:
      payload, _ = decoder.raw_decode(text[start_index:])
    except (TypeError, json.JSONDecodeError):
      continue
    if isinstance(payload, dict):
      return payload
  return None


def parse_anthropic_json_payload(text: str,
                                 *,
                                 schema_name: str,
                                 strict: bool = False) -> Dict[str, Any] | None:
  """
  Parse Anthropic text output that should contain JSON and validate against schema.
  """
  payload = extract_json_object(text)
  if payload is None:
    return None

  try:
    return ai_helper.validate_response_payload(payload,
                                               schema_name=schema_name,
                                               strict=strict)
  except Exception:
    return None


def query_openai_structured(prompt: str,
                            *,
                            schema_name: str,
                            tier: str,
                            max_response_tokens: int) -> tuple[Dict[str, Any], Dict[str, Any]]:
  try:
    helper = ai_helper.AI_Helper__OpenAI()
    return helper.query_ai(prompt,
                           [],
                           max_response_tokens=max_response_tokens,
                           tier=tier,
                           schema_name=schema_name)
  except Exception as e:
    raise autograder_exceptions.AIProviderError(
      f"OpenAI request failed (tier={tier}, schema={schema_name}). "
      "Check API credentials, connectivity, and provider availability.") from e


def query_anthropic_text(prompt: str,
                         *,
                         tier: str,
                         max_response_tokens: int) -> tuple[str, Dict[str, Any]]:
  try:
    helper = ai_helper.AI_Helper__Anthropic()
    return helper.query_ai(prompt,
                           [],
                           max_response_tokens=max_response_tokens,
                           tier=tier)
  except Exception as e:
    raise autograder_exceptions.AIProviderError(
      f"Anthropic request failed (tier={tier}). "
      "Check API credentials, connectivity, and provider availability.") from e


class ProviderFallbackOrchestrator:
  """
  Execute provider calls with a consistent primary/fallback strategy.
  """

  def __init__(self, prefer_anthropic: bool):
    self.prefer_anthropic = prefer_anthropic

  def run(self,
          *,
          run_openai: Callable[[], T],
          run_anthropic: Callable[[], T],
          on_openai_error: Callable[[Exception, bool], None] | None = None,
          on_anthropic_error: Callable[[Exception, bool], None] | None = None,
          on_both_fail: Callable[[Exception, Exception], T] | None = None) -> T:
    """
    Run primary provider first, then fallback provider on failure.

    Error callbacks receive `(error, is_fallback_attempt)`.
    """
    if self.prefer_anthropic:
      primary_runner = run_anthropic
      secondary_runner = run_openai
      primary_error_cb = on_anthropic_error
      secondary_error_cb = on_openai_error
    else:
      primary_runner = run_openai
      secondary_runner = run_anthropic
      primary_error_cb = on_openai_error
      secondary_error_cb = on_anthropic_error

    try:
      return primary_runner()
    except Exception as primary_error:
      if primary_error_cb is not None:
        primary_error_cb(primary_error, False)

      try:
        return secondary_runner()
      except Exception as secondary_error:
        if secondary_error_cb is not None:
          secondary_error_cb(secondary_error, True)

        if on_both_fail is not None:
          return on_both_fail(primary_error, secondary_error)

        raise
