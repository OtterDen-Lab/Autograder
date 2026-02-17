# Troubleshooting Guide

This guide covers common runtime failures and the fastest way to recover.

## Quick Triage Checklist

1. Run a preflight: `grade-assignments --yaml your.yaml --dry-run`
2. Confirm effective config: `grade-assignments --yaml your.yaml --dump-config`
3. Re-run with debug logs: `grade-assignments --yaml your.yaml --debug`
4. Capture a report JSON: `grade-assignments --yaml your.yaml --report ./run-report.json`

## Canvas Connectivity Problems

### Symptoms

- `Failed to initialize Canvas interface`
- `Failed to load Canvas course ...`
- `Failed to load Canvas assignment ...`
- missing assignment metadata (`missing: id, name`)

### Checks

- Confirm `.env` values:
  - `CANVAS_API_URL`
  - `CANVAS_API_KEY`
- Verify token still has course access.
- Verify course/assignment IDs in YAML.
- Check Canvas status/maintenance windows.

### Recovery

- Re-run after maintenance ends.
- Use `--dry-run` first to verify endpoint health before full grading.
- For blind-mode consistency across runs, keep the blind-map file stable:
  - default: `~/.autograder/privacy/blind_id_map.json`
  - override: `AUTOGRADER_BLIND_ID_MAP_PATH`

## Docker Failures

### Symptoms

- `Docker daemon not available`
- image build errors
- container start errors
- seccomp profile decode errors

### Checks

- Docker daemon is running.
- Host has free disk/memory.
- Seccomp profile path/content is valid JSON.
- Base image and repo URLs are reachable.

### Recovery

- Re-run with `--debug` and inspect first Docker error.
- Validate Docker settings:
  - `AUTOGRADER_DOCKER_SECCOMP_PROFILE`
  - `AUTOGRADER_DOCKER_MEMORY_LIMIT`
  - `AUTOGRADER_DOCKER_NANO_CPUS`
  - `AUTOGRADER_DOCKER_PIDS_LIMIT`

## Text Grading / AI Provider Failures

### Symptoms

- fallback between Anthropic/OpenAI
- `AIProviderError` failures
- empty/low-quality analysis due provider outages

### Checks

- API keys are configured for selected provider(s).
- provider/network is reachable.
- model tier values are valid (`small|medium|large`).

### Recovery

- temporarily switch provider preference (`prefer_anthropic`).
- reduce tier size to improve latency/cost.
- rerun with same `--idempotency-key` to avoid duplicate pushes.
- if a submission appears to contain personal data, verify redaction happened in logs/report (`privacy_summary`).

## Push/Finalize Problems

### Symptoms

- push failures in summary
- partial push success

### Checks

- assignment publish state in Canvas
- grade policy/score constraints
- API permissions for posting grades/comments

### Recovery

- rerun with `--idempotency-key` to skip already-pushed submissions.
- inspect `run-report.json` for `push_failed_students`.
- runs with any push failures now return a non-zero exit code (while still aggregating failures into one run-level summary/Slack alert).

## Records / Idempotency Path Issues

### Symptoms

- `records_dir` validation errors
- idempotency state load/save warnings

### Checks

- `records_dir` is absolute (`/path` or `~/path`)
- for safety, `records_dir` is outside repo unless explicitly allowed
- idempotency state directory is writable

### Recovery

- set explicit valid paths in config/CLI.
- for local debugging only, set `AUTOGRADER_ALLOW_IN_REPO_RECORDS=1`.

## Getting a Reproducible Failure Bundle

When escalating an issue, include:

- command used
- sanitized YAML (`--dump-config` output preferred)
- first failing stack trace
- `run-report.json`
- whether Canvas/Docker/provider was in maintenance/outage state

## Reveal-Identity Audit Log

When break-glass mode is used (`AUTOGRADER_BREAK_GLASS=1` with reveal enabled), an audit event is appended to:

- default: `~/.autograder/privacy/reveal_identity_audit.log`
- override: `AUTOGRADER_REVEAL_AUDIT_LOG`
