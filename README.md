# Otter-Autograder

An autograding system for teaching, primarily focused on Canvas LMS integration. Supports automated grading of programming assignments (via Docker), text submissions (like learning logs), and external-tool sourced grades such as Panopto watch analytics.

## Installation

```bash
pip install Otter-Autograder
```

## Quick Start

### 1. Set up Canvas API credentials

Create a `.env` file (by default this tool reads `~/.env`):

```bash
CANVAS_API_KEY=your_canvas_api_key_here
CANVAS_API_URL=https://your-institution.instructure.com
PANOPTO_CLIENT_ID=your_panopto_client_id_here
PANOPTO_CLIENT_SECRET=your_panopto_client_secret_here
PANOPTO_REFRESH_TOKEN=your_panopto_refresh_token_here
```

If you need to bootstrap the first refresh token, run the helper from a machine
with a browser:

```bash
python scripts/bootstrap_panopto_refresh_token.py
```

It will print an authorization URL, capture the redirect on `127.0.0.1:8765`,
and write the resulting refresh token to
`~/.autograder/panopto_refresh_token.json` by default.

### 2. Create a grading configuration

Create a YAML file (e.g., `assignments.yaml`) defining your courses and assignments:

```yaml
privacy_mode: id_only  # none | id_only | blind
reveal_identity: false
idempotency_key: null  # Optional: set to skip re-pushing already pushed feedback
idempotency_state_dir: "~/.autograder/idempotency"  # Optional override

assignment_types:
  programming:
    kind: ProgrammingAssignment
    grader: template-grader
    settings:
      base_image_name: "your-docker-image"
      # Optional: mount extra repositories into specific container paths
      # additional_repos:
      #   - source_repo: "https://github.com/your-org/shared-tests"
      #     container_path: "/repo/shared-tests"
      container_repo_path: "/repo/programming-assignments"  # optional override; default shown
      record_retention: true
      records_dir: "~/autograder-records/your-course"  # required when record_retention=true

courses:
  - name: "Your Course"
    id: 12345
    assignment_groups:
      - type: programming
        assignments:
          - id: 67890
            repo_path: "PA1"
```

### 3. Run the grader

```bash
grade-assignments --yaml assignments.yaml
```

Use a specific env file:

```bash
grade-assignments --yaml assignments.yaml --env /path/to/.env
```

Temporarily include Canvas numeric IDs in logs (break-glass):

```bash
AUTOGRADER_BREAK_GLASS=1 grade-assignments --yaml assignments.yaml --reveal-identity
```

Idempotent push mode (safe rerun key):

```bash
grade-assignments --yaml assignments.yaml --idempotency-key spring26-ll2
```

Path safety defaults:

- `record_retention: true` requires an explicit absolute `records_dir` (or `~/...`).
- `records_dir` is blocked if it points inside this git repo unless `AUTOGRADER_ALLOW_IN_REPO_RECORDS=1`.
- Idempotency state defaults to `~/.autograder/idempotency`.

## Features

### Supported Assignment Types

- **Programming Assignments**: Docker-based grading with template matching and test execution
- **Text Submissions**: AI-powered grading with rubric generation and clustering analysis
- **External Tool Assignments**: synthesize grades from external systems such as Panopto watch progress

### Key Capabilities

- Parallel execution with configurable worker threads
- Privacy modes: `none`, `id_only`, `blind`
- Optional idempotent feedback push via `idempotency_key`
- Automatic score scaling to Canvas points
- Slack notifications for grading errors
- Record retention for audit trails
- Regrade support for existing submissions
- Test mode for validation before full grading runs

## Usage Examples

### Grade with limited submissions (testing)

```bash
grade-assignments --yaml config.yaml --limit 5
```

### Regrade existing submissions

```bash
grade-assignments --yaml config.yaml --regrade
```

### Regrade a single student submission

```bash
grade-assignments --yaml config.yaml --regrade --student-id 123456
```

### Test submissions without pushing grades

```bash
grade-assignments --yaml config.yaml --test
```

### Control parallelism

```bash
grade-assignments --yaml config.yaml --max_workers 2
```

### Show stage timings and push aggregates

```bash
grade-assignments --yaml config.yaml --show-stage-timings
```

### Dry-run preflight (no grading)

```bash
grade-assignments --yaml config.yaml --dry-run
```

### Dump effective merged assignment config

```bash
grade-assignments --yaml config.yaml --dump-config
```

### Write a run report JSON

```bash
grade-assignments --yaml config.yaml --report ./run-report.json
```

### Override Slack channel for run-level failure summaries

```bash
grade-assignments --yaml config.yaml --error-slack-channel C0123456789
```

### Set custom idempotency state directory

```bash
grade-assignments --yaml config.yaml --idempotency-key spring26-ll2 --idempotency-state-dir ~/.autograder/state
```

### Enable debug logging

```bash
grade-assignments --yaml config.yaml --debug
```

## Configuration

See the `example_files/` directory for complete configuration examples:

- `workhorse.yaml`: Recommended combined programming + text setup
- `programming_assignments.yaml`: Programming-only setup
- `learning-logs.yaml`: Text submission grading
- `minimal-external.yaml`: Simplest Panopto-backed external assignment setup
- `minimal-programming.yaml`: Simplest programming assignment setup
- `minimal-text.yaml`: Simplest text assignment setup
- `example-template.yaml`: All available options

## Requirements

- Python >= 3.12
- Docker (for programming assignment grading)
- Canvas API access
- Optional: OpenAI or Anthropic API keys for AI-powered features

## Docker Security Boundaries

Programming submissions run in ephemeral Docker containers with baseline hardening:

- `no-new-privileges:true`
- explicit seccomp profile (`Autograder/seccomp/autograder-seccomp.json` by default)
- resource limits (`mem_limit`, `nano_cpus`, `pids_limit`)

Optional hardened mode:

- set `AUTOGRADER_DOCKER_READ_ONLY_ROOT_FS=1` to use a read-only root filesystem (with writable tmpfs at `/tmp` and `/var/tmp`).

Override knobs (when needed for compatibility/performance):

- `AUTOGRADER_DOCKER_SECCOMP_PROFILE` (path to seccomp profile)
- `AUTOGRADER_DOCKER_MEMORY_LIMIT` (example: `1g`)
- `AUTOGRADER_DOCKER_NANO_CPUS` (example: `2000000000` for 2 CPUs)
- `AUTOGRADER_DOCKER_PIDS_LIMIT` (example: `256`)

Security note: this reduces risk but is not a complete sandbox against all kernel/container escape classes. Keep Docker and host OS patched.

## Local Git Hygiene Hook (Recommended)

Install the repository-managed pre-commit hook so hygiene checks run before each commit:

```bash
bash scripts/install_git_hooks.sh
```

This hook runs `scripts/check_repo_hygiene.sh`.
The installer also adds a repo-local alias so you can run `git bump patch` (or `minor`/`major`) to bump, stage, commit, tag, and push.
LMSInterface is installed as a pinned package dependency (`lms-interface @ ...`) in `pyproject.toml`.
`git bump` no longer vendors source into this repo.

## Documentation

For detailed documentation, see:

- `documentation/instructor_onboarding.md` (minimal setup + common customizations)
- `documentation/operations_runbook.md` (failure autopsy + rerun procedures)
- `documentation/troubleshooting.md` (common runtime failures and recovery steps)
- `documentation/architecture.md` (system data flow and component relationships)
- `documentation/customization.md` (adding graders/kinds + common recipes)
- `documentation/configuration_schema.md` (field-by-field config reference)
- `documentation/privacy_audit.md` (PII surfaces and privacy controls)
- `documentation/archives/step_by_step_grader_reference.md` (archived grader concept for future redesign)
- [documentation directory on GitHub](https://github.com/OtterDen-Lab/Autograder/tree/main/documentation)

## License

This project is licensed under the GPL-3.0-or-later license. See the LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue or pull request on [GitHub](https://github.com/OtterDen-Lab/Autograder).
