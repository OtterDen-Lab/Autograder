# Repository Guidelines

## Project Structure & Module Organization
- `Autograder/` contains the core grading logic (assignments, graders, registry, docker utilities).
- `example_files/` holds sample YAML configurations for grading runs.
- `docker/` contains deployment assets used by grading workflows.

## Build, Test, and Development Commands
- `grade-assignments --yaml example_files/workhorse.yaml`: run the main CLI (preferred entrypoint).
- `python Autograder/grade_assignments.py --yaml example_files/workhorse.yaml`: run directly from source.
- `pytest`: run the test suite (if present).

## Coding Style & Naming Conventions
- Python code uses 2-space indentation in this repo; follow existing style in the file you edit.
- Naming: `snake_case` for functions/vars, `CamelCase` for classes, `UPPER_SNAKE_CASE` for constants.
- No formatter/linter is enforced; keep diffs minimal and consistent with surrounding code.

## Testing Guidelines
- Tests (if present) use `pytest`.\n+- Name tests `test_*.py` and functions `test_*`.\n+- Prefer targeted tests for new behavior; there is no explicit coverage gate.

## Commit & Pull Request Guidelines
- Commit messages are short, imperative, sentence-case (e.g., "Bumping patch version", "Reducing log noise").
- Release automation is driven by version bumps in `pyproject.toml`.
- PRs should describe the change, link relevant issues, and include repro steps or logs when touching grading flows.

## Configuration & Runtime Notes
- Canvas credentials are read from `.env`; see `README.md` for required variables.
- Docker is required for programming assignment grading.
- Logging can be directed via `LOG_DIR` (defaults to `/var/log/grading` when writable).
