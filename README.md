# Otter-Autograder

An autograding system for teaching, primarily focused on Canvas LMS integration. Supports automated grading of programming assignments (via Docker), text submissions (like learning logs), quizzes, and manual exam grading with AI assistance.

## Installation

```bash
pip install Otter-Autograder
```

## Quick Start

### 1. Set up Canvas API credentials

Create a `.env` file in your working directory:

```bash
CANVAS_API_KEY=your_canvas_api_key_here
CANVAS_API_URL=https://your-institution.instructure.com
```

### 2. Create a grading configuration

Create a YAML file (e.g., `assignments.yaml`) defining your courses and assignments:

```yaml
assignment_types:
  programming:
    kind: ProgrammingAssignment
    grader: template-grader
    settings:
      base_image_name: "your-docker-image"

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

## Features

### Supported Assignment Types

- **Programming Assignments**: Docker-based grading with template matching and test execution
- **Text Submissions**: AI-powered grading with rubric generation and clustering analysis
- **Quizzes**: Canvas quiz grading support
- **Exams**: Manual grading with AI-assisted name extraction and handwriting recognition
- **Web-based Grading UI**: Modern interface for problem-first exam grading

### Key Capabilities

- Parallel execution with configurable worker threads
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

- `example-programming_assignments.yaml`: Docker-based grading
- `journal_assignments.yaml`: Text submission grading
- `example-exams.yaml`: Exam grading setup
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
