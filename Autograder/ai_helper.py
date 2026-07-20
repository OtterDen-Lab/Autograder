import abc
import json
import os
import random
import ollama
from typing import Tuple, Dict, List, Any, Callable

import dotenv
import openai.types.chat.completion_create_params
from openai import OpenAI
from anthropic import Anthropic
import httpx

import logging

log = logging.getLogger(__name__)

# Constants
DEFAULT_MAX_TOKENS = 1000  # Default token limit for AI responses
DEFAULT_MAX_RETRIES = 3  # Default number of retries for failed requests

# =============================================================================
# MODEL CONFIGURATION
# =============================================================================
# Define available models per provider and tier (small, medium, large)
# Each model includes: name and pricing (input_cost, output_cost) per million tokens
# Model names can be overridden via environment variables:
#   ANTHROPIC_MODEL_SMALL, ANTHROPIC_MODEL_MEDIUM, ANTHROPIC_MODEL_LARGE
#   OPENAI_MODEL_SMALL, OPENAI_MODEL_MEDIUM, OPENAI_MODEL_LARGE
#   OLLAMA_MODEL_SMALL, OLLAMA_MODEL_MEDIUM, OLLAMA_MODEL_LARGE
# =============================================================================
MODEL_CONFIG = {
  "anthropic": {
    "small": {
      "name": "claude-haiku-4-5",
      "input_cost": 1.0,    # $ per million tokens
      "output_cost": 5.0,
    },
    "medium": {
      "name": "claude-sonnet-4-5",
      "input_cost": 3.0,
      "output_cost": 15.0,
    },
    "large": {
      "name": "claude-opus-4-5",
      "input_cost": 15.0,
      "output_cost": 75.0,
    },
  },
  "openai": {
    "small": {
      "name": "gpt-4.1-nano",
      "input_cost": 0.10,
      "output_cost": 0.40,
    },
    "medium": {
      "name": "gpt-4.1-mini",
      "input_cost": 0.40,
      "output_cost": 1.60,
    },
    "large": {
      "name": "gpt-4.1",
      "input_cost": 2.0,
      "output_cost": 8.0,
    },
  },
  "ollama": {
    "small": {
      "name": "qwen3:4b",
      "input_cost": 0.0,
      "output_cost": 0.0,
    },
    "medium": {
      "name": "qwen3:14b",
      "input_cost": 0.0,
      "output_cost": 0.0,
    },
    "large": {
      "name": "qwen3:32b",
      "input_cost": 0.0,
      "output_cost": 0.0,
    },
  },
}

# Default tier to use when not specified
DEFAULT_MODEL_TIER = "small"


class AIResponseValidationError(ValueError):
  """Raised when an LLM response does not match the expected schema."""
  pass


def _coerce_str(value: Any, default: str = "") -> str:
  if value is None:
    return default
  if isinstance(value, str):
    return value
  return str(value)


def _coerce_int(value: Any,
                *,
                default: int = 0,
                min_value: int | None = None,
                max_value: int | None = None) -> int:
  try:
    coerced = int(value)
  except (TypeError, ValueError):
    coerced = default
  if min_value is not None:
    coerced = max(min_value, coerced)
  if max_value is not None:
    coerced = min(max_value, coerced)
  return coerced


def _coerce_bool(value: Any, default: bool = False) -> bool:
  if isinstance(value, bool):
    return value
  if isinstance(value, str):
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
      return True
    if normalized in {"false", "0", "no"}:
      return False
  if isinstance(value, (int, float)):
    return bool(value)
  return default


def _coerce_str_list(value: Any) -> list[str]:
  if value is None:
    return []
  if not isinstance(value, list):
    raise AIResponseValidationError(
      f"Expected list[str], got {type(value).__name__}.")
  return [str(item).strip() for item in value if str(item).strip()]


def _default_aggregate_analysis() -> Dict[str, Any]:
  return {
    "common_themes": "",
    "commonly_misunderstood_topics": [],
    "misconception_details": "",
    "key_insights": "",
    "teaching_feedback": "",
    "core_topics": [],
    "related_topics": [],
    "off_topic_indicators": [],
    "student_questions": []
  }


def _default_individual_grading() -> Dict[str, Any]:
  return {
    "engagement_score": 0,
    "relevance_score": 0,
    "explanation_quality_score": 0,
    "topics_covered": [],
    "topics_missing": [],
    "topics_needing_review": [],
    "off_topic_content": "",
    "misconception_notes": "",
    "needs_support": False,
    "support_reason": "",
    "feedback": ""
  }


def _default_question_consolidation() -> Dict[str, Any]:
  return {"consolidated_questions": []}


def _validate_aggregate_analysis(payload: Dict[str, Any]) -> Dict[str, Any]:
  if not isinstance(payload, dict):
    raise AIResponseValidationError(
      f"Expected object for aggregate_analysis, got {type(payload).__name__}.")

  return {
    "common_themes": _coerce_str(payload.get("common_themes", "")),
    "commonly_misunderstood_topics": _coerce_str_list(
      payload.get("commonly_misunderstood_topics", [])),
    "misconception_details": _coerce_str(
      payload.get("misconception_details", "")),
    "key_insights": _coerce_str(payload.get("key_insights", "")),
    "teaching_feedback": _coerce_str(payload.get("teaching_feedback", "")),
    "core_topics": _coerce_str_list(payload.get("core_topics", [])),
    "related_topics": _coerce_str_list(payload.get("related_topics", [])),
    "off_topic_indicators": _coerce_str_list(
      payload.get("off_topic_indicators", [])),
    "student_questions": _coerce_str_list(payload.get("student_questions", [])),
  }


def _validate_individual_grading(payload: Dict[str, Any]) -> Dict[str, Any]:
  if not isinstance(payload, dict):
    raise AIResponseValidationError(
      f"Expected object for individual_grading, got {type(payload).__name__}.")

  return {
    "engagement_score":
    _coerce_int(payload.get("engagement_score", 0), min_value=0, max_value=4),
    "relevance_score":
    _coerce_int(payload.get("relevance_score", 0), min_value=0, max_value=2),
    "explanation_quality_score":
    _coerce_int(payload.get("explanation_quality_score", 0),
                min_value=0,
                max_value=2),
    "topics_covered": _coerce_str_list(payload.get("topics_covered", [])),
    "topics_missing": _coerce_str_list(payload.get("topics_missing", [])),
    "topics_needing_review":
    _coerce_str_list(payload.get("topics_needing_review", [])),
    "off_topic_content": _coerce_str(payload.get("off_topic_content", "")),
    "misconception_notes": _coerce_str(payload.get("misconception_notes", "")),
    "needs_support": _coerce_bool(payload.get("needs_support", False)),
    "support_reason": _coerce_str(payload.get("support_reason", "")),
    "feedback": _coerce_str(payload.get("feedback", "")),
  }


def _validate_question_consolidation(payload: Dict[str, Any]) -> Dict[str, Any]:
  if not isinstance(payload, dict):
    raise AIResponseValidationError(
      f"Expected object for question_consolidation, got {type(payload).__name__}."
    )

  groups_raw = payload.get("consolidated_questions", [])
  if not isinstance(groups_raw, list):
    raise AIResponseValidationError(
      "Expected consolidated_questions to be a list.")

  groups = []
  for group in groups_raw:
    if not isinstance(group, dict):
      raise AIResponseValidationError(
        f"Expected consolidated question entry object, got {type(group).__name__}."
      )
    groups.append({
      "canonical_question":
      _coerce_str(group.get("canonical_question", "")),
      "original_questions":
      _coerce_str_list(group.get("original_questions", [])),
      "topic":
      _coerce_str(group.get("topic", "General")),
    })

  return {"consolidated_questions": groups}


RESPONSE_SCHEMAS: dict[str, dict[str, Callable[..., Dict[str, Any]]]] = {
  "aggregate_analysis": {
    "defaults": _default_aggregate_analysis,
    "validator": _validate_aggregate_analysis,
  },
  "individual_grading": {
    "defaults": _default_individual_grading,
    "validator": _validate_individual_grading,
  },
  "question_consolidation": {
    "defaults": _default_question_consolidation,
    "validator": _validate_question_consolidation,
  },
}


def validate_response_payload(payload: Dict[str, Any],
                              *,
                              schema_name: str | None = None,
                              strict: bool = False) -> Dict[str, Any]:
  """
  Validate and normalize a JSON payload returned by an LLM.

  Args:
      payload: Parsed JSON object.
      schema_name: Optional schema key in RESPONSE_SCHEMAS.
      strict: If True, re-raise validation errors instead of returning defaults.

  Returns:
      Validated payload (normalized to expected types).
  """
  if schema_name is None:
    if isinstance(payload, dict):
      return payload
    raise AIResponseValidationError(
      f"Expected JSON object response, got {type(payload).__name__}.")

  schema = RESPONSE_SCHEMAS.get(schema_name)
  if schema is None:
    raise ValueError(f"Unknown AI response schema: {schema_name}")

  try:
    return schema["validator"](payload)
  except AIResponseValidationError as e:
    log.warning(
      f"LLM response schema validation failed for '{schema_name}': {e}")
    if strict:
      raise
    return schema["defaults"]()


def get_model_for_tier(provider: str, tier: str = None) -> str:
  """
  Get the model name for a given provider and tier.

  Args:
      provider: The AI provider ("anthropic", "openai", "ollama")
      tier: The model tier ("small", "medium", "large"). Defaults to DEFAULT_MODEL_TIER.

  Returns:
      The model name to use
  """
  if tier is None:
    tier = DEFAULT_MODEL_TIER

  # Normalize inputs
  provider = provider.lower()
  tier = tier.lower()

  # Check for environment variable override first
  env_var = f"{provider.upper()}_MODEL_{tier.upper()}"
  env_model = os.getenv(env_var)
  if env_model:
    log.debug(f"Using model from {env_var}: {env_model}")
    return env_model

  # Fall back to config
  if provider not in MODEL_CONFIG:
    log.warning(f"Unknown provider '{provider}', using default")
    return "unknown"

  if tier not in MODEL_CONFIG[provider]:
    log.warning(f"Unknown tier '{tier}' for {provider}, falling back to 'small'")
    tier = "small"

  return MODEL_CONFIG[provider][tier]["name"]


def get_model_pricing(provider: str, model: str) -> tuple:
  """
  Get the pricing for a given provider and model.

  Args:
      provider: The AI provider ("anthropic", "openai", "ollama")
      model: The model name to look up pricing for

  Returns:
      Tuple of (input_cost, output_cost) per million tokens
  """
  provider = provider.lower()
  model = model.lower()

  if provider not in MODEL_CONFIG:
    return (0.0, 0.0)

  # Search through tiers to find matching model
  for tier_config in MODEL_CONFIG[provider].values():
    if tier_config["name"].lower() in model or model in tier_config["name"].lower():
      return (tier_config["input_cost"], tier_config["output_cost"])

  # Default to small tier pricing if model not found
  default_tier = MODEL_CONFIG[provider].get("small", {})
  return (default_tier.get("input_cost", 0.0), default_tier.get("output_cost", 0.0))


class AIHelper(abc.ABC):
  _client = None

  def __init__(self) -> None:
    if self._client is None:
      log.debug("Loading credential env file")
      dotenv.load_dotenv(os.path.expanduser("~/.tokens/autograder.env"))

  @classmethod
  @abc.abstractmethod
  def query_ai(cls, message: str, attachments: List[Tuple[str, str]], *args,
               **kwargs) -> str:
    pass


class AnthropicAIHelper(AIHelper):

  def __init__(self) -> None:
    super().__init__()
    self.__class__._client = Anthropic()

  @classmethod
  def query_ai(cls,
               message: str,
               attachments: List[Tuple[str, str]],
               max_response_tokens: int = DEFAULT_MAX_TOKENS,
               max_retries: int = DEFAULT_MAX_RETRIES,
               tier: str = None) -> Tuple[str, Dict]:
    messages = []

    # Get model for the specified tier
    model = get_model_for_tier("anthropic", tier)

    attachment_messages = []
    for file_type, b64_file_contents in attachments:
      if file_type == "png":
        attachment_messages.append({
          "type": "image",
          "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": b64_file_contents
          }
        })

    messages.append({
      "role":
      "user",
      "content": [{
        "type": "text",
        "text": f"{message}"
      }, *attachment_messages]
    })

    response = cls._client.messages.create(model=model,
                                           max_tokens=max_response_tokens,
                                           messages=messages)
    log.debug(response.content)

    # Extract usage information
    usage_info = {
      "prompt_tokens":
      response.usage.input_tokens if response.usage else 0,
      "completion_tokens":
      response.usage.output_tokens if response.usage else 0,
      "total_tokens": (response.usage.input_tokens +
                       response.usage.output_tokens) if response.usage else 0,
      "provider":
      "anthropic",
      "model":
      getattr(response, 'model', 'unknown')
    }

    return response.content[0].text, usage_info


class OpenAIAIHelper(AIHelper):

  def __init__(self) -> None:
    super().__init__()
    self.__class__._client = OpenAI()

  @classmethod
  def query_ai(cls,
               message: str,
               attachments: List[Tuple[str, str]],
               max_response_tokens: int = DEFAULT_MAX_TOKENS,
               max_retries: int = DEFAULT_MAX_RETRIES,
               tier: str = None,
               schema_name: str | None = None,
               strict_validation: bool = False) -> Tuple[Dict, Dict]:
    messages = []

    # Get model for the specified tier
    model = get_model_for_tier("openai", tier)

    attachment_messages = []
    for file_type, b64_file_contents in attachments:
      if file_type == "png":
        attachment_messages.append({
          "type": "image_url",
          "image_url": {
            "url": f"data:image/png;base64,{b64_file_contents}"
          }
        })

    messages.append({
      "role":
      "user",
      "content": [{
        "type": "text",
        "text": f"{message}"
      }, *attachment_messages]
    })

    response = cls._client.chat.completions.create(
      model=model,
      response_format={"type": "json_object"},
      messages=messages,
      temperature=1,
      max_tokens=max_response_tokens,
      top_p=1,
      frequency_penalty=0,
      presence_penalty=0)
    log.debug(response.choices[0])

    # Extract usage information
    usage_info = {
      "prompt_tokens":
      response.usage.prompt_tokens if response.usage else 0,
      "completion_tokens":
      response.usage.completion_tokens if response.usage else 0,
      "total_tokens":
      response.usage.total_tokens if response.usage else 0,
      "provider":
      "openai",
      "model":
      getattr(response, 'model', 'unknown')
    }

    try:
      raw_content = response.choices[0].message.content
      content = json.loads(raw_content)
      validated = validate_response_payload(content,
                                            schema_name=schema_name,
                                            strict=strict_validation)
      return validated, usage_info
    except (TypeError, json.JSONDecodeError, AIResponseValidationError) as e:
      log.warning(f"OpenAI response parse/validation error: {e}")
      if max_retries > 0:
        return cls.query_ai(message,
                            attachments,
                            max_response_tokens=max_response_tokens,
                            max_retries=max_retries - 1,
                            tier=tier,
                            schema_name=schema_name,
                            strict_validation=strict_validation)

      if strict_validation:
        raise

      fallback = {}
      if schema_name is not None:
        fallback = validate_response_payload({},
                                             schema_name=schema_name,
                                             strict=False)
      return fallback, usage_info


class OllamaAIHelper(AIHelper):

  def __init__(self):
    super().__init__()
    # Initialize client if not already done
    if self.__class__._client is None:
      ollama_host = os.getenv('OLLAMA_HOST', 'http://workhorse:11434')
      ollama_timeout = int(os.getenv('OLLAMA_TIMEOUT', '30'))
      log.info(
        f"Initializing Ollama client with host: {ollama_host}, timeout: {ollama_timeout}s"
      )
      self.__class__._client = ollama.Client(host=ollama_host,
                                             timeout=ollama_timeout)

  @classmethod
  def query_ai(cls,
               message: str,
               attachments: List[Tuple[str, str]],
               max_response_tokens: int = DEFAULT_MAX_TOKENS,
               max_retries: int = DEFAULT_MAX_RETRIES,
               tier: str = None) -> Tuple[str, Dict]:

    # Ensure client is initialized
    if cls._client is None:
      ollama_host = os.getenv('OLLAMA_HOST', 'http://workhorse:11434')
      ollama_timeout = int(os.getenv('OLLAMA_TIMEOUT', '30'))
      log.info(
        f"Lazily initializing Ollama client with host: {ollama_host}, timeout: {ollama_timeout}s"
      )
      cls._client = ollama.Client(host=ollama_host, timeout=ollama_timeout)

    # Extract base64 images from attachments (format: [("png", base64_str), ...])
    images = [
      att[1] for att in attachments if att[0] in ("png", "jpg", "jpeg")
    ]

    # Build message for Ollama
    msg_content = {'role': 'user', 'content': message}

    # Add images if present
    if images:
      msg_content['images'] = images

    # Get model for the specified tier, with optional explicit override.
    override_model = os.getenv('OLLAMA_MODEL')
    if override_model and tier is None:
      model = override_model
    else:
      model = get_model_for_tier("ollama", tier)

    log.info(
      f"Ollama: Using model {model} with host {cls._client._client.base_url}")
    log.debug(f"Ollama: Message content has {len(images)} images")

    try:
      # Use streaming mode - timeout resets on each chunk received
      # This differentiates between "actively processing" vs "broken connection"
      # Add options to reduce overthinking/hallucination
      options = {
        'temperature': 0.1,  # Lower temperature = more focused, less creative
        'top_p': 0.9,  # Nucleus sampling
        'num_predict': 500,  # Limit output length to prevent rambling
      }

      stream = cls._client.chat(model=model,
                                messages=[msg_content],
                                stream=True,
                                options=options)

      # Collect the streamed response
      content = ""
      last_response = None
      chunk_count = 0

      for chunk in stream:
        chunk_count += 1
        if chunk_count % 1000 == 0:
          log.debug(
            f"Ollama: Received chunk {chunk_count}, content length: {len(content)}"
          )

        content += chunk['message']['content']
        last_response = chunk  # Keep last chunk for metadata

      log.info(
        f"Ollama: Received {chunk_count} chunks, total {len(content)} characters"
      )

      # Extract usage information from final chunk
      prompt_tokens = last_response.get(
        'prompt_eval_count') or 0 if last_response else 0
      completion_tokens = last_response.get(
        'eval_count') or 0 if last_response else 0
      usage_info = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "provider": "ollama",
        "model": model
      }

      return content, usage_info

    except httpx.ReadTimeout:
      timeout = os.getenv('OLLAMA_TIMEOUT', '30')
      log.error(
        f"Ollama request timed out after {timeout}s (no data received)")
      raise
    except Exception as e:
      log.error(f"Ollama error ({type(e).__name__}): {str(e)}")
      raise


# =============================================================================
# Backwards Compatibility Aliases
# =============================================================================
# These aliases preserve backwards compatibility for code that imports the
# old class names with underscores.
AI_Helper = AIHelper
AI_Helper__Anthropic = AnthropicAIHelper
AI_Helper__OpenAI = OpenAIAIHelper
AI_Helper__Ollama = OllamaAIHelper
