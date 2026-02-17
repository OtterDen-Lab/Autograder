from __future__ import annotations

from dataclasses import dataclass

from .canvas_interface import CanvasAssignment, CanvasCourse, CanvasInterface
from .interfaces import LMSAssignment, LMSBackend, LMSCourse


class CanvasBackend(LMSBackend):
  def __init__(
      self,
      *,
      prod: bool = False,
      env_path: str | None = None,
      canvas_url: str | None = None,
      canvas_key: str | None = None,
      privacy_mode: str | None = None,
      reveal_identity: bool = False,
      blind_id_map_path: str | None = None
  ):
    self._interface = CanvasInterface(
      prod=prod,
      env_path=env_path,
      canvas_url=canvas_url,
      canvas_key=canvas_key,
      privacy_mode=privacy_mode,
      reveal_identity=reveal_identity,
      blind_id_map_path=blind_id_map_path
    )

  def get_course(self, course_id: int) -> LMSCourse:
    return CanvasCourseAdapter(self._interface.get_course(course_id))


@dataclass
class CanvasCourseAdapter(LMSCourse):
  _course: CanvasCourse

  @property
  def id(self):
    return self._course.course.id

  @property
  def name(self):
    return getattr(self._course.course, "name", None)

  def get_assignment(self, assignment_id: int) -> LMSAssignment | None:
    assignment = self._course.get_assignment(assignment_id)
    if assignment is None:
      return None
    return CanvasAssignmentAdapter(assignment)

  def get_assignments(self, **kwargs) -> list[LMSAssignment]:
    return [CanvasAssignmentAdapter(a) for a in self._course.get_assignments(**kwargs)]

  def get_students(self):
    return self._course.get_students()


@dataclass
class CanvasAssignmentAdapter(LMSAssignment):
  _assignment: CanvasAssignment

  @property
  def id(self):
    return self._assignment.assignment.id

  @property
  def name(self):
    return getattr(self._assignment.assignment, "name", None)

  def get_submissions(self, **kwargs):
    return self._assignment.get_submissions(**kwargs)

  def push_feedback(
      self,
      user_id,
      score: float,
      comments: str,
      attachments=None,
      keep_previous_best: bool = True,
      clobber_feedback: bool = False
  ) -> bool | None:
    return self._assignment.push_feedback(
      user_id=user_id,
      score=score,
      comments=comments,
      attachments=attachments,
      keep_previous_best=keep_previous_best,
      clobber_feedback=clobber_feedback
    )
