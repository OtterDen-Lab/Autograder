"""Unified structured AI requests through the Bifrost gateway."""
import json
import os

import dotenv
from openai import OpenAI

from Autograder.ai_helper import validate_response_payload
from Autograder.exceptions import AIProviderError


def query_ai(prompt, *, schema_name, tier, max_response_tokens):
  """Return a validated response and token usage; Bifrost owns model fallback."""
  dotenv.load_dotenv(os.path.expanduser("~/.tokens/autograder.env"))
  key = os.getenv("BIFROST_API_KEY")
  model = os.getenv(f"BIFROST_MODEL_{tier.upper()}")
  if not key or not model:
    raise AIProviderError(
      f"BIFROST_API_KEY and BIFROST_MODEL_{tier.upper()} must be configured")
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
    result = validate_response_payload(
      json.loads(content),
      schema_name=schema_name, strict=True)
    usage = response.usage
    return result, {
      "provider": "bifrost", "model": response.model,
      "prompt_tokens": usage.prompt_tokens if usage else 0,
      "completion_tokens": usage.completion_tokens if usage else 0,
      "total_tokens": usage.total_tokens if usage else 0,
    }
  except AIProviderError:
    raise
  except Exception as e:
    raise AIProviderError(
      f"Bifrost request failed (tier={tier}, schema={schema_name}); "
      "check gateway availability, model routing, and credentials") from e
