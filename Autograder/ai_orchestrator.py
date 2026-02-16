import json
import logging
import time
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
                            max_response_tokens: int,
                            max_attempts: int = 3) -> tuple[Dict[str, Any], Dict[str, Any]]:
  try:
    helper = ai_helper.AI_Helper__OpenAI()
    retries = max(0, int(max_attempts) - 1)
    return helper.query_ai(prompt,
                           [],
                           max_response_tokens=max_response_tokens,
                           max_retries=retries,
                           tier=tier,
                           schema_name=schema_name,
                           strict_validation=True)
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


def query_anthropic_structured(
    prompt: str,
    *,
    schema_name: str,
    tier: str,
    max_response_tokens: int,
    max_retries: int = 3) -> tuple[Dict[str, Any], Dict[str, Any]]:
  """
  Query Anthropic and parse JSON response with retry logic.

  Similar to query_openai_structured but handles Anthropic's text output
  and parses JSON from it, retrying on parse failures.

  Args:
      prompt: The prompt to send
      schema_name: Schema name for validation (from ai_helper.RESPONSE_SCHEMAS)
      tier: Model tier (small, medium, large)
      max_response_tokens: Maximum tokens in response
      max_retries: Number of retries on JSON parse failure

  Returns:
      Tuple of (validated_payload, usage_info)

  Raises:
      AIProviderError: On API failures or exhausted retries
  """
  last_error = None
  combined_usage = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "provider": "anthropic",
    "model": "unknown",
    "retries": 0
  }

  for attempt in range(1, max_retries + 1):
    try:
      text, usage_info = query_anthropic_text(prompt,
                                              tier=tier,
                                              max_response_tokens=max_response_tokens)
      # Accumulate usage across retries
      combined_usage["prompt_tokens"] += usage_info.get("prompt_tokens", 0)
      combined_usage["completion_tokens"] += usage_info.get("completion_tokens", 0)
      combined_usage["total_tokens"] += usage_info.get("total_tokens", 0)
      combined_usage["model"] = usage_info.get("model", "unknown")

      payload = parse_anthropic_json_payload(text, schema_name=schema_name)
      if payload is not None:
        return payload, combined_usage

      # JSON parsing failed - will retry if attempts remain
      last_error = ValueError(
        f"Failed to parse JSON from Anthropic response (attempt {attempt}/{max_retries})"
      )
      combined_usage["retries"] = attempt
      log.warning(
        f"Anthropic JSON parse failed (attempt {attempt}/{max_retries}), "
        f"schema={schema_name}"
      )
      if attempt < max_retries:
        time.sleep(min(0.5 * (2 ** (attempt - 1)), 2.0))

    except autograder_exceptions.AIProviderError:
      raise
    except Exception as e:
      last_error = e
      log.warning(f"Anthropic query error (attempt {attempt}/{max_retries}): {e}")
      if attempt < max_retries:
        time.sleep(min(0.5 * (2 ** (attempt - 1)), 2.0))

  # Exhausted retries
  raise autograder_exceptions.AIProviderError(
    f"Anthropic request failed after {max_retries} attempts (tier={tier}, schema={schema_name}). "
    f"Last error: {last_error}"
  ) from last_error


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
