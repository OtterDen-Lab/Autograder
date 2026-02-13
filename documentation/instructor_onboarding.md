# Instructor Onboarding

This guide is the fastest path to a safe first run with the supported grader flows.

## 1) Minimal Working Config

Start from `example_files/workhorse.yaml` and reduce to one course + one assignment first.

Minimal programming + text config:

```yaml
prod: false
push: false
privacy_mode: id_only

assignment_types:
  programming:
    kind: ProgrammingAssignment
    grader: template-grader
    settings:
      record_retention: true
      records_dir: "~/autograder-records/course-1234"
      source_repo: "https://github.com/your-org/your-template-repo"

  text:
    kind: TextAssignment
    grader: TextSubmissionGrader
    settings:
      grade_after_lock_date: true
      prefer_anthropic: false
      phase1_tier: medium
      phase2_tier: medium
      phase25_tier: medium

courses:
  - id: 12345
    assignment_groups:
      - type: programming
        settings:
          base_image_name: "python:3.11-slim"
          student_code_path: "src"
        assignments:
          - id: 67890
            repo_path: "PA1"
      - type: text
        assignments:
          - id: 67891
```

## 2) First Safe Run

Run in non-push mode first:

```bash
grade-assignments --yaml your_config.yaml --limit 5 --max_workers 1
```

Then run a real push:

```bash
grade-assignments --yaml your_config.yaml --regrade
```

Optional idempotent rerun protection:

```bash
grade-assignments --yaml your_config.yaml --idempotency-key your-run-key
```

## 3) Common Customizations

- Add Docker packages:
  - Use `extra_dockerfile_lines` under programming settings.
- Map uploaded filenames to target locations:
  - Use `file_paths` regex mapping under programming settings.
- Tune text-grading cost/performance:
  - Adjust `phase1_tier`, `phase2_tier`, `phase25_tier` (`small|medium|large`).
- Control privacy in logs:
  - `privacy_mode: none | id_only | blind`
  - `--reveal-identity` requires `AUTOGRADER_BREAK_GLASS=1`.
- Keep assignment IDs unique per course/group:
  - Duplicate non-disabled `assignments[].id` entries in the same group now fail fast at config parse time.

## 4) Failure Recovery

For partial failures, push failures, and rerun/autopsy workflow, use:

- `documentation/operations_runbook.md`

That runbook is the canonical recovery procedure for production runs.
