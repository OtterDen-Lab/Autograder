# Configuration Schema Reference

This document describes the supported YAML schema consumed by `grade-assignments`.

## Top-Level Keys

- `prod` (`bool`, default `false`): use production Canvas credentials.
- `push` (`bool`, default `false`): push feedback/grades to Canvas.
- `privacy_mode` (`none|id_only|blind`, default `id_only`).
- `reveal_identity` (`bool`, default `false`): requires break-glass runtime flag.
- `idempotency_key` (`string|null`): skip re-pushing already-pushed users.
- `idempotency_state_dir` (`string`, default `~/.autograder/idempotency`).
- `assignment_types` (`mapping`, required): named assignment-type templates.
- `courses` (`list`, required): per-course assignment targeting.

## assignment_types

Each entry:

- `kind` (required): currently `ProgrammingAssignment` or `TextAssignment`
- `grader` (required): must be compatible with `kind`
- `settings` (optional mapping): grader-specific settings

### template-grader settings

- `base_image_name` (`string`, default `python:3.11-slim`)
- `source_repo` (`string`, default template repo URL)
- `additional_repos` (`list`)
  - `source_repo` (`string`, required)
  - `container_path` (`string`, required, must be under `/repo`, not `/repo`)
  - `depth` (`int|null`, default `1`, must be `>=1` when present)
- `container_repo_path` (`string`, default `/repo/programming-assignments`, must be under `/repo`)
- `student_code_path` (`string`, default `""`)
- `extra_installs` (`list[string]`)
- `extra_dockerfile_lines` (`list[string]`)
- `file_paths` (`mapping`)
  - key: regex pattern string
  - value:
    - `path` (`string`, default `""`)
    - `name` (`string|null`)
- `golden_repo` (`string|null`)
- `files_from_golden` (`list[string]`)
- `record_retention` (`bool`, default `false`)
- `records_dir` (`string|null`)
- `report_errors` (`bool`, default `true`)
- `upload_error_artifacts` (`bool`, default `false`)
- `slack_webhook` (`string|null`)
- `slack_token` (`string|null`)
- `slack_channel` (`string|null`)
- `grading_script` (`string|null`, default template script under `/repo/scripts/grader.py`)
- `grading_args` (`list[string]`, appended to default `grader.py` invocation; ignored when `grading_script` is explicitly set)
- `grading_workdir` (`string|null`, default `/repo`)
- `num_repeats` (`int|null`)

### TextSubmissionGrader / WeeklyStudyNotesGrader settings

- `grade_after_lock_date` (`bool`, default `false`)
- `prefer_anthropic` (`bool`, default `false`)
- `phase1_tier` (`small|medium|large`, default `small`)
- `phase2_tier` (`small|medium|large`, default `small`)
- `phase25_tier` (`small|medium|large`, default `small`)
- `rate_limit_retries` (`int >= 0`, default `0`)
- `records_dir` (`string|null`)
- `record_retention` (`bool`, default `false`)
- `report_errors` (`bool`, default `true`)
- `slack_webhook` (`string|null`)
- `slack_token` (`string|null`)
- `slack_channel` (`string|null`)
- `prompts` (`mapping`, optional) with keys:
  - `aggregate_analysis` (`string`)
  - `individual_grading` (`string`)
  - `question_consolidation` (`string`)
- `rubric` (`mapping`, optional) with keys:
  - `engagement` (`mapping`): `points` (`int >= 0`), `description` (`string|null`)
  - `length` (`mapping`): `points` (`int >= 0`), `description` (`string|null`)
  - `relevance` (`mapping`): `points` (`int >= 0`), `description` (`string|null`)
  - `explanation_quality` (`mapping`): `points` (`int >= 0`), `description` (`string|null`)
  - `word_threshold` (`int >= 1`)

## courses

Each entry:

- `id` (`int`, required)
- `name` (`string|null`)
- `slack_channel` (`string|null`)
- additional keys merge into per-course settings
- `assignment_groups` (`list`, required)

## assignment_groups

Each entry:

- `type` (`string`, required): key into `assignment_types`
- `name` (`string|null`)
- additional keys merge into group settings
- `assignments` (`list`, required)

## assignments

Each entry can be:

- shorthand integer/string assignment id, or
- mapping with:
  - `id` (`int`, required)
  - `repo_path` (`string|null`)
  - `assignment_name` (`string|null`)
  - `disabled` (`bool`, default `false`)
  - `settings` (`mapping`)
  - any extra keys merge into assignment settings

## Validation Rules (Important)

- unknown `assignment_types.<name>.kind` fails config parse
- grader/kind compatibility is enforced
- duplicate non-disabled assignment IDs within the same course/group fail
- unsupported grader settings fail with explicit key names
- `container_repo_path` and `additional_repos[].container_path` must stay under `/repo`

## Privacy Runtime Paths

These are runtime environment controls (not YAML fields):

- `AUTOGRADER_BLIND_ID_MAP_PATH`: persistent blind-mode ID mapping file
  - default: `~/.autograder/privacy/blind_id_map.json`
- `AUTOGRADER_REVEAL_AUDIT_LOG`: break-glass reveal audit log path
  - default: `~/.autograder/privacy/reveal_identity_audit.log`

## Example

See:

- `example_files/minimal-programming.yaml`
- `example_files/minimal-text.yaml`
- `example_files/workhorse.yaml`
