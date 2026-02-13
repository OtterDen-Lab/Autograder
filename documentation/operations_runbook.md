# Operations Runbook

This runbook covers how to diagnose and recover from failed or partial grading runs.

## Before Running

1. Start with a limited run:
   - `grade-assignments --yaml <config.yaml> --limit 5`
   - Optional preflight: `grade-assignments --yaml <config.yaml> --dry-run`
2. Confirm privacy mode in logs (`privacy_mode=id_only` or `blind`).
3. For any run where retry safety matters, set an idempotency key:
   - `grade-assignments --yaml <config.yaml> --idempotency-key <stable-key>`
   - optional custom state dir: `grade-assignments --yaml <config.yaml> --idempotency-key <stable-key> --idempotency-state-dir <state-dir>`

## During/After Run: Fast Triage

1. Check top-level run summary logs:
   - Assignment failures (`failed > 0`)
   - Per-student push failures (`push failure(s)`)
2. If using Slack run summary, inspect:
   - `Assignment failures:`
   - `Per-student push failures:`
3. If run report is enabled, inspect JSON:
   - `summary.assignment_failures`
   - `summary.push_failures_total`
   - `summary.push_failures`
   - `summary.stage_contracts`

## Failure Types and Response

### A) Assignment Failure

Symptoms:
- Assignment appears in failed list.
- Error has traceback in logs.

Response:
1. Fix root cause (config, environment, grader bug).
2. Re-run that assignment set.
3. Prefer same `idempotency_key` if some pushes may already have succeeded.

### B) Per-student Push Failure (Assignment still "successful")

Symptoms:
- No assignment-level failure.
- Push failure count > 0 in logs/report/Slack.

Response:
1. Inspect failing student labels from push failure summary.
2. Check Canvas/API/network auth health.
3. Re-run with the same `idempotency_key`:
   - previously successful pushes are skipped
   - failed pushes are retried

## Rerun Patterns

### Safe retry after partial push

```bash
grade-assignments --yaml <config.yaml> --idempotency-key <same-key>
```

### Full regrade with push deduplication

```bash
grade-assignments --yaml <config.yaml> --regrade --idempotency-key <same-key>
```

### Dry validation (no push)

```bash
grade-assignments --yaml <config.yaml> --test --limit 5
```

### Dry-run preflight (no download, no grading, no push)

```bash
grade-assignments --yaml <config.yaml> --dry-run
```

### Capture machine-readable run report

```bash
grade-assignments --yaml <config.yaml> --report ./run-report.json
```

### Force run summary Slack destination

```bash
grade-assignments --yaml <config.yaml> --error-slack-channel <channel-id>
```

## Notes on Idempotency vs Ungraded Filtering

- Ungraded filtering selects submissions based on LMS grading state.
- Idempotency controls duplicate push behavior for this tool across reruns.
- They are complementary:
  - ungraded filtering reduces work
  - idempotency reduces duplicate/partial push side effects

## Artifact Hygiene

- Runtime records must not be committed.
- Keep `records_dir` outside repo (`~/autograder-records/...`).
- Ensure git hooks are installed:
  - `bash scripts/install_git_hooks.sh`
