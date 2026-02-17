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
_INT_KEYS = ["rate_limit_retries"]
_OPTIONAL_STR_KEYS = [
  "records_dir",
  "slack_webhook",
  "slack_token",
  "slack_channel",
]
_PROMPT_KEYS = [
  "aggregate_analysis",
  "individual_grading",
  "question_consolidation",
]
_NESTED_KEYS = ["prompts", "rubric"]
_SIMPLE_KEYS = _BOOL_KEYS + _TIER_KEYS + _INT_KEYS + _OPTIONAL_STR_KEYS
_ALL_KEYS = _SIMPLE_KEYS + _NESTED_KEYS
_COURSE_KEYS = [key for key in _SIMPLE_KEYS if key != "slack_channel"]

_OPTIONAL_STR_STRATEGY = st.one_of(
  st.none(),
  st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"),
                           whitelist_characters="-_/:.#"),
    min_size=1,
    max_size=32))
_PROMPTS_STRATEGY = st.dictionaries(
  keys=st.sampled_from(_PROMPT_KEYS),
  values=st.text(min_size=1, max_size=48),
  max_size=len(_PROMPT_KEYS))
_RUBRIC_CRITERION_STRATEGY = st.fixed_dictionaries(
  {},
  optional={
    "points": st.integers(min_value=0, max_value=8),
    "description": st.one_of(st.none(), st.text(min_size=0, max_size=48)),
  })
_RUBRIC_STRATEGY = st.fixed_dictionaries(
  {},
  optional={
    "engagement": _RUBRIC_CRITERION_STRATEGY,
    "length": _RUBRIC_CRITERION_STRATEGY,
    "relevance": _RUBRIC_CRITERION_STRATEGY,
    "explanation_quality": _RUBRIC_CRITERION_STRATEGY,
    "word_threshold": st.integers(min_value=1, max_value=1200),
  })


def _value_strategy_for_key(key: str):
  if key in _BOOL_KEYS:
    return st.booleans()
  if key in _TIER_KEYS:
    return st.sampled_from(["small", "medium", "large"])
  if key in _INT_KEYS:
    return st.integers(min_value=0, max_value=5)
  if key in _OPTIONAL_STR_KEYS:
    return _OPTIONAL_STR_STRATEGY
  if key == "prompts":
    return _PROMPTS_STRATEGY
  if key == "rubric":
    return _RUBRIC_STRATEGY
  raise AssertionError(f"Unexpected key: {key}")

@st.composite
def _settings_strategy(draw):
  merged = {}
  for key in _SIMPLE_KEYS:
    include = draw(st.booleans())
    if not include:
      continue
    merged[key] = draw(_value_strategy_for_key(key))
  return merged


_SETTINGS_STRATEGY = _settings_strategy()


@st.composite
def _course_settings_strategy(draw):
  merged = {}
  for key in _COURSE_KEYS:
    include = draw(st.booleans())
    if not include:
      continue
    merged[key] = draw(_value_strategy_for_key(key))
  return merged


_COURSE_SETTINGS_STRATEGY = _course_settings_strategy()


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
  course_settings=_COURSE_SETTINGS_STRATEGY,
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
  course_settings=_COURSE_SETTINGS_STRATEGY,
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


@hypothesis_settings(max_examples=100, deadline=None)
@given(
  key=st.sampled_from(_SIMPLE_KEYS),
  type_value=st.data(),
  course_value=st.data(),
  group_value=st.data(),
  assignment_value=st.data(),
)
def test_assignment_level_settings_override_group_level_for_each_key(
    key, type_value, course_value, group_value, assignment_value):
  expected_assignment_value = assignment_value.draw(_value_strategy_for_key(key))
  merged = _collect_merged_settings(
    {key: type_value.draw(_value_strategy_for_key(key))},
    {key: course_value.draw(_value_strategy_for_key(key))},
    {key: group_value.draw(_value_strategy_for_key(key))},
    {key: expected_assignment_value},
  )
  assert merged[key] == expected_assignment_value


def test_prompt_templates_assignment_layer_overrides_group_layer():
  merged = _collect_merged_settings(
    {},
    {},
    {"prompts": {"aggregate_analysis": "GROUP {course_name}"}},
    {"prompts": {"aggregate_analysis": "ASSIGN {course_name}"}},
  )
  assert merged["prompt_templates"] == {"aggregate_analysis": "ASSIGN {course_name}"}


def test_rubric_assignment_layer_overrides_group_layer():
  merged = _collect_merged_settings(
    {},
    {},
    {"rubric": {
      "length": {
        "points": 1
      }
    }},
    {"rubric": {
      "length": {
        "points": 3
      }
    }},
  )
  assert merged["rubric"]["length"]["points"] == 3
  assert merged["rubric"]["engagement"]["points"] == 4


@hypothesis_settings(max_examples=100, deadline=None)
@given(
  type_settings=_SETTINGS_STRATEGY,
  course_settings=_COURSE_SETTINGS_STRATEGY,
  group_settings=_SETTINGS_STRATEGY,
  key=st.sampled_from(_OPTIONAL_STR_KEYS),
)
def test_none_optional_values_only_override_target_key(type_settings,
                                                       course_settings,
                                                       group_settings, key):
  baseline = _collect_merged_settings(type_settings, course_settings,
                                      group_settings, {})
  with_none = _collect_merged_settings(type_settings, course_settings,
                                       group_settings, {key: None})

  assert with_none[key] is None
  for merged_key, merged_value in baseline.items():
    if merged_key == key:
      continue
    assert with_none[merged_key] == merged_value


@pytest.mark.parametrize("layer", ["type", "course", "group", "assignment"])
def test_unknown_setting_key_rejected_at_each_layer(layer):
  type_settings = {}
  course_settings = {}
  group_settings = {}
  assignment_settings = {}

  if layer == "type":
    type_settings["unknown_setting"] = True
  elif layer == "course":
    course_settings["unknown_setting"] = True
  elif layer == "group":
    group_settings["unknown_setting"] = True
  elif layer == "assignment":
    assignment_settings["unknown_setting"] = True

  with pytest.raises(ValueError, match="unsupported TextSubmissionGrader setting"):
    _collect_merged_settings(type_settings, course_settings, group_settings,
                             assignment_settings)


def test_merge_with_empty_settings_uses_expected_defaults():
  merged = _collect_merged_settings({}, {}, {}, {})

  assert merged["grade_after_lock_date"] is False
  assert merged["prefer_anthropic"] is False
  assert merged["phase1_tier"] == "small"
  assert merged["phase2_tier"] == "small"
  assert merged["phase25_tier"] == "small"
  assert merged["rate_limit_retries"] == 0
  assert merged["records_dir"] is None
  assert merged["record_retention"] is False
  assert merged["report_errors"] is True
  assert merged["slack_webhook"] is None
  assert merged["slack_token"] is None
  assert merged["slack_channel"] is None
  assert merged["prompt_templates"] == {}
  assert merged["rubric"]["word_threshold"] == 250
  assert merged["rubric"]["total_points"] == 10
