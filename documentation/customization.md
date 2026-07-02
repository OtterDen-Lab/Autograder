# Customization Guide

This guide shows where to make changes when adapting Autograder for a new course.

## 1) Add A New Grader

1. Implement grader class under `Autograder/graders/`.
2. Register it with `@GraderRegistry.register("your-grader-name")`.
3. Declare compatibility with `COMPATIBLE_KINDS`.
4. Add settings normalization in `Autograder/config_models.py` if needed.
5. Add tests:
   - unit tests for grader behavior
   - config validation tests

## 2) Add A New Assignment Kind

1. Implement an `Assignment` subclass in `Autograder/assignment.py` (or sibling module).
2. Register with `@AssignmentRegistry.register("YourAssignmentKind")`.
3. Ensure `prepare()` and `finalize()` behavior is clear.
4. Add one grader with matching `COMPATIBLE_KINDS`.
5. Add tests for assignment lifecycle and integration with `grade_assignments`.

## 3) Configure Template Grader Layout

Use these settings under `assignment_types.<type>.settings`:

- `source_repo`: main repository mounted at `/repo`
- `container_repo_path`: assignment root under `/repo/...`
- `additional_repos`: optional extra repositories mounted into additional paths
- `student_code_path`: where to copy student files under assignment root
- `file_paths`: regex-to-target path/name mapping for uploaded files
- `grading_script`: optional override for grader entrypoint command
- `grading_workdir`: optional override for grader working directory
- `upload_error_artifacts`: opt-in upload of stdout/stderr/student code attachments to Slack on grading errors

## 4) Text Grader Tuning

Use these settings for text grading:

- `prefer_anthropic`: provider preference
- `phase1_tier`, `phase2_tier`, `phase25_tier`: `small|medium|large`
- `grade_after_lock_date`: delay grading until due/lock threshold
- `rate_limit_retries`: provider retry count on 429/rate-limit responses (default `0`, fail-fast)
- `prompts`: optional template overrides for:
  - `aggregate_analysis`
  - `individual_grading`
  - `question_consolidation`
- `rubric`: optional rubric tuning:
  - points/description for `engagement`, `length`, `relevance`, `explanation_quality`
  - `word_threshold`

## 5) Assignment Type Scheduling

Add `schedule` under `assignment_types.<type>` to control how often a type is
eligible to run. The common pattern is to keep cron as the wake-up mechanism and
let Autograder skip types that are not due yet.

```yaml
assignment_types:
  programming:
    kind: ProgrammingAssignment
    grader: template-grader
    schedule:
      timezone: America/New_York
      rrule: "FREQ=DAILY;BYHOUR=0,12;BYMINUTE=0;BYSECOND=0"
```

If `timezone` is omitted, Autograder defaults it to `America/Los_Angeles`.

State is persisted in `LOG_DIR/schedule_state.yaml` and updated atomically when
an assignment type finishes successfully. The file records `last_completed_at`
per assignment type.

## Common Recipes

### Recipe: Multi-file Student Submissions

Use `file_paths` mapping to place files by pattern:

```yaml
file_paths:
  "^.*\\.c$":
    path: "src"
  "^Makefile$":
    path: "."
    name: "Makefile"
```

### Recipe: Extra Docker Dependencies

```yaml
extra_dockerfile_lines:
  - "RUN apt-get update && apt-get install -y valgrind"
  - "RUN pip install pytest"
```

### Recipe: Additional Shared Test Repository

```yaml
additional_repos:
  - source_repo: "https://github.com/your-org/shared-tests"
    container_path: "/repo/shared-tests"
    depth: 1
```

### Recipe: YAML Prompt/Rubric Changes For Text Grading

```yaml
assignment_types:
  notes:
    kind: TextAssignment
    grader: TextSubmissionGrader
    settings:
      prompts:
        aggregate_analysis: "Analyze {num_submissions} submissions for {course_name}."
      rubric:
        engagement:
          points: 5
          description: "Depth of engagement with key concepts"
        word_threshold: 300
```

### Recipe: Code-Level Prompt/Rubric Changes (Advanced)

- Subclass `BaseTextSubmissionGrader`.
- Override prompt builder hooks:
  - `_build_aggregate_analysis_prompt(...)`
  - `_build_individual_grading_prompt(...)`
  - `_build_question_consolidation_prompt(...)`

## 6) Validation And Safe Rollout

1. `--dry-run`
2. `--dump-config`
3. single-assignment/small-limit run
4. full run with `--idempotency-key`

Use this sequence for any major customization change.
