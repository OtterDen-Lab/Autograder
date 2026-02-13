# Otter-Autograder

An autograding system for teaching, primarily focused on Canvas LMS integration. Supports automated grading of programming assignments (via Docker) and text submissions (like learning logs).

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
```

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

### Test submissions without pushing grades

```bash
grade-assignments --yaml config.yaml --test
```

### Control parallelism

```bash
grade-assignments --yaml config.yaml --max_workers 2
```

## Configuration

See the `example_files/` directory for complete configuration examples:

- `workhorse.yaml`: Recommended combined programming + text setup
- `programming_assignments.yaml`: Programming-only setup
- `learning-logs.yaml`: Text submission grading
- `example-template.yaml`: All available options

## Requirements

- Python >= 3.12
- Docker (for programming assignment grading)
- Canvas API access
- Optional: OpenAI or Anthropic API keys for AI-powered features

## Documentation

For detailed documentation, see [the documentation directory](https://github.com/OtterDen-Lab/Autograder/tree/main/documentation).

## License

This project is licensed under the GPL-3.0-or-later license. See the LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue or pull request on [GitHub](https://github.com/OtterDen-Lab/Autograder).
