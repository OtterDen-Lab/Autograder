#!env python
from __future__ import annotations

import enum
import logging
import dataclasses
import functools
import io
import os
import urllib.request
from typing import Optional, List, Dict

import canvasapi.canvas

log = logging.getLogger(__name__)



class LMSWrapper():
  def __init__(self, _inner):
    self._inner = _inner
  
  def __getattr__(self, name):
    try:
      # Try to get the attribute from the inner instance
      return getattr(self._inner, name)
    except AttributeError:
      # Handle the case where the inner instance also doesn't have the attribute
      print(f"Warning: '{name}' not found in either wrapper or inner class")
      # You can raise the error again, return None, or handle it however you want
      return lambda *args, **kwargs: None  # Returns a no-op function for method calls


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
      student : Student = None,
      status : Submission.Status = Status.UNGRADED,
      **kwargs
  ):
    self._student: Optional[Student] = student
    self.status = status
    self.input_files = None
    self.feedback : Optional[Feedback] = None
    self.extra_info = {}

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

  def set_extra(self, extras_dict: Dict):
    self.extra_info.update(extras_dict)


class FileSubmission(Submission):
  """Base class for submissions that contain files (e.g., programming assignments)"""
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._files = None

  @property
  def files(self):
    return self._files

  @files.setter
  def files(self, files):
    self._files = files


class FileSubmission__Canvas(FileSubmission):
  """Canvas-specific file submission with attachment downloading"""
  def __init__(self, *args, attachments : Optional[List], **kwargs):
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
        local_file_name = f"{attachment['filename']}"
        with urllib.request.urlopen(attachment['url']) as response:
          buffer = io.BytesIO(response.read())
          buffer.name = local_file_name
          self._files.append(buffer)

    return self._files


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
  percentage_score: Optional[float] = None
  comments: str = ""
  attachments: List[io.BytesIO] = dataclasses.field(default_factory=list)
  
  def __str__(self):
    short_comment = self.comments[:10].replace('\n', '\\n')
    ellipsis = '...' if len(self.comments) > 10 else ''
    return f"Feedback({self.percentage_score:.4g}%, {short_comment}{ellipsis})"

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
