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

## 4) Text Grader Tuning

Use these settings for text grading:

- `prefer_anthropic`: provider preference
- `phase1_tier`, `phase2_tier`, `phase25_tier`: `small|medium|large`
- `grade_after_lock_date`: delay grading until due/lock threshold

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

### Recipe: Rubric/Prompt Changes For Text Grading

- Subclass `BaseTextSubmissionGrader`.
- Override prompt builder hooks:
  - `_build_aggregate_analysis_prompt(...)`
  - `_build_individual_grading_prompt(...)`
  - `_build_question_consolidation_prompt(...)`
- Keep provider orchestration/shared logic intact.

## 5) Validation And Safe Rollout

1. `--dry-run`
2. `--dump-config`
3. single-assignment/small-limit run
4. full run with `--idempotency-key`

Use this sequence for any major customization change.
