# Archived Concept: Step-by-step Grader

Status: archived reference only (not supported in current build).

## Why this exists

This grader was originally prototyped for shell-command workflows where student submissions are evaluated step-by-step against a "golden" command sequence.

It was removed from active runtime paths to keep the production surface area focused on currently supported graders:

- `template-grader` for `ProgrammingAssignment`
- `TextSubmissionGrader` for `TextAssignment`

## Core idea (legacy design)

The archived design used two containers:

- `golden`: runs expected commands from rubric
- `student`: runs student commands

For each step:

1. Execute golden command and student command.
2. Compare `(stdout, stderr, return code)`.
3. Optionally rollback student container to golden state on mismatch.
4. Compute score as matched steps / total steps.

## Why it was archived

- Not part of current supported config contracts.
- No typed settings model or validation.
- No dedicated tests in the current suite.
- Adds maintenance overhead and user confusion if left registered.

## If we rebuild it later

Recommended direction:

1. Implement as a fresh grader with typed settings in `config_models.py`.
2. Keep it behind explicit supported-kind/graders allowlists.
3. Add unit tests for parser, comparator, rollback behavior, and scoring.
4. Add integration tests with Docker fixtures.
5. Document a minimal working YAML and failure semantics.

Treat this archived design as inspiration, not drop-in code.
