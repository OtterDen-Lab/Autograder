# Configuration Examples

This directory contains YAML examples for the currently supported grading flows.

## Active examples

- `workhorse.yaml`: Primary production-style config using `assignment_types` + `assignment_groups` for both programming and text assignments.
- `programming_assignments.yaml`: Programming-only config with `template-grader`.
- `learning-logs.yaml`: Text submission grading example with `TextSubmissionGrader`.
- `minimal-programming.yaml`: Smallest viable programming config (copy/paste starter).
- `minimal-text.yaml`: Smallest viable text-grading config (copy/paste starter).
- `example-template.yaml`: Programming template with detailed `template-grader` options.
- `algo.yaml`: Programming assignment with custom file mapping and Docker setup.

## Notes

- Prefer `workhorse.yaml` for new setups.
- Set `privacy_mode` to `id_only` (default), `blind`, or `none` at top level.
- Set `reveal_identity: true` only for break-glass debugging and run with `AUTOGRADER_BREAK_GLASS=1`.
- Set `idempotency_key` to avoid duplicate feedback pushes when rerunning the same grading job.
- `idempotency_state_dir` defaults to `~/.autograder/idempotency`.
- `record_retention: true` requires an explicit absolute `records_dir` (or `~/...`).
- `records_dir` paths inside this repo are blocked unless `AUTOGRADER_ALLOW_IN_REPO_RECORDS=1`.
- Each student programming submission is graded in a fresh Docker container.
