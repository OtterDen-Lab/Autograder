from __future__ import annotations

from typing import Protocol

from .classes import Submission


class LMSUser(Protocol):
  name: str
  user_id: str | int


class LMSAssignment(Protocol):
  id: str | int
  name: str | None

  def get_submissions(self, **kwargs) -> list[Submission]: ...
  def push_feedback(
      self,
      user_id,
      score: float,
      comments: str,
      attachments=None,
      keep_previous_best: bool = True,
      clobber_feedback: bool = False
  ) -> bool | None: ...


class LMSCourse(Protocol):
  id: str | int
  name: str | None

  def get_assignment(self, assignment_id: int) -> LMSAssignment | None: ...
  def get_assignments(self, **kwargs) -> list[LMSAssignment]: ...
  def get_students(self) -> list[LMSUser]: ...


class LMSBackend(Protocol):
  def get_course(self, course_id: int) -> LMSCourse: ...
