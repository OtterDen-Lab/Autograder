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
               max_retries: int = DEFAULT_MAX_RETRIES) -> Tuple[str, Dict]:
    messages = []

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

    response = cls._client.messages.create(model="claude-3-7-sonnet-latest",
                                           max_tokens=DEFAULT_MAX_TOKENS,
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
      "anthropic"
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
               max_retries: int = DEFAULT_MAX_RETRIES) -> Tuple[Dict, Dict]:
    messages = []

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
      model="gpt-4.1-nano",
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
      "openai"
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
               max_retries: int = DEFAULT_MAX_RETRIES) -> Tuple[str, Dict]:

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

    # Use the client instance to make the request
    # Model can be configured via environment variable or default to qwen3-vl:2b
    model = os.getenv('OLLAMA_MODEL', 'qwen3-vl:2b')

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
        "provider": "ollama"
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
