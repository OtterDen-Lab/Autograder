import abc
import json
import os
import random
import ollama
from typing import Tuple, Dict, List

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
# These can be overridden via environment variables:
#   ANTHROPIC_MODEL_SMALL, ANTHROPIC_MODEL_MEDIUM, ANTHROPIC_MODEL_LARGE
#   OPENAI_MODEL_SMALL, OPENAI_MODEL_MEDIUM, OPENAI_MODEL_LARGE
#   OLLAMA_MODEL_SMALL, OLLAMA_MODEL_MEDIUM, OLLAMA_MODEL_LARGE
# =============================================================================
MODEL_CONFIG = {
  "anthropic": {
    "small": "claude-haiku-4-5",
    "medium": "claude-sonnet-4-5",
    "large": "claude-opus-4-5",
  },
  "openai": {
    "small": "gpt-4.1-nano",
    "medium": "gpt-4.1-mini",
    "large": "gpt-4.1",
  },
  "ollama": {
    "small": "qwen3:4b",
    "medium": "qwen3:14b",
    "large": "qwen3:32b",
  }
}

# Default tier to use when not specified
DEFAULT_MODEL_TIER = "small"


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

  return MODEL_CONFIG[provider][tier]


class AI_Helper(abc.ABC):
  _client = None

  def __init__(self) -> None:
    if self._client is None:
      log.debug("Loading dotenv")  # Load the .env file
      dotenv.load_dotenv(os.path.expanduser('~/.env'))

  @classmethod
  @abc.abstractmethod
  def query_ai(cls, message: str, attachments: List[Tuple[str, str]], *args,
               **kwargs) -> str:
    pass


class AI_Helper__Anthropic(AI_Helper):

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


class AI_Helper__OpenAI(AI_Helper):

  def __init__(self) -> None:
    super().__init__()
    self.__class__._client = OpenAI()

  @classmethod
  def query_ai(cls,
               message: str,
               attachments: List[Tuple[str, str]],
               max_response_tokens: int = DEFAULT_MAX_TOKENS,
               max_retries: int = DEFAULT_MAX_RETRIES,
               tier: str = None) -> Tuple[Dict, Dict]:
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
      content = json.loads(response.choices[0].message.content)
      return content, usage_info
    except TypeError:
      if max_retries > 0:
        return cls.query_ai(message, attachments, max_response_tokens,
                            max_retries - 1)
      else:
        return {}, usage_info


class AI_Helper__Ollama(AI_Helper):

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

    # Get model for the specified tier
    # Also check legacy OLLAMA_MODEL env var for backward compatibility
    legacy_model = os.getenv('OLLAMA_MODEL')
    if legacy_model and tier is None:
      model = legacy_model
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
