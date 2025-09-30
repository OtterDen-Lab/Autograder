import abc
import json
import os
import random
from typing import Tuple, Dict, List

import dotenv
import openai.types.chat.completion_create_params
from openai import OpenAI
from anthropic import Anthropic

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
  def query_ai(cls, message: str, attachments: List[Tuple[str, str]], 
               *args, **kwargs) -> str:
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
            "type" : "base64",
            "media_type" : "image/png",
            "data" : b64_file_contents
          }
        })
    
    messages.append(
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text":
              f"{message}"
          },
          *attachment_messages
        ]
      }
    )
    
    response = cls._client.messages.create(
      model="claude-3-7-sonnet-latest",
      max_tokens=DEFAULT_MAX_TOKENS,
      messages=messages
    )
    log.debug(response.content)

    # Extract usage information
    usage_info = {
      "prompt_tokens": response.usage.input_tokens if response.usage else 0,
      "completion_tokens": response.usage.output_tokens if response.usage else 0,
      "total_tokens": (response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0,
      "provider": "anthropic"
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
        
    messages.append(
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text":
              f"{message}"
          },
          *attachment_messages
        ]
      }
    )
    
    response = cls._client.chat.completions.create(
      model="gpt-4o",
      response_format={"type": "json_object"},
      messages=messages,
      temperature=1,
      max_tokens=max_response_tokens,
      top_p=1,
      frequency_penalty=0,
      presence_penalty=0
    )
    log.debug(response.choices[0])

    # Extract usage information
    usage_info = {
      "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
      "completion_tokens": response.usage.completion_tokens if response.usage else 0,
      "total_tokens": response.usage.total_tokens if response.usage else 0,
      "provider": "openai"
    }

    try:
      content = json.loads(response.choices[0].message.content)
      return content, usage_info
    except TypeError:
      if max_retries > 0:
        return cls.query_ai(message, attachments, max_response_tokens, max_retries-1)
      else:
        return {}, usage_info
