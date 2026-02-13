# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an autograding system for teaching, primarily focused on Canvas LMS integration. It supports automated grading of programming assignments (via Docker) and text submissions (like learning logs) through a CLI-based grading flow.

## Key Commands

### Development Setup

```bash
# Install dependencies (uses uv for package management)
uv sync

# Install dev dependencies
pip install -e ".[dev]"
```

### Main Grading Flow

```bash
# Grade assignments defined in YAML config
python grade_assignments.py --yaml example_files/programming_assignments.yaml

# Regrade existing submissions
python grade_assignments.py --yaml <config.yaml> --regrade

# Test with limited submissions (e.g., first 5)
python grade_assignments.py --yaml <config.yaml> --limit 5

# Only test student submissions
python grade_assignments.py --yaml <config.yaml> --test

# Control parallelism
python grade_assignments.py --yaml <config.yaml> --max_workers 2

# Quick test command for learning logs
python grade_assignments.py TEST
```

### Testing

```bash
# Run tests
pytest

# Code formatting
black Autograder/
flake8 Autograder/
```

## Architecture

### Core Components

**Registry System** (`Autograder/registry.py`):
- `GraderRegistry`: Factory for grader implementations (auto-discovers from `Autograder/graders/`)
- `AssignmentRegistry`: Factory for assignment types (from `Autograder/assignment.py`)
- All use decorator pattern: `@GraderRegistry.register("name")`

**Assignment Types** (`Autograder/assignment.py`):
- `Assignment`: Abstract base class with `prepare()` and `finalize()` methods
- `Assignment__ProgrammingAssignment`: Downloads student code, handles file renaming
- `Assignment_TextAssignment`: Canvas text submissions (learning logs, reflections)
- All assignments are context managers that manage working directories

**Graders** (`Autograder/graders/`):
- `Grader`: Abstract base class with `execute_grading()` and `score_grading()` methods
- `docker_graders.py`: Template-based Docker grading (compiles, runs tests, diffs output)
- `text_submission_grader.py`: AI-powered grading with rubric generation, clustering, batch processing
- Each grader implements `can_grade_submission()` to validate submission types

**Docker Infrastructure** (`Autograder/docker_utils.py`):
- `DockerClient`: Manages Docker connection and image building
- `DockerContainer`: Lifecycle management with context manager support
- `DockerContainerManager`: Multi-container orchestration
- Thread-safe with unique container names per thread
- Global cleanup tracking to prevent resource leaks

**LMS Interface** (external package: `lms-interface`):
- Lives in `../LMSInterface` (path dependency in `pyproject.toml`)
- Provides `CanvasInterface`, `CanvasCourse`, `CanvasAssignment`, `CanvasQuiz`
- Handles all Canvas API interactions
- Defines core data classes: `Student`, `Submission`, `Feedback`

### YAML Configuration

**Format**:
```yaml
assignment_types:
  programming:
    kind: ProgrammingAssignment
    grader: template-grader
    settings:
      base_image_name: "samogden/cst334"

courses:
  - name: CST334
    id: 29978
    assignment_groups:
      - type: programming
        assignments:
          - id: 506889
            repo_path: PA1
```

Settings merge priority: `type defaults → course → group → assignment`

### Grading Flow

1. **Configuration Loading** (`grade_assignments.py:load_and_validate_config()`):
   - Parse YAML into typed config objects and extract global flags (`prod`, `push`)
   - Validate `assignment_types` and `assignment_groups` contracts

2. **Assignment Collection** (`collect_assignments_to_grade()`):
   - Create LMS interface (Canvas)
   - For each course, process `assignment_groups` only
   - Merge settings and create typed assignment run requests

3. **Parallel Execution** (`execute_grading()`):
   - ThreadPoolExecutor with configurable workers (default: 4)
   - Each assignment graded in `grade_single_assignment()`
   - File locking prevents multiple instances (`ensure_single_instance()`)

4. **Per-Assignment Flow**:
   - Create `Assignment` object (determines type via `kind`)
   - Call `assignment.prepare()` to download/process submissions
   - Create `Grader` object via registry
   - Call `grader.grade_assignment()` to grade all submissions
   - Call `assignment.finalize()` to push grades/feedback to Canvas
   - Cleanup Docker resources

### AI Integration

**Providers** (`Autograder/ai_helper.py`):
- Anthropic (Claude): Default, controlled via `prefer_anthropic` setting
- OpenAI (GPT): Fallback option
- Used for: name extraction, rubric generation, text grading, blank detection

**Text Submission Grading** (`graders/text_submission_grader.py`):
- Rubric generation from submission corpus
- Question extraction from rubric
- Batch processing with configurable batch size
- Clustering analysis for common themes
- Per-student feedback generation

### Important Patterns

**Record Retention**:
- Set `record_retention: true` in YAML assignment settings
- Optionally specify `records_dir: ~/path/to/records`
- Saves feedback to timestamped files: `{assignment}.{student}.{timestamp}.log`
- Implemented in `Assignment.finalize()` via `_save_feedback_record()`

**Score Scaling**:
- All graders return percentage scores (0-100+)
- `Assignment.scale_score_for_canvas()` converts to Canvas points
- Priority: explicit `canvas_points` in YAML → `points_possible` from Canvas → raw percentage

**Slack Notifications**:
- Configure per-course: `slack_channel`, `slack_webhook`, `slack_token`
- Graders can report errors via `report_errors` setting
- Passed through settings hierarchy to grader instances

**Multi-threading Safety**:
- Assignment context managers only change `cwd` in main thread
- Docker containers have unique names per thread
- File locking prevents parallel grade_assignments.py runs
- Each grader gets assignment identifier for logging

## Common Development Tasks

### Adding a New Grader

1. Create file in `Autograder/graders/my_grader.py`
2. Inherit from `Grader` (text submissions) or `FileBasedGrader` (file submissions)
3. Implement required methods:
   - `can_grade_submission()`: Validate submission type
   - `execute_grading()`: Run grading logic
   - `score_grading()`: Convert results to Feedback
4. Register with decorator: `@GraderRegistry.register("my-grader")`
5. Use in YAML: `grader: my-grader`

### Adding a New Assignment Type

1. Add class to `Autograder/assignment.py`
2. Inherit from `Assignment`
3. Implement `prepare()` to fetch/process submissions
4. Optionally override `finalize()` for custom upload logic
5. Register: `@AssignmentRegistry.register("MyType")`
6. Use in YAML: `kind: MyType`

### Testing YAML Configurations

Use example files in `example_files/` as templates:
- `programming_assignments.yaml`: Docker-based grading
- `learning-logs.yaml`: Text submission grading
- `example-template.yaml`: Shows all available options

### Working with Canvas

The system defaults to non-prod Canvas for safety. To use production:

```bash
# In ~/.env
USE_PROD_CANVAS=true
CANVAS_API_KEY_PROD=your_prod_key
CANVAS_API_URL_PROD=https://csumb.instructure.com

# Or in YAML
prod: true  # Top-level flag
```

### Debugging Docker Issues

```bash
# Check containers
docker ps -a

# View logs
docker logs <container_name>

# Manual cleanup (if script fails)
docker stop $(docker ps -a -q --filter ancestor=samogden/cst334)
docker rm $(docker ps -a -q --filter ancestor=samogden/cst334)
```

The system tracks containers/images globally and cleans them up in `grade_assignments.py:main()` finally block.

## Project Dependencies

- **lms-interface**: Canvas API wrapper (local path dependency in `../LMSInterface`)
- **Docker**: Required for programming assignment grading
- **Anthropic/OpenAI**: AI-powered features (optional but recommended)

## Configuration Files

- `.env`: Canvas API keys, Slack tokens, AI API keys (not committed)
- `pyproject.toml`: Python package configuration, dependencies
- `Autograder/logging.yaml`: Logging configuration
- YAML files in `example_files/`: Assignment/course configurations

## Important Notes

- Always test with `--test` flag or `limit` before grading full classes
- Docker cleanup happens in finally block - don't skip with Ctrl+C repeatedly
- File locking prevents multiple instances - remove `/tmp/TeachingTools.grade_assignments.lock` if stuck
