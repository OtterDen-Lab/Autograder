from types import SimpleNamespace
from unittest.mock import patch

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from Autograder import grade_assignments
from Autograder.config_models import parse_run_config


_BOOL_KEYS = [
  "grade_after_lock_date",
  "prefer_anthropic",
  "record_retention",
  "report_errors",
]
_TIER_KEYS = ["phase1_tier", "phase2_tier", "phase25_tier"]
_ALL_KEYS = _BOOL_KEYS + _TIER_KEYS

@st.composite
def _settings_strategy(draw):
  merged = {}
  for key in _ALL_KEYS:
    include = draw(st.booleans())
    if not include:
      continue
    if key in _BOOL_KEYS:
      merged[key] = draw(st.booleans())
    else:
      merged[key] = draw(st.sampled_from(["small", "medium", "large"]))
  return merged


_SETTINGS_STRATEGY = _settings_strategy()


class _DummyCourse:
  name = "CST334"

  def get_assignment(self, _assignment_id):
    return SimpleNamespace(name="Weekly Notes")


class _DummyCanvasInterface:
  def __init__(self, *args, **kwargs):
    pass

  def get_course(self, _course_id):
    return _DummyCourse()


def _collect_merged_settings(type_settings, course_settings,
                             group_settings, assignment_settings):
  run_config = parse_run_config({
    "assignment_types": {
      "text": {
        "kind": "TextAssignment",
        "grader": "TextSubmissionGrader",
        "settings": type_settings,
      }
    },
    "courses": [{
      "id": 10,
      "name": "CST334",
      **course_settings,
      "assignment_groups": [{
        "type": "text",
        "settings": group_settings,
        "assignments": [{
          "id": 42,
          "settings": assignment_settings,
        }]
      }]
    }]
  })

  args = SimpleNamespace(env=None,
                         reveal_identity=False,
                         idempotency_key=None,
                         idempotency_state_dir=None)
  with patch.object(grade_assignments, "CanvasInterface",
                    _DummyCanvasInterface), patch.object(
                      grade_assignments.log, "info", lambda *a, **k: None):
    requests = grade_assignments.collect_assignments_to_grade(run_config, args)
  assert len(requests) == 1
  return requests[0].settings


@hypothesis_settings(max_examples=100, deadline=None)
@given(
  type_settings=_SETTINGS_STRATEGY,
  course_settings=_SETTINGS_STRATEGY,
  group_settings=_SETTINGS_STRATEGY,
  assignment_settings=_SETTINGS_STRATEGY,
)
def test_config_merge_is_deterministic(type_settings,
                                       course_settings, group_settings,
                                       assignment_settings):
  merged_a = _collect_merged_settings(type_settings, course_settings,
                                      group_settings,
                                      assignment_settings)
  merged_b = _collect_merged_settings(type_settings, course_settings,
                                      group_settings,
                                      assignment_settings)
  assert merged_a == merged_b


@hypothesis_settings(max_examples=100, deadline=None)
@given(
  type_settings=_SETTINGS_STRATEGY,
  course_settings=_SETTINGS_STRATEGY,
  group_settings=_SETTINGS_STRATEGY,
  assignment_settings=_SETTINGS_STRATEGY,
)
def test_config_merge_precedence_and_no_silent_drop(type_settings,
                                                    course_settings,
                                                    group_settings,
                                                    assignment_settings):
  merged = _collect_merged_settings(type_settings, course_settings,
                                    group_settings,
                                    assignment_settings)

  expected = {}
  expected.update(type_settings)
  expected.update(course_settings)
  expected.update(group_settings)
  expected.update(assignment_settings)

  for key, value in expected.items():
    assert key in merged
    assert merged[key] == value
