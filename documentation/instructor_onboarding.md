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
          container_repo_path: "/repo/programming-assignments"
          student_code_path: "src"
        assignments:
          - id: 67890
            repo_path: "PA1"
      - type: text
        assignments:
          - id: 67891
```

## 2) First Safe Run

Start with a preflight check:

```bash
grade-assignments --yaml your_config.yaml --dry-run
```

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

Useful diagnostics:

```bash
grade-assignments --yaml your_config.yaml --dump-config
grade-assignments --yaml your_config.yaml --report ./run-report.json
grade-assignments --yaml your_config.yaml --debug
```

If you need run-specific idempotency state location:

```bash
grade-assignments --yaml your_config.yaml --idempotency-key your-run-key --idempotency-state-dir ~/.autograder/state
```

## 3) Common Customizations

- Add Docker packages:
  - Use `extra_dockerfile_lines` under programming settings.
  - Use `extra_installs` for simple additional `RUN` commands in image build.
- Map uploaded filenames to target locations:
  - Use `file_paths` regex mapping under programming settings.
- Use a non-default template repo layout:
  - Set `container_repo_path` (must be under `/repo`; default is `/repo/programming-assignments`).
- Override template grader execution command (when needed):
  - Set `grading_script` and/or `grading_workdir` under programming settings.
- Keep Slack grading error reports privacy-safe by default:
  - Leave `upload_error_artifacts: false` unless doing break-glass debugging.
- Mount multiple template/helper repos in one grading image:
  - Use `additional_repos` with `source_repo` + `container_path` (paths must be under `/repo`).
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
