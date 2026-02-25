from __future__ import annotations

import copy
import hashlib
import html
import json
import logging
import re
import secrets
import string
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import canvasapi

from lms_interface.canvas_interface import CanvasCourse

log = logging.getLogger(__name__)

WEEKDAY_TO_INT = {
    "Mon": 0,
    "Tue": 1,
    "Wed": 2,
    "Thu": 3,
    "Fri": 4,
    "Sat": 5,
    "Sun": 6,
}


@dataclass
class CalendarBuildResult:
    normalized_plan: dict[str, Any]
    calendar_json: dict[str, Any]
    calendar_html: str
    warnings: list[str]
    output_paths: dict[str, Path]
    publish_result: dict[str, Any] | None


def _load_yaml_module():
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PyYAML is required for course-plan workflows. "
            "Install dependencies (e.g., `uv sync --dev`)."
        ) from exc
    return yaml


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"Expected ISO date string, got: {value!r}")
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_hhmm(value: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"Expected time in HH:MM format, got: {value!r}") from exc
    return parsed.strftime("%H:%M")


def _time_from_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _date_range(start: date, end: date) -> list[date]:
    if end < start:
        return []
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


def _weekday_to_int(value: str) -> int:
    key = str(value or "").strip()[:3].title()
    if key not in WEEKDAY_TO_INT:
        raise ValueError(f"Invalid weekday value: {value!r}")
    return WEEKDAY_TO_INT[key]


def _slugify(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def infer_title_from_id(topic_id: str) -> str:
    text = topic_id.strip().replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    tokens = text.split(" ")
    normalized_tokens: list[str] = []
    for token in tokens:
        lower = token.lower()
        if lower.startswith("ostep") and any(ch.isdigit() for ch in lower):
            normalized_tokens.append(lower[:5].upper() + lower[5:])
        elif token.isupper():
            normalized_tokens.append(token)
        else:
            normalized_tokens.append(token.capitalize())
    return " ".join(normalized_tokens).strip()


def infer_resource_title(url: str) -> str:
    parsed = urlparse(url)
    leaf = unquote(parsed.path.rstrip("/").split("/")[-1]).strip()
    if leaf:
        return leaf
    domain = parsed.netloc or "resource"
    return domain


def _canonicalize_http_url(url_value: str) -> str:
    parsed = urlsplit(url_value)
    if parsed.scheme not in {"http", "https"}:
        return url_value
    path = quote(unquote(parsed.path), safe="/:@!$&'()*+,;=-._~")
    query = quote(unquote(parsed.query), safe="=&:@!$'()*+,;/?-._~")
    fragment = quote(unquote(parsed.fragment), safe="=&:@!$'()*+,;/?-._~")
    return urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))


def _resolve_resource_url(url_value: str, *, base_url: str | None) -> str:
    parsed = urlparse(url_value)
    if parsed.scheme in {"http", "https"}:
        return _canonicalize_http_url(url_value)
    if base_url:
        joined = urljoin(base_url.rstrip("/") + "/", url_value.lstrip("/"))
        return _canonicalize_http_url(joined)
    return url_value


def _normalize_resource_list(
    values: Any,
    *,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    if not values:
        return []
    normalized: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, str):
            resolved_url = _resolve_resource_url(item, base_url=base_url)
            normalized.append(
                {
                    "title": infer_resource_title(resolved_url),
                    "url": resolved_url,
                }
            )
            continue
        if not isinstance(item, dict):
            raise ValueError(f"Invalid resource item: {item!r}")
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"Resource is missing a valid url: {item!r}")
        resolved_url = _resolve_resource_url(url, base_url=base_url)
        entry: dict[str, Any] = {
            "title": item.get("title") or infer_resource_title(resolved_url),
            "url": resolved_url,
        }
        if "type" in item:
            entry["type"] = item["type"]
        if "required" in item:
            entry["required"] = bool(item["required"])
        normalized.append(entry)
    return normalized


def load_course_plan(plan_path: str | Path) -> dict[str, Any]:
    yaml = _load_yaml_module()
    raw = yaml.safe_load(Path(plan_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Course plan must be a mapping/object at the top level.")
    return raw


def normalize_course_plan(raw_plan: dict[str, Any]) -> dict[str, Any]:
    plan = copy.deepcopy(raw_plan)

    term_raw = plan.get("term") or {}
    term_start = _parse_date(term_raw["start_date"])
    term_end = _parse_date(term_raw["end_date"])
    global_no_class_dates = {
        _parse_date(value) for value in term_raw.get("global_no_class_dates", [])
    }

    breaks: list[dict[str, Any]] = []
    for break_raw in term_raw.get("breaks", []):
        applies_to = break_raw.get("applies_to")
        breaks.append(
            {
                "name": break_raw.get("name", "Break"),
                "kind": break_raw.get("kind", "break"),
                "start_date": _parse_date(break_raw["start_date"]),
                "end_date": _parse_date(break_raw["end_date"]),
                "applies_to": set(applies_to) if applies_to else None,
                "notes": break_raw.get("notes"),
            }
        )

    sections: list[dict[str, Any]] = []
    for section_raw in plan.get("sections", []):
        day_values = section_raw.get("meeting_days", [])
        meeting_days = [WEEKDAY_TO_INT[value] for value in day_values]
        meeting_start_time_raw = section_raw.get("meeting_start_time")
        meeting_start_time = (
            _parse_hhmm(str(meeting_start_time_raw))
            if meeting_start_time_raw is not None
            else None
        )
        sections.append(
            {
                "id": section_raw["id"],
                "name": section_raw.get("name", section_raw["id"]),
                "meeting_days": sorted(set(meeting_days)),
                "meeting_day_labels": day_values,
                "canvas_course_id": section_raw.get("canvas_course_id"),
                "canvas_section_id": section_raw.get("canvas_section_id"),
                "canvas_section_selector": (
                    str(
                        section_raw.get(
                            "canvas_section_selector",
                            section_raw.get("canvas_section_filter", ""),
                        )
                    ).strip()
                    or None
                ),
                "meeting_start_time": meeting_start_time,
            }
        )

    exam_coverage: dict[str, list[str]] = {}
    raw_exam_coverage = plan.get("exam_coverage", {}) or {}
    for exam_name, topic_ids in raw_exam_coverage.items():
        exam_coverage[exam_name] = list(topic_ids)

    resource_defaults_raw = plan.get("resource_defaults") or {}
    resource_defaults = {
        "lecture_slides_base_url": resource_defaults_raw.get("lecture_slides_base_url"),
        "readings_base_url": resource_defaults_raw.get("readings_base_url"),
        "resources_base_url": resource_defaults_raw.get("resources_base_url"),
    }

    placeholders_raw = plan.get("placeholders") or {}
    if not isinstance(placeholders_raw, dict):
        raise ValueError("`placeholders` must be a mapping when provided.")

    used_topic_ids: dict[str, int] = {}
    placeholder_counters: dict[str, int] = {}

    topics: list[dict[str, Any]] = []
    for topic_raw in plan.get("topics", []):
        working_topic = copy.deepcopy(topic_raw)

        placeholder_key = working_topic.pop("placeholder", None)
        if placeholder_key:
            template = placeholders_raw.get(placeholder_key)
            if not isinstance(template, dict):
                raise ValueError(
                    f"Topic placeholder '{placeholder_key}' is not defined in top-level `placeholders`."
                )
            merged_topic = copy.deepcopy(template)
            merged_topic.update(working_topic)
            working_topic = merged_topic
            if "id" not in working_topic:
                placeholder_slug = _slugify(str(placeholder_key))
                placeholder_counters[placeholder_slug] = (
                    placeholder_counters.get(placeholder_slug, 0) + 1
                )
                working_topic["id"] = (
                    f"{placeholder_slug}-{placeholder_counters[placeholder_slug]}"
                )

        topic_id = str(working_topic.get("id") or "").strip()
        if not topic_id:
            title_value = str(working_topic.get("title") or "").strip()
            if not title_value:
                raise ValueError("Each topic requires either `id` or `title`.")
            topic_id = _slugify(title_value)
        used_topic_ids[topic_id] = used_topic_ids.get(topic_id, 0) + 1
        if used_topic_ids[topic_id] > 1:
            topic_id = f"{topic_id}-{used_topic_ids[topic_id]}"

        title = working_topic.get("title") or infer_title_from_id(topic_id)
        meetings = int(working_topic.get("meetings", 1))
        duration_hours = int(
            working_topic.get("duration_hours", working_topic.get("hours", meetings))
        )
        topic = {
            "id": topic_id,
            "title": str(title).strip(),
            "meetings": meetings,
            "duration_hours": max(1, duration_hours),
            "new_material": bool(working_topic.get("new_material", True)),
            "lecture_slides": _normalize_resource_list(
                working_topic.get("lecture_slides"),
                base_url=resource_defaults.get("lecture_slides_base_url"),
            ),
            "readings": _normalize_resource_list(
                working_topic.get("readings"),
                base_url=resource_defaults.get("readings_base_url"),
            ),
            "resources": _normalize_resource_list(
                working_topic.get("resources"),
                base_url=resource_defaults.get("resources_base_url"),
            ),
            "notes": working_topic.get("notes"),
            "tags": list(working_topic.get("tags", [])),
        }
        topics.append(topic)
        for exam_name in working_topic.get("appears_on", []) or []:
            exam_coverage.setdefault(exam_name, []).append(topic_id)

    sync_raw = plan.get("sync") or {}
    topics_per_meeting = int(
        sync_raw.get("topics_per_meeting", sync_raw.get("hours_per_meeting", 1))
    )
    sync = {
        "mode": sync_raw.get("mode", "lockstep_by_topic"),
        "skip_for_all_if_any_section_skips": bool(
            sync_raw.get("skip_for_all_if_any_section_skips", True)
        ),
        "carry_over_policy": sync_raw.get("carry_over_policy", "defer_topic"),
        "topics_per_meeting": max(1, topics_per_meeting),
        "hours_per_meeting": max(1, topics_per_meeting),
    }

    publishing_raw = plan.get("publishing") or {}
    attendance_raw = publishing_raw.get("attendance") or {}
    weekly_slides_indent = int(publishing_raw.get("weekly_slides_indent", 1))
    attendance_password_env = str(
        attendance_raw.get("password_env", "ATTENDANCE_QUIZ_PASSWORD")
    ).strip()
    attendance_password = attendance_raw.get("password")
    if attendance_password is not None:
        attendance_password = str(attendance_password).strip()
    elif attendance_password_env:
        import os

        attendance_password = str(os.environ.get(attendance_password_env, "")).strip()
        if not attendance_password:
            attendance_password = None
    publishing = {
        "module_name_template": publishing_raw.get(
            "module_name_template",
            "Week {week_number}",
        ),
        "schedule_page_title": publishing_raw.get(
            "schedule_page_title",
            "Course Schedule",
        ),
        "create_calendar_html": bool(publishing_raw.get("create_calendar_html", True)),
        "weekly_slides_title_prefix": str(
            publishing_raw.get("weekly_slides_title_prefix", "Slides: ")
        ),
        "weekly_slides_indent": max(0, weekly_slides_indent),
        "weekly_slides_section_header": (
            str(publishing_raw["weekly_slides_section_header"]).strip()
            if publishing_raw.get("weekly_slides_section_header") is not None
            else "Slides"
        ),
        "weekly_slides_prune_existing": bool(
            publishing_raw.get("weekly_slides_prune_existing", True)
        ),
        "attendance": {
            "enabled": bool(attendance_raw.get("enabled", False)),
            "assignment_group_name": str(
                attendance_raw.get("assignment_group_name", "Attendance")
            ).strip(),
            "title_prefix": str(
                attendance_raw.get("title_prefix", "Attendance: ")
            ),
            "description": str(
                attendance_raw.get(
                    "description",
                    "<p>Please complete this short attendance check-in.</p>",
                )
            ),
            "points_possible": float(attendance_raw.get("points_possible", 1.0)),
            "unlock_minutes_before_start": int(
                attendance_raw.get("unlock_minutes_before_start", 10)
            ),
            "due_minutes_after_start": int(
                attendance_raw.get("due_minutes_after_start", 10)
            ),
            "lock_minutes_after_start": int(
                attendance_raw.get("lock_minutes_after_start", 10)
            ),
            "password": attendance_password,
            "password_env": attendance_password_env,
            "random_password_length": int(attendance_raw.get("random_password_length", 4)),
            "rotate_random_password_on_update": bool(
                attendance_raw.get("rotate_random_password_on_update", False)
            ),
            "module_section_header": str(
                attendance_raw.get("module_section_header", "Attendence")
            ).strip(),
            "module_indent": max(0, int(attendance_raw.get("module_indent", 1))),
            "include_exam_days": bool(attendance_raw.get("include_exam_days", True)),
            "prune_section_overrides": bool(
                attendance_raw.get("prune_section_overrides", True)
            ),
        },
    }

    if publishing["attendance"]["enabled"]:
        missing_time_sections = [
            section["id"] for section in sections if not section["meeting_start_time"]
        ]
        if missing_time_sections:
            raise ValueError(
                "Attendance publishing requires `meeting_start_time` (HH:MM) for each section. "
                f"Missing for: {', '.join(missing_time_sections)}"
            )

    exam_rules_raw = plan.get("exam_rules") or {}
    exam_defaults_raw = exam_rules_raw.get("defaults") or {}
    exam_rules = {
        "schedule_mode": exam_rules_raw.get("schedule_mode", "derived"),
        "defaults": {
            "class_meeting_index_in_week": int(
                exam_defaults_raw.get("class_meeting_index_in_week", 2)
            ),
            "min_class_meetings_after_last_new_material": int(
                exam_defaults_raw.get("min_class_meetings_after_last_new_material", 1)
            ),
            "prefer_before_break": bool(
                exam_defaults_raw.get("prefer_before_break", True)
            ),
            "require_before_break": bool(
                exam_defaults_raw.get("require_before_break", False)
            ),
        },
        "groups": list(exam_rules_raw.get("groups", [])),
    }

    assignment_templates: list[dict[str, Any]] = []
    for idx, assignment_raw in enumerate(plan.get("assignments", []) or [], start=1):
        if not isinstance(assignment_raw, dict):
            raise ValueError(
                f"Each item in `assignments` must be a mapping/object, got: {assignment_raw!r}"
            )
        assignment_type = str(assignment_raw.get("type") or "").strip().lower()
        if assignment_type not in {"weekly_study_notes", "programming_assignment"}:
            raise ValueError(
                "Assignment `type` must be one of: "
                "`weekly_study_notes`, `programming_assignment`."
            )

        template_id = str(
            assignment_raw.get("id") or f"{assignment_type}-{idx}"
        ).strip()
        contents_raw = assignment_raw.get("contents")
        if isinstance(contents_raw, str):
            markdown_value = contents_raw
        elif isinstance(contents_raw, dict):
            markdown_value = str(contents_raw.get("markdown") or "")
        elif contents_raw is None:
            markdown_value = str(assignment_raw.get("markdown") or "")
        else:
            raise ValueError(
                f"Assignment '{template_id}' has invalid `contents`: {contents_raw!r}"
            )

        submission_types = list(
            assignment_raw.get("submission_types") or ["online_text_entry"]
        )
        rules_raw = assignment_raw.get("rules") or {}
        template: dict[str, Any] = {
            "id": template_id,
            "type": assignment_type,
            "title": str(assignment_raw.get("title") or "").strip() or None,
            "title_template": str(assignment_raw.get("title_template") or "").strip()
            or None,
            "assignment_group_name": str(
                assignment_raw.get("assignment_group_name")
                or (
                    "Weekly Study Notes"
                    if assignment_type == "weekly_study_notes"
                    else "Programming Assignments"
                )
            ).strip(),
            "points_possible": float(assignment_raw.get("points_possible", 1.0)),
            "published": bool(assignment_raw.get("published", True)),
            "submission_types": [str(value) for value in submission_types],
            "contents": {"markdown": markdown_value},
            "rules": {},
        }

        if assignment_type == "weekly_study_notes":
            due_time = _parse_hhmm(str(rules_raw.get("due_time", "23:59")))
            unlock_time = _parse_hhmm(str(rules_raw.get("unlock_time", "00:00")))
            lock_time = _parse_hhmm(str(rules_raw.get("lock_time", due_time)))
            template["rules"] = {
                "include_exam_days": bool(rules_raw.get("include_exam_days", True)),
                "include_exam_only_weeks": bool(
                    rules_raw.get("include_exam_only_weeks", False)
                ),
                "unlock_time": unlock_time,
                "due_anchor": str(
                    rules_raw.get("due_anchor", "week_last_class")
                ).strip(),
                "due_days_after": int(rules_raw.get("due_days_after", 0)),
                "due_time": due_time,
                "lock_days_after_due": int(rules_raw.get("lock_days_after_due", 0)),
                "lock_time": lock_time,
            }
        else:
            instances: list[dict[str, Any]] = []
            for inst_idx, instance_raw in enumerate(
                list(rules_raw.get("instances") or []), start=1
            ):
                if not isinstance(instance_raw, dict):
                    raise ValueError(
                        f"Assignment '{template_id}' has invalid programming `instance`: {instance_raw!r}"
                    )
                release_raw = instance_raw.get("release") or {}
                release_week = int(
                    release_raw.get("week", instance_raw.get("release_week", 0))
                )
                if release_week <= 0:
                    raise ValueError(
                        f"Assignment '{template_id}' instance {inst_idx} requires release.week > 0."
                    )
                release_day = int(
                    release_raw.get("day", instance_raw.get("release_day", 2))
                )
                release_time = _parse_hhmm(
                    str(release_raw.get("time", instance_raw.get("release_time", "00:00")))
                )

                due_raw = instance_raw.get("due") or {}
                due_strategy = str(due_raw.get("strategy", "recommended")).strip()
                preferred_weekday_value = due_raw.get("preferred_weekday", "Sun")
                preferred_weekday = _weekday_to_int(str(preferred_weekday_value))
                instance = {
                    "id": str(instance_raw.get("id") or f"{template_id}-{inst_idx}"),
                    "title": str(instance_raw.get("title") or "").strip() or None,
                    "release": {
                        "week": release_week,
                        "day": release_day,
                        "time": release_time,
                    },
                    "due": {
                        "strategy": due_strategy,
                        "week": (
                            int(due_raw["week"])
                            if due_raw.get("week") is not None
                            else None
                        ),
                        "day": (
                            int(due_raw["day"])
                            if due_raw.get("day") is not None
                            else None
                        ),
                        "time": _parse_hhmm(str(due_raw.get("time", "23:59"))),
                        "min_days_after_release": int(
                            due_raw.get("min_days_after_release", 7)
                        ),
                        "preferred_weekday": preferred_weekday,
                        "include_exam_days": bool(
                            due_raw.get("include_exam_days", False)
                        ),
                    },
                }
                instances.append(instance)
            template["rules"] = {"instances": instances}

        assignment_templates.append(template)

    return {
        "version": str(plan.get("version", "1.1")),
        "term": {
            "start_date": term_start,
            "end_date": term_end,
            "timezone": term_raw.get("timezone", "America/Los_Angeles"),
            "global_no_class_dates": global_no_class_dates,
            "breaks": breaks,
        },
        "sections": sections,
        "sync": sync,
        "topics": topics,
        "placeholders": placeholders_raw,
        "exam_rules": exam_rules,
        "exam_coverage": exam_coverage,
        "resource_defaults": resource_defaults,
        "publishing": publishing,
        "assignments": assignment_templates,
    }


def _blocked_dates_for_section(term: dict[str, Any], section_id: str) -> set[date]:
    blocked = set(term["global_no_class_dates"])
    for break_period in term["breaks"]:
        applies_to = break_period["applies_to"]
        if applies_to and section_id not in applies_to:
            continue
        blocked.update(
            _date_range(break_period["start_date"], break_period["end_date"])
        )
    return blocked


def _section_slots(plan: dict[str, Any]) -> dict[str, dict[tuple[date, int], date]]:
    term = plan["term"]
    start_date = term["start_date"]
    end_date = term["end_date"]
    slots_by_section: dict[str, dict[tuple[date, int], date]] = {}

    for section in plan["sections"]:
        section_id = section["id"]
        blocked_dates = _blocked_dates_for_section(term, section_id)
        meeting_days = set(section["meeting_days"])

        weekly_dates: dict[date, list[date]] = {}
        for day in _date_range(start_date, end_date):
            if day.weekday() not in meeting_days:
                continue
            if day in blocked_dates:
                continue
            week_start = day - timedelta(days=day.weekday())
            weekly_dates.setdefault(week_start, []).append(day)

        section_slots: dict[tuple[date, int], date] = {}
        for week_start, dates in weekly_dates.items():
            for idx, meeting_date in enumerate(sorted(dates), start=1):
                section_slots[(week_start, idx)] = meeting_date
        slots_by_section[section_id] = section_slots

    return slots_by_section


def _next_break_start_after(term: dict[str, Any], pivot: date) -> date | None:
    starts = []
    for break_period in term["breaks"]:
        start_date = break_period["start_date"]
        if start_date > pivot:
            starts.append(start_date)
    return min(starts) if starts else None


def _no_class_labels_for_date(
    term: dict[str, Any],
    *,
    section_id: str,
    target_date: date,
) -> list[str]:
    labels: list[str] = []
    if target_date in term["global_no_class_dates"]:
        labels.append("No class")
    for break_period in term["breaks"]:
        applies_to = break_period["applies_to"]
        if applies_to and section_id not in applies_to:
            continue
        if break_period["start_date"] <= target_date <= break_period["end_date"]:
            labels.append(str(break_period["name"]))
    return labels


def _normalize_no_class_label(label: str) -> str:
    normalized = str(label).strip()
    # Keep break labels concise in schedule notices (e.g., "Break week 11" -> "Break week").
    normalized = re.sub(r"(?i)\bbreak week\s+\d+\b", "Break week", normalized)
    return normalized


def _slot_index_for_section_date(
    slots: list[dict[str, Any]],
    *,
    section_id: str,
    meeting_date: date,
) -> int | None:
    for idx, slot in enumerate(slots):
        if slot["dates"].get(section_id) == meeting_date:
            return idx
    return None


def build_schedule(plan: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    slots_by_section = _section_slots(plan)
    sections = plan["sections"]
    section_ids = [section["id"] for section in sections]
    key_sets = [set(slots_by_section[sid].keys()) for sid in section_ids]

    if not key_sets:
        raise ValueError("At least one section is required.")

    sync_mode = plan["sync"]["mode"]
    skip_all = plan["sync"]["skip_for_all_if_any_section_skips"]
    if sync_mode == "lockstep_by_topic":
        if skip_all and len(key_sets) > 1:
            selected_keys = set.intersection(*key_sets)
        else:
            selected_keys = set.union(*key_sets)
    else:
        selected_keys = key_sets[0]

    sorted_keys = sorted(selected_keys, key=lambda key: (key[0], key[1]))
    week_numbers: dict[date, int] = {}
    for key in sorted_keys:
        week_start = key[0]
        if week_start not in week_numbers:
            week_numbers[week_start] = len(week_numbers) + 1

    slots: list[dict[str, Any]] = []
    for key in sorted_keys:
        week_start, slot_in_week = key
        slot = {
            "week_start": week_start,
            "week_number": week_numbers[week_start],
            "slot_in_week": slot_in_week,
            "dates": {
                section_id: slots_by_section[section_id].get(key)
                for section_id in section_ids
            },
            "allocations": [],
            "topic": None,
        }
        slots.append(slot)

    hours_per_meeting = plan["sync"]["topics_per_meeting"]

    exam_defaults = plan["exam_rules"]["defaults"]
    exam_groups = list(plan["exam_rules"]["groups"])
    if not exam_groups and plan["exam_coverage"]:
        for exam_name, topics in plan["exam_coverage"].items():
            if not topics:
                continue
            exam_groups.append(
                {
                    "name": exam_name,
                    "included_topics": topics,
                }
            )

    exam_warnings: list[str] = []
    exam_dates: dict[str, dict[str, str]] = {}
    exam_slots_by_name: dict[str, set[int]] = {}
    blocked_slot_indices: set[int] = set()

    for group in exam_groups:
        name = str(group.get("name") or "").strip()
        if not name:
            continue
        group_slots: set[int] = set()
        section_exam_dates: dict[str, str] = {}
        fixed_dates = group.get("fixed_date_overrides", {}) or {}
        for section_id, raw_date in fixed_dates.items():
            if section_id not in section_ids:
                exam_warnings.append(
                    f"Exam '{name}' has fixed_date_overrides for unknown section '{section_id}'."
                )
                continue
            parsed_date = _parse_date(raw_date)
            section_exam_dates[section_id] = parsed_date.isoformat()
            slot_idx_for_date = _slot_index_for_section_date(
                slots,
                section_id=section_id,
                meeting_date=parsed_date,
            )
            if slot_idx_for_date is not None:
                group_slots.add(slot_idx_for_date)

        if section_exam_dates:
            exam_dates[name] = section_exam_dates
        if group_slots:
            exam_slots_by_name[name] = set(group_slots)
            blocked_slot_indices.update(group_slots)

    def _allocate_topics(
        blocked_indices: set[int],
    ) -> tuple[dict[str, int], list[str]]:
        local_warnings: list[str] = []
        topic_slot_index: dict[str, int] = {}
        for slot in slots:
            slot["allocations"] = []
            slot["topic"] = None

        slot_idx = 0
        slot_hours_remaining = hours_per_meeting
        for topic in plan["topics"]:
            remaining_topic_hours = max(1, int(topic.get("duration_hours", 1)))
            while remaining_topic_hours > 0:
                while slot_idx < len(slots) and slot_idx in blocked_indices:
                    slot_idx += 1
                    slot_hours_remaining = hours_per_meeting

                if slot_idx >= len(slots):
                    local_warnings.append(
                        f"Topic '{topic['id']}' did not fit in available meeting slots ({remaining_topic_hours}h unscheduled)."
                    )
                    break

                allocation_hours = min(remaining_topic_hours, slot_hours_remaining)
                slots[slot_idx]["allocations"].append(
                    {
                        "topic": topic,
                        "hours": allocation_hours,
                    }
                )
                slots[slot_idx]["topic"] = slots[slot_idx]["allocations"][0]["topic"]
                topic_slot_index[topic["id"]] = slot_idx

                remaining_topic_hours -= allocation_hours
                slot_hours_remaining -= allocation_hours
                if slot_hours_remaining == 0:
                    slot_idx += 1
                    slot_hours_remaining = hours_per_meeting

        return topic_slot_index, local_warnings

    topic_slot_index, allocation_warnings = _allocate_topics(blocked_slot_indices)

    for group in exam_groups:
        name = str(group.get("name") or "").strip()
        if not name:
            continue

        section_exam_dates = dict(exam_dates.get(name, {}))
        group_exam_slots = set(exam_slots_by_name.get(name, set()))
        if len(section_exam_dates) < len(section_ids) and group_exam_slots:
            anchor_slot = slots[min(group_exam_slots)]
            for section_id in section_ids:
                if section_id in section_exam_dates:
                    continue
                meeting_date = anchor_slot["dates"].get(section_id)
                if meeting_date is not None:
                    section_exam_dates[section_id] = meeting_date.isoformat()

        coverage_topics = list(group.get("included_topics", []))
        if not coverage_topics and name in plan["exam_coverage"]:
            coverage_topics = list(plan["exam_coverage"][name])
        through_topic = group.get("through_topic")
        if through_topic:
            coverage_topics.append(through_topic)

        last_slot_index = None
        for topic_id in coverage_topics:
            slot_idx = topic_slot_index.get(topic_id)
            if slot_idx is not None:
                if last_slot_index is None or slot_idx > last_slot_index:
                    last_slot_index = slot_idx

        if len(section_exam_dates) < len(section_ids):
            if last_slot_index is None:
                exam_warnings.append(
                    f"Exam '{name}' could not be derived (no through_topic/included_topics matched scheduled topics)."
                )
            else:
                min_gap = int(
                    group.get(
                        "min_class_meetings_after_last_new_material",
                        exam_defaults["min_class_meetings_after_last_new_material"],
                    )
                )
                meeting_index = int(
                    group.get(
                        "class_meeting_index_in_week",
                        exam_defaults["class_meeting_index_in_week"],
                    )
                )
                earliest_idx = last_slot_index + min_gap + 1
                candidate_indices = [
                    idx
                    for idx in range(earliest_idx, len(slots))
                    if idx not in blocked_slot_indices
                    if slots[idx]["slot_in_week"] == meeting_index
                ]
                if not candidate_indices:
                    candidate_indices = [
                        idx
                        for idx in range(earliest_idx, len(slots))
                        if idx not in blocked_slot_indices
                    ]

                prefer_before_break = bool(
                    group.get(
                        "prefer_before_break", exam_defaults["prefer_before_break"]
                    )
                )
                require_before_break = bool(
                    group.get(
                        "require_before_break", exam_defaults["require_before_break"]
                    )
                )
                if candidate_indices and (prefer_before_break or require_before_break):
                    pivot_date = min(
                        d
                        for d in slots[last_slot_index]["dates"].values()
                        if d is not None
                    )
                    next_break_start = _next_break_start_after(plan["term"], pivot_date)
                    if next_break_start is not None:
                        before_break = []
                        for idx in candidate_indices:
                            slot_dates = [
                                d for d in slots[idx]["dates"].values() if d is not None
                            ]
                            if slot_dates and max(slot_dates) < next_break_start:
                                before_break.append(idx)
                        if before_break:
                            candidate_indices = before_break
                        elif require_before_break:
                            exam_warnings.append(
                                f"Exam '{name}' requires placement before break starting {next_break_start.isoformat()}, but no slot matched."
                            )

                if candidate_indices:
                    chosen_idx = candidate_indices[0]
                    chosen_slot = slots[chosen_idx]
                    group_exam_slots.add(chosen_idx)
                    for section_id in section_ids:
                        if section_id in section_exam_dates:
                            continue
                        meeting_date = chosen_slot["dates"].get(section_id)
                        if meeting_date is not None:
                            section_exam_dates[section_id] = meeting_date.isoformat()
                    if chosen_idx not in blocked_slot_indices:
                        blocked_slot_indices.add(chosen_idx)
                        topic_slot_index, allocation_warnings = _allocate_topics(
                            blocked_slot_indices
                        )
                else:
                    exam_warnings.append(
                        f"Exam '{name}' could not be scheduled; no remaining candidate slots."
                    )

        if section_exam_dates:
            exam_dates[name] = section_exam_dates
        if group_exam_slots:
            exam_slots_by_name[name] = group_exam_slots

    warnings.extend(allocation_warnings)
    warnings.extend(exam_warnings)

    topic_to_exams: dict[str, set[str]] = {}
    for exam_name, topic_ids in plan["exam_coverage"].items():
        for topic_id in topic_ids:
            topic_to_exams.setdefault(topic_id, set()).add(exam_name)

    rows = []
    slot_exam_lookup: dict[int, list[str]] = {}
    for exam_name, slot_indices in exam_slots_by_name.items():
        for idx in slot_indices:
            slot_exam_lookup.setdefault(idx, []).append(exam_name)

    for idx, slot in enumerate(slots):
        allocations = slot.get("allocations", [])
        primary_topic = allocations[0]["topic"] if allocations else None
        exam_names = sorted(slot_exam_lookup.get(idx, []))
        readings: list[dict[str, Any]] = []
        lecture_slides: list[dict[str, Any]] = []
        resources: list[dict[str, Any]] = []
        topic_ids: list[str] = []
        topic_titles: list[str] = []
        topic_allocations: list[dict[str, Any]] = []
        coverage_exams: set[str] = set()
        for allocation in allocations:
            topic = allocation["topic"]
            hours = int(allocation["hours"])
            topic_ids.append(topic["id"])
            topic_exam_names = sorted(topic_to_exams.get(topic["id"], set()))
            coverage_exams.update(topic_exam_names)
            readings.extend(topic["readings"])
            lecture_slides.extend(topic["lecture_slides"])
            resources.extend(topic["resources"])
            label = topic["title"] if hours == 1 else f"{topic['title']} ({hours}h)"
            topic_titles.append(label)
            topic_allocations.append(
                {
                    "topic_id": topic["id"],
                    "topic_title": topic["title"],
                    "hours": hours,
                }
            )
        rows.append(
            {
                "week_number": slot["week_number"],
                "slot_in_week": slot["slot_in_week"],
                "dates": {
                    sid: value.isoformat() if value else None
                    for sid, value in slot["dates"].items()
                },
                "topic_ids": topic_ids,
                "topic_titles": topic_titles,
                "topic_allocations": topic_allocations,
                "topic_id": primary_topic["id"] if primary_topic else None,
                "topic_title": primary_topic["title"] if primary_topic else None,
                "exam_names": exam_names,
                "coverage_exams": sorted(coverage_exams),
                "readings": readings,
                "lecture_slides": lecture_slides,
                "resources": resources,
                "new_material": any(
                    bool(allocation["topic"]["new_material"])
                    for allocation in allocations
                ),
                "no_class": False,
                "no_class_label": None,
                "_week_start": slot["week_start"],
            }
        )

    existing_slot_keys = {(slot["week_start"], slot["slot_in_week"]) for slot in slots}
    no_class_rows_by_key: dict[tuple[date, int], dict[str, Any]] = {}
    term_start = plan["term"]["start_date"]
    term_end = plan["term"]["end_date"]
    for section in sections:
        section_id = section["id"]
        day_to_slot_in_week = {
            day_value: idx for idx, day_value in enumerate(section["meeting_days"], start=1)
        }
        blocked_dates = sorted(_blocked_dates_for_section(plan["term"], section_id))
        for blocked_date in blocked_dates:
            if blocked_date < term_start or blocked_date > term_end:
                continue
            slot_in_week = day_to_slot_in_week.get(blocked_date.weekday())
            if slot_in_week is None:
                continue
            week_start = blocked_date - timedelta(days=blocked_date.weekday())
            key = (week_start, slot_in_week)
            if key in existing_slot_keys:
                continue

            candidate_row = no_class_rows_by_key.get(key)
            if candidate_row is None:
                candidate_row = {
                    "week_number": 0,
                    "slot_in_week": slot_in_week,
                    "dates": {sid: None for sid in section_ids},
                    "topic_ids": [],
                    "topic_titles": [],
                    "topic_allocations": [],
                    "topic_id": None,
                    "topic_title": None,
                    "exam_names": [],
                    "coverage_exams": [],
                    "readings": [],
                    "lecture_slides": [],
                    "resources": [],
                    "new_material": False,
                    "no_class": True,
                    "no_class_label": "No class",
                    "_week_start": week_start,
                    "_labels": set(),
                }
                no_class_rows_by_key[key] = candidate_row

            candidate_row["dates"][section_id] = blocked_date.isoformat()
            for label in _no_class_labels_for_date(
                plan["term"],
                section_id=section_id,
                target_date=blocked_date,
            ):
                candidate_row["_labels"].add(label)

    for candidate_row in no_class_rows_by_key.values():
        labels = sorted(candidate_row.pop("_labels"))
        if labels:
            if labels == ["No class"]:
                candidate_row["no_class_label"] = "No class"
            else:
                normalized_labels = [
                    _normalize_no_class_label(label)
                    for label in labels
                    if label != "No class"
                ]
                candidate_row["no_class_label"] = (
                    "No class: " + ", ".join(sorted(set(normalized_labels or labels)))
                )
        rows.append(candidate_row)

    exams_with_slot_rows = {
        exam_name for exam_names in slot_exam_lookup.values() for exam_name in exam_names
    }
    week_numbers_with_exams = dict(week_numbers)
    next_week_number = len(week_numbers_with_exams) + 1
    for exam_name, section_dates in exam_dates.items():
        if exam_name in exams_with_slot_rows:
            continue
        parsed_dates: dict[str, date] = {}
        for section_id, iso_date in section_dates.items():
            try:
                parsed_dates[section_id] = _parse_date(iso_date)
            except Exception:
                continue
        if not parsed_dates:
            continue

        earliest_exam_date = min(parsed_dates.values())
        exam_week_start = earliest_exam_date - timedelta(days=earliest_exam_date.weekday())
        if exam_week_start not in week_numbers_with_exams:
            week_numbers_with_exams[exam_week_start] = next_week_number
            next_week_number += 1
        rows.append(
            {
                "week_number": week_numbers_with_exams[exam_week_start],
                "slot_in_week": "Exam",
                "dates": {
                    sid: section_dates.get(sid)
                    for sid in section_ids
                },
                "topic_ids": [],
                "topic_titles": [],
                "topic_allocations": [],
                "topic_id": None,
                "topic_title": None,
                "exam_names": [exam_name],
                "coverage_exams": [],
                "readings": [],
                "lecture_slides": [],
                "resources": [],
                "new_material": False,
                "no_class": False,
                "no_class_label": None,
                "_week_start": exam_week_start,
            }
        )

    week_starts_in_order = sorted(
        {
            row["_week_start"]
            for row in rows
            if not bool(row.get("no_class"))
        },
    )
    week_number_by_start = {
        week_start: idx for idx, week_start in enumerate(week_starts_in_order, start=1)
    }
    for row in rows:
        row["week_number"] = week_number_by_start.get(row["_week_start"])

    rows.sort(
        key=lambda row: (
            row["_week_start"],
            0 if isinstance(row.get("slot_in_week"), int) else 1,
            row.get("slot_in_week"),
        )
    )

    grouped_no_class_rows: dict[date, list[dict[str, Any]]] = {}
    for row in rows:
        if bool(row.get("no_class")) and row.get("week_number") is None:
            grouped_no_class_rows.setdefault(row["_week_start"], []).append(row)

    filtered_rows: list[dict[str, Any]] = []
    emitted_no_class_weeks: set[date] = set()
    for row in rows:
        week_start = row["_week_start"]
        if bool(row.get("no_class")) and row.get("week_number") is None:
            if week_start in emitted_no_class_weeks:
                continue
            emitted_no_class_weeks.add(week_start)
            group_rows = grouped_no_class_rows.get(week_start, [row])
            labels = [
                str(group_row.get("no_class_label") or "No class")
                for group_row in group_rows
                if str(group_row.get("no_class_label") or "").strip()
            ]
            unique_labels = sorted(set(labels)) or ["No class"]
            all_dates = sorted(
                {
                    date_value
                    for group_row in group_rows
                    for date_value in group_row.get("dates", {}).values()
                    if date_value
                }
            )
            notice_label = unique_labels[0]
            if len(unique_labels) > 1:
                notice_label = "No class"
            filtered_rows.append(
                {
                    "week_number": None,
                    "slot_in_week": "",
                    "dates": {sid: None for sid in section_ids},
                    "topic_ids": [],
                    "topic_titles": [],
                    "topic_allocations": [],
                    "topic_id": None,
                    "topic_title": None,
                    "exam_names": [],
                    "coverage_exams": [],
                    "readings": [],
                    "lecture_slides": [],
                    "resources": [],
                    "new_material": False,
                    "no_class": True,
                    "no_class_notice": True,
                    "no_class_label": notice_label,
                    "no_class_dates": all_dates,
                }
            )
            continue
        filtered_rows.append(row)

    for row in filtered_rows:
        row.pop("_week_start", None)

    schedule = {
        "sections": sections,
        "rows": filtered_rows,
        "exam_dates": exam_dates,
        "term": {
            "start_date": plan["term"]["start_date"].isoformat(),
            "end_date": plan["term"]["end_date"].isoformat(),
            "timezone": plan["term"]["timezone"],
            "global_no_class_dates": [
                d.isoformat()
                for d in sorted(plan["term"]["global_no_class_dates"])
            ],
            "breaks": [
                {
                    "name": b["name"],
                    "kind": b["kind"],
                    "start_date": b["start_date"].isoformat(),
                    "end_date": b["end_date"].isoformat(),
                    "applies_to": (
                        sorted(list(b["applies_to"])) if b["applies_to"] else None
                    ),
                }
                for b in plan["term"]["breaks"]
            ],
        },
    }
    return schedule, warnings


def _render_resource_links(resources: list[dict[str, Any]]) -> str:
    if not resources:
        return ""
    links = []
    for resource in resources:
        label = html.escape(
            str(resource.get("title") or infer_resource_title(resource["url"]))
        )
        href = html.escape(str(resource["url"]), quote=True)
        links.append(
            f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'
        )
    return "<br/>".join(links)


def render_calendar_html(
    normalized_plan: dict[str, Any],
    calendar_json: dict[str, Any],
) -> str:
    section_columns = calendar_json["sections"]
    show_other_resources = any(
        bool(row.get("resources"))
        for row in calendar_json.get("rows", [])
        if not bool(row.get("no_class_notice"))
    )

    break_items = []
    for break_period in calendar_json["term"]["breaks"]:
        break_name = _normalize_no_class_label(str(break_period["name"]))
        label = (
            f"{break_name}: {break_period['start_date']} to {break_period['end_date']}"
        )
        break_items.append(f"<li>{html.escape(label)}</li>")
    break_html = (
        "<ul>" + "".join(break_items) + "</ul>" if break_items else "<p>None</p>"
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    page_title = html.escape(normalized_plan["publishing"]["schedule_page_title"])
    today = date.today()
    cell_style = "border:1px solid #ccc;padding:8px;vertical-align:top;"
    header_style = (
        "border:1px solid #ccc;padding:8px;vertical-align:top;background:#f5f5f5;"
    )

    rows_html = []
    for row in calendar_json["rows"]:
        if bool(row.get("no_class_notice")):
            notice_text = str(row.get("no_class_label") or "No class")
            total_columns = 2 + len(section_columns) + 3 + (
                1 if show_other_resources else 0
            )
            rows_html.append(
                '<tr class="no-class-notice-row">'
                f'<td colspan="{total_columns}" style="{cell_style}"><strong>{html.escape(notice_text)}</strong></td>'
                "</tr>"
            )
            continue

        titles = row.get("topic_titles") or []
        exam_names = row.get("exam_names") or []
        coverage_exams = row.get("coverage_exams") or []
        no_class = bool(row.get("no_class"))
        if no_class:
            topic_title = f"<em>{html.escape(str(row.get('no_class_label') or 'No class'))}</em>"
        elif titles:
            topic_title = "<br/>".join(
                f"<strong>{html.escape(title)}</strong>" for title in titles
            )
        elif exam_names:
            topic_title = "<br/>".join(
                f"<strong>{html.escape(f'EXAM DAY: {exam}')}</strong>"
                for exam in exam_names
            )
        else:
            topic_title = html.escape(row.get("topic_title") or "")
        readings_html = _render_resource_links(row["readings"])
        slides_html = _render_resource_links(row["lecture_slides"])
        extra_html = _render_resource_links(row["resources"])
        if not readings_html:
            readings_html = "&nbsp;"
        if not slides_html:
            slides_html = "&nbsp;"
        if not extra_html:
            extra_html = "&nbsp;"

        row_dates = []
        for section in section_columns:
            section_id = section["id"]
            date_value = row["dates"].get(section_id)
            if date_value:
                try:
                    row_dates.append(_parse_date(date_value))
                except ValueError:
                    pass

        row_status_class = "upcoming-row"
        if row_dates:
            if max(row_dates) < today:
                row_status_class = "past-row"
            elif min(row_dates) <= today <= max(row_dates):
                row_status_class = "current-row"

        row_classes = [row_status_class]
        if no_class:
            row_classes.append("no-class-row")
        elif exam_names:
            row_classes.append("exam-row")

        coverage_html = ""
        if coverage_exams and not no_class and not exam_names:
            coverage_html = (
                '<div style="margin-top:6px;"><em>Included on: '
                + html.escape(", ".join(coverage_exams))
                + "</em></div>"
            )

        row_cell_style = cell_style
        topic_cell_style = cell_style
        if exam_names and not no_class:
            row_cell_style = (
                cell_style
                + "background:#ffe6c9;border-top:3px solid #2b6cb0;border-bottom:3px solid #2b6cb0;"
            )
            topic_cell_style = (
                row_cell_style
                + "font-size:16px;font-weight:700;letter-spacing:0.2px;"
            )

        cells = [
            f"<td style=\"{row_cell_style}\">{'' if row.get('week_number') is None else row['week_number']}</td>",
            f"<td style=\"{row_cell_style}\">{row['slot_in_week']}</td>",
        ]

        for section in section_columns:
            section_id = section["id"]
            date_value = row["dates"].get(section_id)
            if date_value:
                cells.append(
                    f"<td style=\"{row_cell_style}\">{html.escape(date_value)}</td>"
                )
            else:
                cells.append(
                    f'<td class="muted" style="{row_cell_style}">No class</td>'
                )

        cells.extend(
            [
                f"<td style=\"{topic_cell_style}\">{topic_title if topic_title else '&nbsp;'}{coverage_html}</td>",
                f"<td style=\"{row_cell_style}\">{readings_html}</td>",
                f"<td style=\"{row_cell_style}\">{slides_html}</td>",
            ]
        )
        if show_other_resources:
            cells.append(f"<td style=\"{row_cell_style}\">{extra_html}</td>")

        rows_html.append(
            f'<tr class="{" ".join(row_classes)}">' + "".join(cells) + "</tr>"
        )

    section_headers = "".join(
        f"<th style=\"{header_style}\">{html.escape(section['name'])}</th>"
        for section in section_columns
    )
    other_resources_header = (
        f"<th style=\"{header_style}\">Other Resources</th>"
        if show_other_resources
        else ""
    )

    return f"""<!-- course-plan-generated:start -->
<style>
.course-calendar {{
  --line: #cfd6dd;
  --past-bg: #f7f8fa;
  --current-bg: #e8f3ff;
  --upcoming-bg: #ffffff;
  --no-class-bg: #e6f7ea;
  --exam-bg: #ffe6c9;
  --accent: #2b6cb0;
  font-family: Arial, sans-serif;
  font-size: 14px;
}}
.course-calendar table {{ border-collapse: collapse; width: 100%; border: 1px solid var(--line); }}
.course-calendar .muted {{ color: #777; }}
.course-calendar .past-row td {{ background: var(--past-bg); }}
.course-calendar .current-row td {{ background: var(--current-bg); }}
.course-calendar .upcoming-row td {{ background: var(--upcoming-bg); }}
.course-calendar .no-class-row td {{
  background: var(--no-class-bg);
  border-top: 2px solid #91c49b !important;
  border-bottom: 2px solid #91c49b !important;
}}
.course-calendar .no-class-notice-row td {{
  background: #d9f0df;
  border-top: 3px solid #70a978 !important;
  border-bottom: 3px solid #70a978 !important;
  text-align: center;
}}
.course-calendar .exam-row td {{
  background: var(--exam-bg);
  border-top: 3px solid var(--accent) !important;
  border-bottom: 3px solid var(--accent) !important;
}}
.course-calendar .exam-badge {{
  display: inline-block;
  margin-left: 6px;
  padding: 2px 6px;
  background: #ffe8a3;
  border: 1px solid #e0c34d;
  border-radius: 4px;
  font-size: 12px;
}}
</style>
<div class="course-calendar">
  <h2>{page_title}</h2>
  <p><strong>Generated:</strong> {generated_at}</p>
  <p><strong>Term:</strong> {calendar_json['term']['start_date']} to {calendar_json['term']['end_date']} ({html.escape(calendar_json['term']['timezone'])})</p>
  <h3>Breaks</h3>
  {break_html}
  <h3>Schedule</h3>
  <table>
    <thead>
      <tr>
        <th style="{header_style}">Week</th>
        <th style="{header_style}">Slot</th>
        {section_headers}
        <th style="{header_style}">Topic</th>
        <th style="{header_style}">Readings</th>
        <th style="{header_style}">Slides</th>
        {other_resources_header}
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</div>
<!-- course-plan-generated:end -->
"""


def _find_page_by_title(canvas_course: CanvasCourse, title: str):
    for page in canvas_course.course.get_pages():
        if str(getattr(page, "title", "")).strip() == title.strip():
            return page
    return None


def _find_module_by_name(canvas_course: CanvasCourse, module_name: str):
    for module in canvas_course.course.get_modules():
        if str(getattr(module, "name", "")).strip() == module_name.strip():
            return module
    return None


def _build_weekly_slide_items(
    calendar_json: dict[str, Any],
    *,
    module_name_template: str,
) -> dict[str, list[dict[str, str]]]:
    weekly: dict[str, list[dict[str, str]]] = {}
    seen_by_module: dict[str, set[str]] = {}

    for row in calendar_json.get("rows", []):
        week_number = row.get("week_number")
        try:
            module_name = module_name_template.format(week_number=week_number)
        except Exception:
            module_name = f"Week {week_number}"
        weekly.setdefault(module_name, [])
        seen_by_module.setdefault(module_name, set())

        for slide in row.get("lecture_slides", []) or []:
            if not isinstance(slide, dict):
                continue
            url = str(slide.get("url") or "").strip()
            if not url:
                continue
            if url in seen_by_module[module_name]:
                continue
            seen_by_module[module_name].add(url)
            weekly[module_name].append(
                {
                    "title": str(slide.get("title") or infer_resource_title(url)),
                    "url": url,
                }
            )
    return weekly


def _find_quiz_by_title(canvas_course: CanvasCourse, quiz_title: str):
    for quiz in canvas_course.course.get_quizzes():
        if str(getattr(quiz, "title", "")).strip() == quiz_title.strip():
            return quiz
    return None


def _section_display_label(canvas_section: Any) -> str:
    section_name = str(getattr(canvas_section, "name", "")).strip()
    section_id = getattr(canvas_section, "id", "")
    return f"{section_name} (id={section_id})".strip()


def _section_match_values(canvas_section: Any) -> list[str]:
    values: list[str] = []
    for attr in (
        "sis_section_id",
        "integration_id",
    ):
        value = getattr(canvas_section, attr, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            values.append(text.casefold())
    return values


def _resolve_canvas_section_ids(
    canvas_course: CanvasCourse,
    *,
    sections: list[dict[str, Any]],
) -> dict[str, int]:
    canvas_sections = list(canvas_course.course.get_sections())
    sections_by_id = {int(getattr(section, "id")): section for section in canvas_sections}
    sections_by_name: dict[str, list[Any]] = {}
    for section in canvas_sections:
        key = str(getattr(section, "name", "")).strip().casefold()
        sections_by_name.setdefault(key, []).append(section)

    resolved: dict[str, int] = {}
    for section in sections:
        section_id = str(section["id"])
        configured_canvas_section_id = section.get("canvas_section_id")
        if configured_canvas_section_id is not None:
            try:
                canvas_section_id = int(configured_canvas_section_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Section '{section_id}' has invalid canvas_section_id={configured_canvas_section_id!r}; "
                    "expected an integer."
                ) from exc
            canvas_section = sections_by_id.get(canvas_section_id)
            if canvas_section is None:
                raise ValueError(
                    f"Section '{section_id}' references canvas_section_id={canvas_section_id}, "
                    "but that section was not found in the course."
                )
            resolved[section_id] = canvas_section_id
            continue

        configured_selector = str(section.get("canvas_section_selector") or "").strip()
        if configured_selector:
            selector_key = configured_selector.casefold()
            name_matches = [
                canvas_section
                for canvas_section in canvas_sections
                if selector_key in str(getattr(canvas_section, "name", "")).strip().casefold()
            ]
            if len(name_matches) == 1:
                resolved[section_id] = int(getattr(name_matches[0], "id"))
                continue
            if len(name_matches) > 1:
                match_labels = ", ".join(
                    _section_display_label(m) for m in name_matches
                )
                raise ValueError(
                    f"Section '{section_id}' selector '{configured_selector}' matched multiple Canvas sections: "
                    f"{match_labels}. Refine selector or set `canvas_section_id`."
                )

            metadata_matches = [
                canvas_section
                for canvas_section in canvas_sections
                if any(
                    selector_key in match_value
                    for match_value in _section_match_values(canvas_section)
                )
            ]
            if len(metadata_matches) == 1:
                resolved[section_id] = int(getattr(metadata_matches[0], "id"))
                continue
            if len(metadata_matches) > 1:
                match_labels = ", ".join(
                    _section_display_label(m) for m in metadata_matches
                )
                raise ValueError(
                    f"Section '{section_id}' selector '{configured_selector}' matched multiple Canvas sections "
                    f"(via SIS/integration ids): {match_labels}. Refine selector or set `canvas_section_id`."
                )

            available_labels = ", ".join(
                _section_display_label(candidate) for candidate in canvas_sections
            )
            raise ValueError(
                f"Section '{section_id}' selector '{configured_selector}' did not match any Canvas section. "
                f"Available sections: {available_labels}"
            )

        section_name_key = str(section.get("name", section_id)).strip().casefold()
        matches = sections_by_name.get(section_name_key, [])
        if len(matches) == 1:
            resolved[section_id] = int(getattr(matches[0], "id"))
            continue
        if len(matches) > 1:
            raise ValueError(
                f"Section '{section_id}' name '{section.get('name')}' matched multiple Canvas sections. "
                "Set `canvas_section_selector` or `canvas_section_id` in YAML to disambiguate."
            )
        available_labels = ", ".join(
            _section_display_label(candidate) for candidate in canvas_sections
        )
        raise ValueError(
            f"Could not match YAML section '{section_id}' ('{section.get('name')}') to a Canvas section. "
            "Set `canvas_section_selector`/`canvas_section_id` in YAML or make section names match exactly. "
            f"Available sections: {available_labels}"
        )
    return resolved


def _build_attendance_quiz_plans(
    normalized_plan: dict[str, Any],
    calendar_json: dict[str, Any],
    *,
    attendance_config: dict[str, Any],
) -> list[dict[str, Any]]:
    timezone_name = str(normalized_plan["term"]["timezone"])
    tzinfo = ZoneInfo(timezone_name)
    title_prefix = str(attendance_config.get("title_prefix") or "")
    include_exam_days = bool(attendance_config.get("include_exam_days", True))
    unlock_minutes_before_start = int(
        attendance_config.get("unlock_minutes_before_start", 10)
    )
    due_minutes_after_start = int(attendance_config.get("due_minutes_after_start", 10))
    lock_minutes_after_start = int(
        attendance_config.get("lock_minutes_after_start", 10)
    )

    plans: list[dict[str, Any]] = []
    dedupe_counter: dict[str, int] = {}
    for row in calendar_json.get("rows", []):
        if bool(row.get("no_class")) or bool(row.get("no_class_notice")):
            continue
        if not include_exam_days and bool(row.get("exam_names")):
            continue

        week_number = row.get("week_number")
        slot_in_week = row.get("slot_in_week")
        if week_number is None:
            continue

        slot_text = str(slot_in_week).strip() if slot_in_week is not None else "Slot"
        if isinstance(slot_in_week, int):
            slot_title = f"Day {slot_in_week}"
        else:
            slot_title = slot_text or "Day"
        if isinstance(week_number, int):
            base_title = f"Week {week_number:02d} {slot_title}"
        else:
            base_title = f"Week {week_number} {slot_title}".strip()
        quiz_title = (f"{title_prefix}{base_title}").strip()
        dedupe_counter[quiz_title] = dedupe_counter.get(quiz_title, 0) + 1
        if dedupe_counter[quiz_title] > 1:
            quiz_title = f"{quiz_title} ({dedupe_counter[quiz_title]})"

        windows: list[dict[str, Any]] = []
        for section in normalized_plan["sections"]:
            section_id = str(section["id"])
            section_date_value = str(row.get("dates", {}).get(section_id) or "").strip()
            if not section_date_value:
                continue
            meeting_start_time = section.get("meeting_start_time")
            if not meeting_start_time:
                continue
            section_date = _parse_date(section_date_value)
            section_start_dt = datetime.combine(
                section_date,
                _time_from_hhmm(str(meeting_start_time)),
                tzinfo=tzinfo,
            )
            unlock_at = section_start_dt - timedelta(minutes=unlock_minutes_before_start)
            due_at = section_start_dt + timedelta(minutes=due_minutes_after_start)
            lock_at = section_start_dt + timedelta(minutes=lock_minutes_after_start)
            windows.append(
                {
                    "section_id": section_id,
                    "section_name": str(section.get("name", section_id)),
                    "meeting_date": section_date_value,
                    "unlock_at": unlock_at.isoformat(),
                    "due_at": due_at.isoformat(),
                    "lock_at": lock_at.isoformat(),
                }
            )
        if not windows:
            continue
        plans.append(
            {
                "title": quiz_title,
                "week_number": week_number,
                "slot_in_week": slot_in_week,
                "windows": windows,
            }
        )
    return plans


def _default_attendance_question_payload(*, quiz_title: str) -> dict[str, Any]:
    words = [
        "thread",
        "kernel",
        "process",
        "paging",
        "cache",
        "mutex",
        "signal",
        "socket",
        "inode",
        "binary",
    ]
    digest = hashlib.sha256(quiz_title.encode("utf-8")).hexdigest()
    offset = int(digest[:8], 16) % len(words)
    options = ["I'm here today"]
    for idx in range(3):
        options.append(words[(offset + (idx * 3)) % len(words)])
    answers = []
    for idx, option in enumerate(options):
        answers.append({"answer_text": option, "answer_weight": 100 if idx == 0 else 0})
    return {
        "question_name": "Attendance Check-in",
        "question_text": "<p>Select \"I'm here today\" to confirm attendance.</p>",
        "question_type": "multiple_choice_question",
        "points_possible": 1,
        "answers": answers,
    }


def _generate_random_attendance_password(
    *,
    length: int,
    used_passwords: set[str],
) -> str:
    alphabet = string.ascii_lowercase
    normalized_length = max(3, int(length))
    for _ in range(64):
        candidate = "".join(secrets.choice(alphabet) for _ in range(normalized_length))
        if candidate not in used_passwords:
            used_passwords.add(candidate)
            return candidate
    # Extremely unlikely fallback: include one extra character.
    candidate = "".join(
        secrets.choice(alphabet) for _ in range(normalized_length + 1)
    )
    used_passwords.add(candidate)
    return candidate


def _publish_attendance_quizzes(
    canvas_course: CanvasCourse,
    *,
    normalized_plan: dict[str, Any],
    calendar_json: dict[str, Any],
    weekly_module_template: str,
    dry_run: bool,
) -> list[str]:
    actions: list[str] = []
    attendance_config = normalized_plan["publishing"]["attendance"]
    if not bool(attendance_config.get("enabled", False)):
        return actions

    attendance_plans = _build_attendance_quiz_plans(
        normalized_plan,
        calendar_json,
        attendance_config=attendance_config,
    )
    if not attendance_plans:
        actions.append("attendance:no-quiz-plans")
        return actions

    section_id_map = _resolve_canvas_section_ids(
        canvas_course,
        sections=normalized_plan["sections"],
    )
    managed_canvas_section_ids = set(section_id_map.values())

    assignment_group_name = str(
        attendance_config.get("assignment_group_name") or "Attendance"
    )
    points_possible = float(attendance_config.get("points_possible", 1.0))
    description = str(
        attendance_config.get("description")
        or "<p>Please complete this short attendance check-in.</p>"
    )
    password_value = attendance_config.get("password")
    random_password_length = int(attendance_config.get("random_password_length", 4))
    rotate_random_password_on_update = bool(
        attendance_config.get("rotate_random_password_on_update", False)
    )
    attendance_header_value = str(
        attendance_config.get("module_section_header") or ""
    ).strip()
    attendance_module_indent = max(0, int(attendance_config.get("module_indent", 1)))
    prune_section_overrides = bool(attendance_config.get("prune_section_overrides", True))
    used_random_passwords: set[str] = set()

    def _ensure_module_published(module_obj: Any, *, module_label: str) -> None:
        if bool(getattr(module_obj, "published", False)):
            return
        actions.append(f"publish-module:{module_label}")
        if not dry_run:
            module_obj.edit(module={"published": True})

    def _ensure_module_item_published(item_obj: Any, *, item_label: str) -> None:
        if bool(getattr(item_obj, "published", False)):
            return
        actions.append(f"publish-module-item:{item_label}")
        if not dry_run:
            item_obj.edit(module_item={"published": True})

    assignment_group = None
    if not dry_run:
        assignment_group = canvas_course.create_assignment_group(
            name=assignment_group_name,
            delete_existing=False,
        )

    total_quizzes = len(attendance_plans)
    log.info("Attendance publishing: %d quizzes planned", total_quizzes)
    for quiz_idx, quiz_plan in enumerate(attendance_plans, start=1):
        quiz_title = str(quiz_plan["title"])
        windows = list(quiz_plan.get("windows", []))
        week_number = quiz_plan.get("week_number")
        slot_in_week = quiz_plan.get("slot_in_week")
        window_labels: list[str] = []
        for window in windows:
            meeting_date_raw = str(window.get("meeting_date") or "").strip()
            weekday_label = ""
            if meeting_date_raw:
                try:
                    weekday_label = _parse_date(meeting_date_raw).strftime("%a")
                except ValueError:
                    weekday_label = ""
            section_label = str(window.get("section_name") or window.get("section_id") or "")
            if weekday_label and meeting_date_raw:
                window_labels.append(f"{section_label} {weekday_label} {meeting_date_raw}")
            elif meeting_date_raw:
                window_labels.append(f"{section_label} {meeting_date_raw}")
            else:
                window_labels.append(section_label)
        log.info(
            "Attendance quiz %d/%d | Week %s Day %s | %s | %d section windows",
            quiz_idx,
            total_quizzes,
            week_number,
            slot_in_week,
            quiz_title,
            len(windows),
        )
        if window_labels:
            log.info("  Sections: %s", " | ".join(window_labels))

        existing_quiz = _find_quiz_by_title(canvas_course, quiz_title)
        quiz_payload: dict[str, Any] = {
            "title": quiz_title,
            "description": description,
            "quiz_type": "graded_survey",
            "published": True,
            "points_possible": points_possible,
            "only_visible_to_overrides": True,
            "allowed_attempts": 1,
        }
        if assignment_group is not None:
            quiz_payload["assignment_group_id"] = int(getattr(assignment_group, "id"))
        static_password_value = (
            password_value.strip()
            if isinstance(password_value, str) and password_value.strip()
            else None
        )
        if static_password_value:
            quiz_payload["access_code"] = static_password_value

        if existing_quiz is None:
            actions.append(f"create-attendance-quiz:{quiz_title}")
            log.info("  Creating quiz: %s", quiz_title)
            if static_password_value is None:
                generated_password = _generate_random_attendance_password(
                    length=random_password_length,
                    used_passwords=used_random_passwords,
                )
                quiz_payload["access_code"] = generated_password
                log.info("    Attendance password: %s", generated_password)
            if dry_run:
                for window in windows:
                    actions.append(
                        f"plan-attendance-window:{quiz_title}:{window['section_id']}:{window['unlock_at']}..{window['lock_at']}"
                    )
                    log.info(
                        "    Plan window: %s %s -> %s",
                        window["section_id"],
                        window["unlock_at"],
                        window["lock_at"],
                    )
                continue
            quiz_obj = canvas_course.course.create_quiz(quiz=quiz_payload)
        else:
            actions.append(f"update-attendance-quiz:{quiz_title}")
            log.info("  Updating quiz: %s", quiz_title)
            if static_password_value is None and rotate_random_password_on_update:
                generated_password = _generate_random_attendance_password(
                    length=random_password_length,
                    used_passwords=used_random_passwords,
                )
                quiz_payload["access_code"] = generated_password
                log.info("    Rotated attendance password: %s", generated_password)
            if dry_run:
                quiz_obj = existing_quiz
            else:
                quiz_obj = existing_quiz.edit(quiz=quiz_payload)

        module_name = None
        try:
            module_name = str(weekly_module_template).format(week_number=week_number)
        except Exception:
            module_name = f"Week {week_number}"

        weekly_module = _find_module_by_name(canvas_course, module_name)
        if weekly_module is None:
            actions.append(f"create-module:{module_name}")
            log.info("  Creating module for attendance quiz: %s", module_name)
            if not dry_run:
                weekly_module = canvas_course.course.create_module(
                    module={"name": module_name, "published": True}
                )
        else:
            actions.append(f"reuse-module:{module_name}")

        if weekly_module is not None:
            _ensure_module_published(weekly_module, module_label=module_name)

        existing_subheader = None
        existing_quiz_item = None
        if weekly_module is not None:
            for module_item in weekly_module.get_module_items():
                item_type = str(getattr(module_item, "type", "")).lower().replace(
                    "_", ""
                )
                item_title = str(getattr(module_item, "title", "")).strip()
                if (
                    attendance_header_value
                    and existing_subheader is None
                    and item_type == "subheader"
                    and item_title == attendance_header_value
                ):
                    existing_subheader = module_item
                    continue
                if item_type == "quiz":
                    content_id = getattr(module_item, "content_id", None)
                    try:
                        if content_id is not None and int(content_id) == int(
                            getattr(quiz_obj, "id")
                        ):
                            existing_quiz_item = module_item
                            continue
                    except Exception:
                        pass
                if item_title == quiz_title:
                    existing_quiz_item = module_item

        if attendance_header_value:
            if existing_subheader is None:
                actions.append(
                    f"add-attendance-header:{module_name}:{attendance_header_value}"
                )
                log.info("  Adding attendance header in module '%s'", module_name)
                if not dry_run and weekly_module is not None:
                    existing_subheader = weekly_module.create_module_item(
                        module_item={
                            "type": "SubHeader",
                            "title": attendance_header_value,
                            "published": True,
                        }
                    )
                    _ensure_module_item_published(
                        existing_subheader,
                        item_label=f"{module_name}:{attendance_header_value}",
                    )
            elif existing_subheader is not None:
                actions.append(
                    f"attendance-header-exists:{module_name}:{attendance_header_value}"
                )
                _ensure_module_item_published(
                    existing_subheader,
                    item_label=f"{module_name}:{attendance_header_value}",
                )

        assignment_id = getattr(quiz_obj, "assignment_id", None)
        if assignment_id is None:
            log.warning(
                "Attendance quiz '%s' missing assignment_id; skipping section overrides.",
                quiz_title,
            )
            continue

        assignment = canvas_course.course.get_assignment(int(assignment_id))
        if not dry_run:
            assignment.edit(
                assignment={
                    "published": True,
                    "only_visible_to_overrides": True,
                    "points_possible": points_possible,
                }
            )

        existing_overrides_by_section: dict[int, Any] = {}
        for override in assignment.get_overrides():
            section_value = getattr(override, "course_section_id", None)
            if section_value is None:
                continue
            try:
                section_id = int(section_value)
            except (TypeError, ValueError):
                continue
            existing_overrides_by_section[section_id] = override

        target_canvas_section_ids: set[int] = set()
        for window in windows:
            section_id = str(window["section_id"])
            canvas_section_id = int(section_id_map[section_id])
            target_canvas_section_ids.add(canvas_section_id)
            override_payload = {
                "course_section_id": canvas_section_id,
                "unlock_at": window["unlock_at"],
                "due_at": window["due_at"],
                "lock_at": window["lock_at"],
            }
            existing_override = existing_overrides_by_section.get(canvas_section_id)
            if existing_override is None:
                actions.append(
                    f"create-attendance-override:{quiz_title}:{section_id}:{window['unlock_at']}..{window['lock_at']}"
                )
                log.info(
                    "    Creating section window: %s %s -> %s",
                    section_id,
                    window["unlock_at"],
                    window["lock_at"],
                )
                if not dry_run:
                    assignment.create_override(assignment_override=override_payload)
            else:
                actions.append(
                    f"update-attendance-override:{quiz_title}:{section_id}:{window['unlock_at']}..{window['lock_at']}"
                )
                log.info(
                    "    Updating section window: %s %s -> %s",
                    section_id,
                    window["unlock_at"],
                    window["lock_at"],
                )
                if not dry_run:
                    existing_override.edit(assignment_override=override_payload)

        if prune_section_overrides:
            for canvas_section_id, existing_override in existing_overrides_by_section.items():
                if (
                    canvas_section_id in managed_canvas_section_ids
                    and canvas_section_id not in target_canvas_section_ids
                ):
                    actions.append(
                        f"delete-attendance-override:{quiz_title}:section-{canvas_section_id}"
                    )
                    log.info(
                        "    Removing stale section window: section-%s",
                        canvas_section_id,
                    )
                    if not dry_run:
                        existing_override.delete()

        if existing_quiz_item is None:
            actions.append(f"add-attendance-module-item:{module_name}:{quiz_title}")
            log.info(
                "  Adding attendance quiz module item: %s -> %s",
                module_name,
                quiz_title,
            )
            if not dry_run and weekly_module is not None:
                existing_quiz_item = weekly_module.create_module_item(
                    module_item={
                        "type": "Quiz",
                        "content_id": int(getattr(quiz_obj, "id")),
                        "title": quiz_title,
                        "indent": attendance_module_indent,
                        "published": True,
                    }
                )
        else:
            actions.append(f"attendance-module-item-exists:{module_name}:{quiz_title}")
            if not dry_run:
                existing_quiz_item.edit(
                    module_item={
                        "title": quiz_title,
                        "indent": attendance_module_indent,
                        "published": True,
                    }
                )

        if existing_quiz_item is not None:
            _ensure_module_item_published(
                existing_quiz_item,
                item_label=f"{module_name}:{quiz_title}",
            )

        existing_questions = list(quiz_obj.get_questions())
        if existing_questions:
            actions.append(f"attendance-question-exists:{quiz_title}")
            log.info("    Attendance question already exists")
        else:
            actions.append(f"add-attendance-question:{quiz_title}")
            log.info("    Adding default attendance question")
            if not dry_run:
                quiz_obj.create_question(
                    question=_default_attendance_question_payload(quiz_title=quiz_title)
                )

    return actions


def _find_assignment_by_name(canvas_course: CanvasCourse, assignment_name: str):
    for assignment in canvas_course.course.get_assignments(order_by="name"):
        if str(getattr(assignment, "name", "")).strip() == assignment_name.strip():
            return assignment
    return None


def _markdown_to_html(markdown_text: str) -> str:
    try:
        import markdown as markdown_module  # type: ignore
    except Exception:
        return _markdown_to_html_fallback(markdown_text)
    return str(
        markdown_module.markdown(
            markdown_text,
            extensions=["fenced_code", "tables", "nl2br"],
        )
    )


def _markdown_to_html_fallback(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    html_parts: list[str] = []
    paragraph_lines: list[str] = []
    list_items: list[str] = []
    in_code_block = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            html_parts.append("<p>" + "<br/>".join(paragraph_lines) + "</p>")
            paragraph_lines = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            html_parts.append("<ul>" + "".join(list_items) + "</ul>")
            list_items = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code_block:
                html_parts.append("</code></pre>")
                in_code_block = False
            else:
                html_parts.append("<pre><code>")
                in_code_block = True
            continue

        if in_code_block:
            html_parts.append(html.escape(raw_line))
            continue

        if not stripped:
            flush_paragraph()
            flush_list()
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            flush_paragraph()
            flush_list()
            level = len(heading_match.group(1))
            heading_text = html.escape(heading_match.group(2))
            html_parts.append(f"<h{level}>{heading_text}</h{level}>")
            continue

        bullet_match = re.match(r"^[-*]\s+(.*)$", stripped)
        if bullet_match:
            flush_paragraph()
            list_items.append(f"<li>{html.escape(bullet_match.group(1))}</li>")
            continue

        flush_list()
        paragraph_lines.append(html.escape(stripped))

    flush_paragraph()
    flush_list()
    if in_code_block:
        html_parts.append("</code></pre>")

    return "\n".join(html_parts)


def _row_meeting_dates(row: dict[str, Any]) -> list[date]:
    parsed_dates: list[date] = []
    for raw_value in (row.get("dates") or {}).values():
        if not raw_value:
            continue
        try:
            parsed_dates.append(_parse_date(str(raw_value)))
        except ValueError:
            continue
    return parsed_dates


def _build_instructional_slot_lookup(
    calendar_json: dict[str, Any],
) -> tuple[dict[int, dict[int, dict[str, Any]]], list[dict[str, Any]]]:
    lookup: dict[int, dict[int, dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    for row in calendar_json.get("rows", []):
        if bool(row.get("no_class")) or bool(row.get("no_class_notice")):
            continue
        week_number = row.get("week_number")
        slot_in_week = row.get("slot_in_week")
        if not isinstance(week_number, int) or not isinstance(slot_in_week, int):
            continue
        if not _row_meeting_dates(row):
            continue
        lookup.setdefault(week_number, {})[slot_in_week] = row
        rows.append(row)
    rows.sort(
        key=lambda row: (
            int(row.get("week_number") or 0),
            int(row.get("slot_in_week") or 0),
        )
    )
    return lookup, rows


def _resolve_programming_due_date(
    *,
    due_rule: dict[str, Any],
    release_date: date,
    all_rows: list[dict[str, Any]],
    slot_lookup: dict[int, dict[int, dict[str, Any]]],
) -> date | None:
    due_week = due_rule.get("week")
    due_day = due_rule.get("day")
    if due_week is not None and due_day is not None:
        week_rows = slot_lookup.get(int(due_week), {})
        target_row = week_rows.get(int(due_day))
        if target_row is None:
            return None
        target_dates = _row_meeting_dates(target_row)
        return max(target_dates) if target_dates else None

    strategy = str(due_rule.get("strategy") or "recommended").strip().lower()
    if strategy != "recommended":
        return None

    include_exam_days = bool(due_rule.get("include_exam_days", False))
    min_days_after_release = int(due_rule.get("min_days_after_release", 7))
    preferred_weekday = int(due_rule.get("preferred_weekday", WEEKDAY_TO_INT["Sun"]))
    earliest_date = release_date + timedelta(days=min_days_after_release)

    candidate_rows: list[tuple[date, dict[str, Any]]] = []
    for row in all_rows:
        if (not include_exam_days) and bool(row.get("exam_names")):
            continue
        row_dates = _row_meeting_dates(row)
        if not row_dates:
            continue
        canonical_date = max(row_dates)
        if canonical_date >= earliest_date:
            candidate_rows.append((canonical_date, row))
    candidate_rows.sort(key=lambda pair: pair[0])
    if not candidate_rows:
        return None

    for candidate_date, _ in candidate_rows:
        if candidate_date.weekday() == preferred_weekday:
            return candidate_date
    return candidate_rows[0][0]


def _build_assignment_publish_plans(
    normalized_plan: dict[str, Any],
    calendar_json: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    templates = list(normalized_plan.get("assignments") or [])
    if not templates:
        return [], warnings

    timezone_name = str(normalized_plan["term"]["timezone"])
    tzinfo = ZoneInfo(timezone_name)
    slot_lookup, all_rows = _build_instructional_slot_lookup(calendar_json)

    plans: list[dict[str, Any]] = []
    for template in templates:
        template_type = str(template.get("type") or "").strip().lower()
        template_id = str(template.get("id") or template_type)
        markdown_template = str((template.get("contents") or {}).get("markdown") or "")
        title_template = (
            str(template.get("title_template") or "").strip()
            or str(template.get("title") or "").strip()
        )
        if template_type == "weekly_study_notes":
            rules = template.get("rules") or {}
            include_exam_days = bool(rules.get("include_exam_days", True))
            include_exam_only_weeks = bool(rules.get("include_exam_only_weeks", False))
            unlock_time = _time_from_hhmm(str(rules.get("unlock_time", "00:00")))
            due_time = _time_from_hhmm(str(rules.get("due_time", "23:59")))
            lock_time = _time_from_hhmm(str(rules.get("lock_time", str(rules.get("due_time", "23:59")))))
            due_days_after = int(rules.get("due_days_after", 0))
            lock_days_after_due = int(rules.get("lock_days_after_due", 0))

            for week_number in sorted(slot_lookup.keys()):
                week_rows_all = list(slot_lookup[week_number].values())
                week_rows = [
                    row
                    for row in week_rows_all
                    if include_exam_days or not bool(row.get("exam_names"))
                ]
                if not week_rows:
                    if include_exam_only_weeks and week_rows_all:
                        week_rows = week_rows_all
                    else:
                        continue

                all_week_dates = [
                    d for row in week_rows for d in _row_meeting_dates(row)
                ]
                if not all_week_dates:
                    continue
                unlock_date = min(all_week_dates)
                due_date = max(all_week_dates) + timedelta(days=due_days_after)
                lock_date = due_date + timedelta(days=lock_days_after_due)

                context = {"week_number": week_number}
                title = (
                    title_template.format(**context)
                    if title_template
                    else f"Weekly Study Notes Week {week_number:02d}"
                )
                try:
                    markdown_body = markdown_template.format(**context)
                except Exception:
                    markdown_body = markdown_template
                plans.append(
                    {
                        "template_id": template_id,
                        "instance_id": f"{template_id}-week-{week_number:02d}",
                        "type": template_type,
                        "autograder_type": "text",
                        "autograder_repo_path": None,
                        "title": title,
                        "description_html": _markdown_to_html(markdown_body),
                        "assignment_group_name": template["assignment_group_name"],
                        "points_possible": float(template["points_possible"]),
                        "published": bool(template["published"]),
                        "submission_types": list(template["submission_types"]),
                        "unlock_at": datetime.combine(
                            unlock_date, unlock_time, tzinfo=tzinfo
                        ).isoformat(),
                        "due_at": datetime.combine(
                            due_date, due_time, tzinfo=tzinfo
                        ).isoformat(),
                        "lock_at": datetime.combine(
                            lock_date, lock_time, tzinfo=tzinfo
                        ).isoformat(),
                        "week_number": week_number,
                    }
                )
            continue

        if template_type == "programming_assignment":
            instances = list((template.get("rules") or {}).get("instances") or [])
            if not instances:
                warnings.append(
                    f"Programming assignment template '{template_id}' has no rules.instances."
                )
                continue

            for instance_idx, instance in enumerate(instances, start=1):
                release_rule = instance.get("release") or {}
                release_week = int(release_rule.get("week", 0))
                release_day = int(release_rule.get("day", 2))
                release_time = _time_from_hhmm(str(release_rule.get("time", "00:00")))
                release_row = slot_lookup.get(release_week, {}).get(release_day)
                if release_row is None:
                    warnings.append(
                        f"Programming assignment '{template_id}' instance {instance_idx} release slot "
                        f"(week={release_week}, day={release_day}) was not found."
                    )
                    continue
                release_dates = _row_meeting_dates(release_row)
                if not release_dates:
                    continue
                release_date = max(release_dates)

                due_rule = instance.get("due") or {}
                due_date = _resolve_programming_due_date(
                    due_rule=due_rule,
                    release_date=release_date,
                    all_rows=all_rows,
                    slot_lookup=slot_lookup,
                )
                if due_date is None:
                    warnings.append(
                        f"Programming assignment '{template_id}' instance {instance_idx} due date could not be resolved."
                    )
                    continue
                due_time = _time_from_hhmm(str(due_rule.get("time", "23:59")))

                context = {
                    "week_number": release_week,
                    "day": release_day,
                    "instance_index": instance_idx,
                }
                instance_title = str(instance.get("title") or "").strip()
                if instance_title:
                    title = instance_title
                elif title_template:
                    try:
                        title = title_template.format(**context)
                    except Exception:
                        title = title_template
                else:
                    title = f"Programming Assignment {instance_idx}"
                try:
                    markdown_body = markdown_template.format(**context)
                except Exception:
                    markdown_body = markdown_template

                plans.append(
                    {
                        "template_id": template_id,
                        "instance_id": str(instance.get("id") or f"{template_id}-{instance_idx}"),
                        "type": template_type,
                        "autograder_type": "programming",
                        "autograder_repo_path": str(
                            instance.get("repo_path")
                            or instance.get("id")
                            or f"{template_id}-{instance_idx}"
                        ),
                        "title": title,
                        "description_html": _markdown_to_html(markdown_body),
                        "assignment_group_name": template["assignment_group_name"],
                        "points_possible": float(template["points_possible"]),
                        "published": bool(template["published"]),
                        "submission_types": list(template["submission_types"]),
                        "unlock_at": datetime.combine(
                            release_date, release_time, tzinfo=tzinfo
                        ).isoformat(),
                        "due_at": datetime.combine(
                            due_date, due_time, tzinfo=tzinfo
                        ).isoformat(),
                        "lock_at": None,
                        "week_number": release_week,
                    }
                )
            continue

        warnings.append(
            f"Unsupported assignment template type '{template_type}' for '{template_id}'."
        )

    plans.sort(
        key=lambda plan: (
            str(plan.get("unlock_at") or ""),
            str(plan.get("title") or ""),
        )
    )
    return plans, warnings


def _publish_generated_assignments(
    canvas_course: CanvasCourse,
    *,
    assignment_plans: list[dict[str, Any]],
    dry_run: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    actions: list[str] = []
    exported_assignments: list[dict[str, Any]] = []
    if not assignment_plans:
        return actions, exported_assignments

    assignment_group_cache: dict[str, Any] = {}

    def get_group(group_name: str):
        if group_name in assignment_group_cache:
            return assignment_group_cache[group_name]
        if dry_run:
            assignment_group_cache[group_name] = None
            return None
        group = canvas_course.create_assignment_group(
            name=group_name,
            delete_existing=False,
        )
        assignment_group_cache[group_name] = group
        return group

    total_assignments = len(assignment_plans)
    log.info("Publishing generated assignments: %d total", total_assignments)
    for idx, plan in enumerate(assignment_plans, start=1):
        title = str(plan["title"])
        assignment_group_name = str(plan["assignment_group_name"])
        payload: dict[str, Any] = {
            "name": title,
            "description": str(plan["description_html"]),
            "points_possible": float(plan["points_possible"]),
            "published": bool(plan["published"]),
            "submission_types": list(plan["submission_types"]),
            "unlock_at": plan.get("unlock_at"),
            "due_at": plan.get("due_at"),
        }
        if plan.get("lock_at"):
            payload["lock_at"] = plan["lock_at"]

        assignment_group = get_group(assignment_group_name)
        if assignment_group is not None:
            payload["assignment_group_id"] = int(getattr(assignment_group, "id"))

        existing_assignment = _find_assignment_by_name(canvas_course, title)
        log.info(
            "Assignment %d/%d | %s | group=%s",
            idx,
            total_assignments,
            title,
            assignment_group_name,
        )
        if existing_assignment is None:
            actions.append(f"create-assignment:{title}")
            if not dry_run:
                created_assignment = canvas_course.course.create_assignment(
                    assignment=payload
                )
                exported_assignments.append(
                    {
                        "assignment_id": int(getattr(created_assignment, "id")),
                        "title": title,
                        "assignment_group_name": assignment_group_name,
                        "autograder_type": str(plan.get("autograder_type") or "text"),
                        "autograder_repo_path": plan.get("autograder_repo_path"),
                    }
                )
            else:
                exported_assignments.append(
                    {
                        "assignment_id": None,
                        "title": title,
                        "assignment_group_name": assignment_group_name,
                        "autograder_type": str(plan.get("autograder_type") or "text"),
                        "autograder_repo_path": plan.get("autograder_repo_path"),
                    }
                )
            continue

        actions.append(f"update-assignment:{title}")
        if not dry_run:
            existing_assignment.edit(assignment=payload)
            exported_assignments.append(
                {
                    "assignment_id": int(getattr(existing_assignment, "id")),
                    "title": title,
                    "assignment_group_name": assignment_group_name,
                    "autograder_type": str(plan.get("autograder_type") or "text"),
                    "autograder_repo_path": plan.get("autograder_repo_path"),
                }
            )
        else:
            exported_assignments.append(
                {
                    "assignment_id": None,
                    "title": title,
                    "assignment_group_name": assignment_group_name,
                    "autograder_type": str(plan.get("autograder_type") or "text"),
                    "autograder_repo_path": plan.get("autograder_repo_path"),
                }
            )
    return actions, exported_assignments


def _build_autograder_assignment_snippet(
    *,
    course_id: int | None,
    exported_assignments: list[dict[str, Any]],
) -> dict[str, Any]:
    groups_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in exported_assignments:
        group_name = str(entry.get("assignment_group_name") or "Assignments")
        group_type = str(entry.get("autograder_type") or "text")
        key = (group_name, group_type)
        if key not in groups_by_key:
            groups_by_key[key] = {
                "name": group_name,
                "type": group_type,
                "assignments": [],
            }

        assignment_id = entry.get("assignment_id")
        repo_path = entry.get("autograder_repo_path")
        if group_type == "programming":
            groups_by_key[key]["assignments"].append(
                {
                    "id": assignment_id,
                    "repo_path": str(repo_path or ""),
                }
            )
        else:
            groups_by_key[key]["assignments"].append(assignment_id)

    assignment_groups = [
        groups_by_key[key]
        for key in sorted(groups_by_key.keys(), key=lambda item: (item[0], item[1]))
    ]
    return {
        "course_id": course_id,
        "assignment_groups": assignment_groups,
    }


def publish_calendar_to_canvas(
    canvas_course: CanvasCourse,
    *,
    normalized_plan: dict[str, Any],
    calendar_json: dict[str, Any],
    assignment_plans: list[dict[str, Any]],
    page_title: str,
    module_name: str | None,
    publish_weekly_slides: bool,
    publish_attendance_quizzes: bool,
    publish_assignments: bool,
    weekly_module_template: str,
    weekly_slides_title_prefix: str,
    weekly_slides_indent: int,
    weekly_slides_section_header: str | None,
    weekly_slides_prune_existing: bool,
    lecture_slides_base_url: str | None,
    html_body: str,
    dry_run: bool,
) -> dict[str, Any]:
    actions: list[str] = []
    exported_assignments: list[dict[str, Any]] = []
    page_url = _slugify(page_title)
    normalized_title_prefix = str(weekly_slides_title_prefix or "")
    normalized_weekly_indent = max(0, int(weekly_slides_indent))
    normalized_lecture_base = (
        _canonicalize_http_url(lecture_slides_base_url)
        if lecture_slides_base_url
        else None
    )
    section_header_value = (
        str(weekly_slides_section_header).strip()
        if weekly_slides_section_header is not None
        else ""
    )

    def _ensure_module_published(module_obj: Any, *, module_label: str) -> None:
        if bool(getattr(module_obj, "published", False)):
            return
        actions.append(f"publish-module:{module_label}")
        log.info("Publishing module '%s'", module_label)
        if not dry_run:
            module_obj.edit(module={"published": True})

    def _ensure_module_item_published(item_obj: Any, *, item_label: str) -> None:
        if bool(getattr(item_obj, "published", False)):
            return
        actions.append(f"publish-module-item:{item_label}")
        log.info("Publishing module item '%s'", item_label)
        if not dry_run:
            item_obj.edit(module_item={"published": True})

    existing_page = _find_page_by_title(canvas_course, page_title)
    if existing_page is not None:
        actions.append(f"update-page:{page_title}")
        page_url = getattr(existing_page, "url", page_url) or page_url
        if not dry_run:
            existing_page.edit(
                wiki_page={
                    "title": page_title,
                    "body": html_body,
                    "published": True,
                }
            )
    else:
        actions.append(f"create-page:{page_title}")
        if not dry_run:
            created_page = canvas_course.course.create_page(
                wiki_page={
                    "title": page_title,
                    "body": html_body,
                    "published": True,
                }
            )
            page_url = getattr(created_page, "url", page_url) or page_url

    if module_name:
        module = _find_module_by_name(canvas_course, module_name)
        if module is None:
            actions.append(f"create-module:{module_name}")
            if not dry_run:
                module = canvas_course.course.create_module(
                    module={
                        "name": module_name,
                        "published": True,
                    }
                )
        else:
            actions.append(f"reuse-module:{module_name}")

        if module is not None:
            _ensure_module_published(module, module_label=module_name)
            page_item = None
            for item in module.get_module_items():
                item_type = str(getattr(item, "type", "")).lower()
                item_title = str(getattr(item, "title", ""))
                item_page_url = str(getattr(item, "page_url", ""))
                if item_type == "page" and (
                    item_page_url == page_url
                    or item_title.strip() == page_title.strip()
                ):
                    page_item = item
                    break
            if page_item is None:
                actions.append(f"link-page-in-module:{module_name}:{page_title}")
                if not dry_run:
                    page_item = module.create_module_item(
                        module_item={
                            "type": "Page",
                            "title": page_title,
                            "page_url": page_url,
                            "published": True,
                        }
                    )
                    _ensure_module_item_published(
                        page_item,
                        item_label=f"{module_name}:{page_title}",
                    )
            else:
                actions.append(f"module-link-exists:{module_name}:{page_title}")
                _ensure_module_item_published(
                    page_item,
                    item_label=f"{module_name}:{page_title}",
                )

    if publish_weekly_slides:
        weekly_modules = _build_weekly_slide_items(
            calendar_json,
            module_name_template=weekly_module_template,
        )
        total_modules = len(weekly_modules)
        for module_idx, (weekly_module_name, links) in enumerate(
            weekly_modules.items(), start=1
        ):
            log.info(
                "Weekly slides module %d/%d: %s (%d links)",
                module_idx,
                total_modules,
                weekly_module_name,
                len(links),
            )
            weekly_module = _find_module_by_name(canvas_course, weekly_module_name)
            if weekly_module is None:
                actions.append(f"create-module:{weekly_module_name}")
                log.info("Creating module '%s'", weekly_module_name)
                if not dry_run:
                    weekly_module = canvas_course.course.create_module(
                        module={
                            "name": weekly_module_name,
                            "published": True,
                        }
                    )
            else:
                actions.append(f"reuse-module:{weekly_module_name}")
                log.info("Reusing module '%s'", weekly_module_name)

            if weekly_module is not None:
                _ensure_module_published(weekly_module, module_label=weekly_module_name)

            desired_links: list[dict[str, str]] = []
            desired_urls: set[str] = set()
            for link in links:
                raw_title = str(link.get("title") or "").strip()
                raw_url = _canonicalize_http_url(str(link.get("url") or "").strip())
                if not raw_url:
                    continue
                desired_title = (
                    f"{normalized_title_prefix}{raw_title}"
                    if normalized_title_prefix
                    else raw_title
                )
                desired_links.append(
                    {
                        "title": desired_title,
                        "url": raw_url,
                    }
                )
                desired_urls.add(raw_url)

            existing_external_items_by_url: dict[str, list[Any]] = {}
            managed_external_items: list[tuple[Any, str]] = []
            existing_subheaders: list[Any] = []
            if weekly_module is not None:
                for item in weekly_module.get_module_items():
                    item_type = str(getattr(item, "type", "")).lower().replace("_", "")
                    item_title = str(getattr(item, "title", "")).strip()
                    if item_type == "subheader" and section_header_value:
                        if item_title == section_header_value:
                            existing_subheaders.append(item)
                        continue
                    if item_type != "externalurl":
                        continue
                    external_url = _canonicalize_http_url(
                        str(getattr(item, "external_url", "")).strip()
                    )
                    if external_url:
                        existing_external_items_by_url.setdefault(external_url, []).append(
                            item
                        )
                        is_managed = False
                        if normalized_title_prefix and item_title.startswith(
                            normalized_title_prefix
                        ):
                            is_managed = True
                        if (
                            normalized_lecture_base
                            and external_url.startswith(
                                normalized_lecture_base.rstrip("/") + "/"
                            )
                        ):
                            is_managed = True
                        if is_managed:
                            managed_external_items.append((item, external_url))

            if section_header_value:
                if existing_subheaders:
                    actions.append(f"slides-header-exists:{weekly_module_name}")
                    for subheader_item in existing_subheaders:
                        _ensure_module_item_published(
                            subheader_item,
                            item_label=f"{weekly_module_name}:{section_header_value}",
                        )
                else:
                    actions.append(
                        f"add-slides-header:{weekly_module_name}:{section_header_value}"
                    )
                    log.info(
                        "Adding section header '%s' in module '%s'",
                        section_header_value,
                        weekly_module_name,
                    )
                    if not dry_run and weekly_module is not None:
                        created_subheader = weekly_module.create_module_item(
                            module_item={
                                "type": "SubHeader",
                                "title": section_header_value,
                                "published": True,
                            }
                        )
                        _ensure_module_item_published(
                            created_subheader,
                            item_label=f"{weekly_module_name}:{section_header_value}",
                        )

            if weekly_slides_prune_existing:
                stale_items = [
                    (item, url_value)
                    for item, url_value in managed_external_items
                    if url_value not in desired_urls
                ]
                for stale_item, stale_url in stale_items:
                    stale_title = str(getattr(stale_item, "title", "")).strip()
                    actions.append(
                        f"remove-weekly-link:{weekly_module_name}:{stale_title}:{stale_url}"
                    )
                    log.info(
                        "Removing stale weekly link in '%s': %s (%s)",
                        weekly_module_name,
                        stale_title,
                        stale_url,
                    )
                    if not dry_run:
                        stale_item.delete()

            total_links = len(desired_links)
            for link_idx, link in enumerate(desired_links, start=1):
                title = link["title"]
                url = link["url"]
                existing_for_url = existing_external_items_by_url.get(url, [])
                has_matching = False
                for existing_item in existing_for_url:
                    existing_title = str(getattr(existing_item, "title", "")).strip()
                    existing_indent = int(getattr(existing_item, "indent", 0) or 0)
                    if (
                        existing_title == title
                        and existing_indent == normalized_weekly_indent
                    ):
                        has_matching = True
                        break

                if has_matching:
                    for existing_item in existing_for_url:
                        existing_title = str(getattr(existing_item, "title", "")).strip()
                        existing_indent = int(getattr(existing_item, "indent", 0) or 0)
                        if (
                            existing_title == title
                            and existing_indent == normalized_weekly_indent
                        ):
                            _ensure_module_item_published(
                                existing_item,
                                item_label=f"{weekly_module_name}:{existing_title}",
                            )
                            break
                    if weekly_slides_prune_existing:
                        for existing_item in existing_for_url:
                            existing_title = str(
                                getattr(existing_item, "title", "")
                            ).strip()
                            existing_indent = int(
                                getattr(existing_item, "indent", 0) or 0
                            )
                            if (
                                existing_title == title
                                and existing_indent == normalized_weekly_indent
                            ):
                                continue
                            actions.append(
                                f"remove-duplicate-weekly-link:{weekly_module_name}:{existing_title}:{url}"
                            )
                            log.info(
                                "Removing duplicate weekly link in '%s': %s (%s)",
                                weekly_module_name,
                                existing_title,
                                url,
                            )
                            if not dry_run:
                                existing_item.delete()
                    actions.append(
                        f"weekly-link-exists:{weekly_module_name}:{title}:{url}"
                    )
                    log.info(
                        "Week %s link %d/%d already present: %s",
                        weekly_module_name,
                        link_idx,
                        total_links,
                        title,
                    )
                    continue

                if weekly_slides_prune_existing:
                    for existing_item in existing_for_url:
                        existing_title = str(getattr(existing_item, "title", "")).strip()
                        actions.append(
                            f"replace-weekly-link:{weekly_module_name}:{existing_title}:{url}"
                        )
                        log.info(
                            "Replacing weekly link in '%s': %s (%s)",
                            weekly_module_name,
                            existing_title,
                            url,
                        )
                        if not dry_run:
                            existing_item.delete()

                actions.append(f"add-weekly-link:{weekly_module_name}:{title}:{url}")
                log.info(
                    "Week %s link %d/%d add: %s",
                    weekly_module_name,
                    link_idx,
                    total_links,
                    title,
                )
                if not dry_run and weekly_module is not None:
                    created_item = weekly_module.create_module_item(
                        module_item={
                            "type": "ExternalUrl",
                            "title": title,
                            "external_url": url,
                            "indent": normalized_weekly_indent,
                            "new_tab": True,
                            "published": True,
                        }
                    )
                    _ensure_module_item_published(
                        created_item,
                        item_label=f"{weekly_module_name}:{title}",
                    )

    if publish_attendance_quizzes:
        attendance_actions = _publish_attendance_quizzes(
            canvas_course,
            normalized_plan=normalized_plan,
            calendar_json=calendar_json,
            weekly_module_template=weekly_module_template,
            dry_run=dry_run,
        )
        actions.extend(attendance_actions)

    if publish_assignments:
        assignment_actions, exported_assignments = _publish_generated_assignments(
            canvas_course,
            assignment_plans=assignment_plans,
            dry_run=dry_run,
        )
        actions.extend(assignment_actions)

    return {
        "dry_run": dry_run,
        "actions": actions,
        "page_title": page_title,
        "page_url": page_url,
        "module_name": module_name,
        "publish_weekly_slides": publish_weekly_slides,
        "publish_attendance_quizzes": publish_attendance_quizzes,
        "publish_assignments": publish_assignments,
        "exported_assignments": exported_assignments,
        "weekly_module_template": weekly_module_template,
    }


def build_course_calendar(
    *,
    plan_path: str | Path,
    output_dir: str | Path,
    publish: bool = False,
    publish_weekly_slides: bool = False,
    publish_attendance_quizzes: bool = False,
    publish_assignments: bool = False,
    dry_run: bool = True,
    canvas_course: CanvasCourse | None = None,
    page_title_override: str | None = None,
    module_name: str | None = None,
    weekly_module_template_override: str | None = None,
) -> CalendarBuildResult:
    raw = load_course_plan(plan_path)
    normalized = normalize_course_plan(raw)
    calendar_json, warnings = build_schedule(normalized)
    assignment_plans, assignment_warnings = _build_assignment_publish_plans(
        normalized, calendar_json
    )
    warnings.extend(assignment_warnings)
    calendar_html = render_calendar_html(normalized, calendar_json)

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    html_path = output_root / "calendar.html"
    json_path = output_root / "calendar.json"
    normalized_path = output_root / "normalized_plan.yaml"
    assignment_plan_path = output_root / "assignment_plan.json"
    autograder_snippet_path = output_root / "autograder_assignment_snippet.yaml"

    yaml = _load_yaml_module()
    html_path.write_text(calendar_html, encoding="utf-8")
    json_path.write_text(json.dumps(calendar_json, indent=2) + "\n", encoding="utf-8")
    assignment_plan_path.write_text(
        json.dumps({"assignments": assignment_plans}, indent=2) + "\n",
        encoding="utf-8",
    )
    normalized_path.write_text(
        yaml.safe_dump(
            {
                **normalized,
                "term": {
                    **normalized["term"],
                    "start_date": normalized["term"]["start_date"].isoformat(),
                    "end_date": normalized["term"]["end_date"].isoformat(),
                    "global_no_class_dates": [
                        d.isoformat()
                        for d in sorted(normalized["term"]["global_no_class_dates"])
                    ],
                    "breaks": [
                        {
                            **break_period,
                            "start_date": break_period["start_date"].isoformat(),
                            "end_date": break_period["end_date"].isoformat(),
                            "applies_to": (
                                sorted(list(break_period["applies_to"]))
                                if break_period["applies_to"]
                                else None
                            ),
                        }
                        for break_period in normalized["term"]["breaks"]
                    ],
                },
            },
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )

    publish_result = None
    if publish:
        if canvas_course is None:
            raise ValueError("canvas_course is required when publish=True.")
        page_title = (
            page_title_override or normalized["publishing"]["schedule_page_title"]
        )
        module_name_value = module_name or "Course Schedule"
        weekly_module_template = (
            weekly_module_template_override
            or normalized["publishing"]["module_name_template"]
        )
        publish_result = publish_calendar_to_canvas(
            canvas_course,
            normalized_plan=normalized,
            calendar_json=calendar_json,
            assignment_plans=assignment_plans,
            page_title=page_title,
            module_name=module_name_value,
            publish_weekly_slides=publish_weekly_slides,
            publish_attendance_quizzes=publish_attendance_quizzes,
            publish_assignments=publish_assignments,
            weekly_module_template=weekly_module_template,
            weekly_slides_title_prefix=normalized["publishing"][
                "weekly_slides_title_prefix"
            ],
            weekly_slides_indent=normalized["publishing"]["weekly_slides_indent"],
            weekly_slides_section_header=normalized["publishing"][
                "weekly_slides_section_header"
            ],
            weekly_slides_prune_existing=normalized["publishing"][
                "weekly_slides_prune_existing"
            ],
            lecture_slides_base_url=normalized["resource_defaults"][
                "lecture_slides_base_url"
            ],
            html_body=calendar_html,
            dry_run=dry_run,
        )

    if publish_result is not None and publish_result.get("exported_assignments") is not None:
        snippet_assignments = list(publish_result.get("exported_assignments") or [])
    else:
        snippet_assignments = [
            {
                "assignment_id": None,
                "title": str(plan.get("title") or ""),
                "assignment_group_name": str(
                    plan.get("assignment_group_name") or "Assignments"
                ),
                "autograder_type": str(plan.get("autograder_type") or "text"),
                "autograder_repo_path": plan.get("autograder_repo_path"),
            }
            for plan in assignment_plans
        ]
    snippet_course_id = (
        int(getattr(canvas_course.course, "id"))
        if canvas_course is not None and hasattr(canvas_course, "course")
        else None
    )
    autograder_snippet = _build_autograder_assignment_snippet(
        course_id=snippet_course_id,
        exported_assignments=snippet_assignments,
    )
    autograder_snippet_path.write_text(
        yaml.safe_dump(autograder_snippet, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )

    return CalendarBuildResult(
        normalized_plan=normalized,
        calendar_json=calendar_json,
        calendar_html=calendar_html,
        warnings=warnings,
        output_paths={
            "calendar_html": html_path,
            "calendar_json": json_path,
            "normalized_plan": normalized_path,
            "assignment_plan": assignment_plan_path,
            "autograder_assignment_snippet": autograder_snippet_path,
        },
        publish_result=publish_result,
    )
