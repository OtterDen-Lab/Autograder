#!/usr/bin/env python
from __future__ import annotations

import dataclasses
import enum
import functools
import io
import logging
import os
import typing
import urllib.parse
import urllib.request

import canvasapi.canvas

log = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_BYTES = 64 * 1024
ALLOWED_CONTENT_TYPES_BY_EXTENSION = {
  ".c": {"text/plain", "text/x-c", "application/octet-stream"},
  ".h": {"text/plain", "text/x-c", "application/octet-stream"},
  ".cpp": {"text/plain", "text/x-c++src", "application/octet-stream"},
  ".hpp": {"text/plain", "text/x-c++hdr", "application/octet-stream"},
  ".cc": {"text/plain", "text/x-c++src", "application/octet-stream"},
  ".py": {"text/plain", "text/x-python", "application/octet-stream"},
  ".java": {"text/plain", "text/x-java-source", "application/octet-stream"},
  ".js": {"text/plain", "application/javascript", "text/javascript", "application/octet-stream"},
  ".ts": {"text/plain", "application/typescript", "application/octet-stream"},
  ".go": {"text/plain", "text/x-go", "application/octet-stream"},
  ".rs": {"text/plain", "text/rust", "application/octet-stream"},
  ".rb": {"text/plain", "application/x-ruby", "application/octet-stream"},
  ".sh": {"text/plain", "application/x-sh", "application/octet-stream"},
  ".txt": {"text/plain", "application/octet-stream"},
  ".md": {"text/plain", "text/markdown", "application/octet-stream"},
  ".csv": {"text/csv", "text/plain", "application/octet-stream"},
  ".json": {"application/json", "text/plain", "application/octet-stream"},
  ".yaml": {"application/x-yaml", "text/yaml", "text/plain", "application/octet-stream"},
  ".yml": {"application/x-yaml", "text/yaml", "text/plain", "application/octet-stream"},
  ".zip": {"application/zip", "application/octet-stream"},
  ".tar": {"application/x-tar", "application/octet-stream"},
  ".gz": {"application/gzip", "application/x-gzip", "application/octet-stream"},
}



class LMSWrapper():
  def __init__(self, _inner):
    self._inner = _inner
  
  def __getattr__(self, name):
    try:
      # Try to get the attribute from the inner instance
      return getattr(self._inner, name)
    except AttributeError:
      # Surface missing attributes instead of silently swallowing errors
      message = f"'{name}' not found in either wrapper or inner class"
      log.error(message)
      raise AttributeError(message)


@dataclasses.dataclass
class Student(LMSWrapper):
  name : str
  user_id : int
  _inner : canvasapi.canvas.User
  

class Submission:

  class Status(enum.Enum):
    MISSING = "unsubmitted"
    UNGRADED = ("submitted", "pending_review")
    GRADED = "graded"

    @classmethod
    def from_string(cls, status_string, current_score):
      for status in cls:
        if status is not cls.MISSING and current_score is None:
          return cls.UNGRADED
        if isinstance(status.value, tuple):
          if status_string in status.value:
            return status
        elif status_string == status.value:
          return status
      return cls.MISSING  # Default


  def __init__(
      self,
      *,
      student : Student | None = None,
      status : Submission.Status = Status.UNGRADED,
      **kwargs
  ):
    self._student: Student | None = student
    self.status = status
    self.input_files: list[io.BytesIO] | None = None
    self.feedback : Feedback | None = None
    self.extra_info: dict[str, typing.Any] = {}

  @property
  def student(self):
    return self._student

  @student.setter
  def student(self, student):
    self._student = student

  def __str__(self):
    try:
      return f"Submission({self.student.name} : {self.feedback})"
    except AttributeError:
      return f"Submission({self.student} : {self.feedback})"

  def set_extra(self, extras_dict: dict):
    self.extra_info.update(extras_dict)


class FileSubmission(Submission):
  """Base class for submissions that contain files (e.g., programming assignments)"""
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._files: list[io.BytesIO] | None = None

  @property
  def files(self):
    return self._files

  @files.setter
  def files(self, files):
    self._files = files


class FileSubmission__Canvas(FileSubmission):
  """Canvas-specific file submission with attachment downloading"""
  def __init__(self, *args, attachments : list | None, **kwargs):
    super().__init__(*args, **kwargs)
    self._attachments = attachments
    self.submission_index = kwargs.get("submission_index", None)

  @property
  def files(self):
    # Check if we have already downloaded the files locally and return if we have
    if self._files is not None:
      return self._files

    # If we haven't downloaded the files yet, check if we have attachments and can download them
    if self._attachments is not None:
      self._files = []
      for attachment in self._attachments:

        # Generate a local file name with a number of options
        # local_file_name = f"{self.student.name.replace(' ', '-')}_{self.student.user_id}_{attachment['filename']}"
        local_file_name = self._sanitize_filename(attachment.get('filename', 'submission_file'))
        download_url = attachment.get('url')
        if not isinstance(download_url, str) or not download_url:
          raise ValueError(
            f"Attachment missing valid URL for file '{local_file_name}'.")

        self._validate_url(download_url, local_file_name)
        attachment_content_type = self._extract_content_type(
          attachment.get('content-type') or attachment.get('content_type'))

        with urllib.request.urlopen(download_url,
                                    timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
          response_content_type = self._response_content_type(response)
          self._validate_content_type(local_file_name, attachment_content_type,
                                      response_content_type)

          buffer = io.BytesIO()
          total_bytes = 0
          while True:
            chunk = response.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
              break
            total_bytes += len(chunk)
            if total_bytes > MAX_DOWNLOAD_BYTES:
              raise ValueError(
                f"Attachment '{local_file_name}' exceeds max size of {MAX_DOWNLOAD_BYTES} bytes."
              )
            buffer.write(chunk)
          buffer.seek(0)
          buffer.name = local_file_name
          self._files.append(buffer)

    return self._files

  @staticmethod
  def _sanitize_filename(filename: str) -> str:
    basename = os.path.basename(str(filename or "submission_file"))
    basename = basename.replace('\x00', '').strip()
    safe = ''.join(
      c if (c.isalnum() or c in {'-', '_', '.', ' '}) else '_'
      for c in basename)
    safe = safe.strip().strip('.')
    if safe in {"", ".", ".."}:
      return "submission_file"
    return safe

  @staticmethod
  def _extract_content_type(value) -> str | None:
    if not value:
      return None
    return str(value).split(';', 1)[0].strip().lower() or None

  @staticmethod
  def _response_content_type(response) -> str | None:
    try:
      info = response.info()
      if info is None:
        return None
      if hasattr(info, "get_content_type"):
        return FileSubmission__Canvas._extract_content_type(
          info.get_content_type())
      return FileSubmission__Canvas._extract_content_type(
        info.get("Content-Type"))
    except Exception:
      return None

  @staticmethod
  def _validate_url(url: str, filename: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
      raise ValueError(
        f"Attachment '{filename}' has unsupported URL scheme '{parsed.scheme}'."
      )

  @staticmethod
  def _validate_content_type(filename: str,
                             attachment_content_type: str | None,
                             response_content_type: str | None) -> None:
    ext = os.path.splitext(filename)[1].lower()
    allowed = ALLOWED_CONTENT_TYPES_BY_EXTENSION.get(ext)
    if not allowed:
      return

    observed = response_content_type or attachment_content_type
    if observed is None:
      return

    if observed in allowed:
      return

    if observed == "application/octet-stream":
      return

    raise ValueError(
      f"Attachment '{filename}' has content-type '{observed}', expected one of: {sorted(allowed)}"
    )


class TextSubmission(Submission):
  """Submission containing text content (e.g., journal entries, essays)"""
  def __init__(self, *args, submission_text=None, **kwargs):
    super().__init__(*args, **kwargs)
    self.submission_text = submission_text or ""

  def get_text(self):
    """Get the submission text content"""
    return self.submission_text

  def get_word_count(self):
    """Get word count of the submission"""
    return len(self.submission_text.split()) if self.submission_text else 0

  def get_character_count(self, include_spaces=True):
    """Get character count of the submission"""
    if not self.submission_text:
      return 0
    return len(self.submission_text) if include_spaces else len(self.submission_text.replace(' ', ''))

  def get_paragraph_count(self):
    """Get paragraph count (separated by double newlines)"""
    if not self.submission_text:
      return 0
    paragraphs = [p.strip() for p in self.submission_text.split('\n\n') if p.strip()]
    return len(paragraphs)

  def __str__(self):
    try:
      word_count = self.get_word_count()
      return f"TextSubmission({self.student.name} : {word_count} words : {self.feedback})"
    except AttributeError:
      return f"TextSubmission({self.student} : {self.get_word_count()} words : {self.feedback})"


class TextSubmission__Canvas(TextSubmission):
  """Canvas-specific text submission"""
  def __init__(self, *args, canvas_submission_data=None, **kwargs):
    submission_text = ""
    if canvas_submission_data and hasattr(canvas_submission_data, 'body') and canvas_submission_data.body:
      submission_text = canvas_submission_data.body

    super().__init__(*args, submission_text=submission_text, **kwargs)
    self.canvas_submission_data = canvas_submission_data
    self.submission_index = kwargs.get("submission_index", None)


class QuizSubmission(Submission):
  """Submission containing quiz responses and question metadata"""
  def __init__(self, *args, quiz_submission_data=None, student_responses=None, quiz_questions=None, **kwargs):
    super().__init__(*args, **kwargs)
    self.quiz_submission_data = quiz_submission_data
    self.responses = student_responses or {}  # Dict mapping question_id -> response
    self.questions = quiz_questions or {}     # Dict mapping question_id -> question metadata

  def get_response(self, question_id: int):
    """Get student's response to a specific question"""
    return self.responses.get(question_id)

  def get_question(self, question_id: int):
    """Get question metadata for a specific question"""
    return self.questions.get(question_id)

  def __str__(self):
    try:
      response_count = len(self.responses)
      return f"QuizSubmission({self.student.name} : {response_count} responses : {self.feedback})"
    except AttributeError:
      return f"QuizSubmission({self.student} : {len(self.responses)} responses : {self.feedback})"


# Maintain backward compatibility
Submission__Canvas = FileSubmission__Canvas


@functools.total_ordering
@dataclasses.dataclass
class Feedback:
  percentage_score: float | None = None
  comments: str = ""
  attachments: list[io.BytesIO] = dataclasses.field(default_factory=list)
  
  def __str__(self):
    short_comment = self.comments[:10].replace('\n', '\\n')
    ellipsis = '...' if len(self.comments) > 10 else ''
    score = "None" if self.percentage_score is None else f"{self.percentage_score:.4g}%"
    return f"Feedback({score}, {short_comment}{ellipsis})"

  def __eq__(self, other):
    if not isinstance(other, Feedback):
      return NotImplemented
    return self.percentage_score == other.percentage_score
  
  def __lt__(self, other):
    if not isinstance(other, Feedback):
      return NotImplemented
    if self.percentage_score is None:
      return False  # None is treated as greater than any other value
    if other.percentage_score is None:
      return True
    return self.percentage_score < other.percentage_score
