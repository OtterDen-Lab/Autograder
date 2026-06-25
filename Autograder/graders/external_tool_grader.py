from __future__ import annotations

from typing import Any

from Autograder import config_models
from Autograder.grader import Grader
from Autograder.registry import GraderRegistry
from lms_interface.classes import Feedback, Submission


@GraderRegistry.register("panopto-watch-grader")
class PanoptoWatchGrader(Grader):
  COMPATIBLE_KINDS = {"ExternalToolAssignment"}

  @classmethod
  def normalize_settings(cls, settings: dict, context_label: str) -> dict:
    return config_models._normalize_external_tool_grader_settings(
      settings, context_label)

  def can_grade_submission(self, submission: Submission) -> bool:
    return True

  def execute_grading(self, *args, **kwargs) -> Any:
    submission = kwargs.get("submission")
    if submission is None:
      raise ValueError("submission is required")
    return dict(getattr(submission, "extra_info", {}))

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    percent_watched = execution_results.get("percent_watched")
    if percent_watched is None:
      percent_watched = execution_results.get("missing_user_score", 0.0)
    percent_watched = max(0.0, min(100.0, float(percent_watched)))

    watch_record_found = bool(execution_results.get("watch_record_found"))
    viewed_seconds = execution_results.get("viewed_seconds")
    duration_seconds = execution_results.get("duration_seconds")

    if watch_record_found:
      comment = f"Panopto watch progress: {percent_watched:.1f}%"
      if viewed_seconds is not None and duration_seconds:
        comment += (
          f" ({viewed_seconds / 60.0:.1f} / {duration_seconds / 60.0:.1f} minutes viewed)")
    else:
      identifier = execution_results.get("canvas_identifier")
      if identifier:
        comment = (
          "No Panopto watch record was found for the mapped student identity "
          f"'{identifier}'. Score defaulted to {percent_watched:.1f}%.")
      else:
        comment = (
          "No Panopto watch record was found and no Canvas student identifier could "
          f"be mapped. Score defaulted to {percent_watched:.1f}%.")

    return Feedback(percentage_score=percent_watched, comments=comment)
