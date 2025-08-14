# Configuration Examples

This directory contains example YAML configuration files for the Autograder system.

## Docker Configurable Grader

The `docker-configurable` grader allows you to specify either a grading script or a series of bash commands to run in a Docker container for grading submissions.

### Configuration Options

#### grading_script
- **Type**: String
- **Description**: Path to a grading script to execute in the container
- **Example**: `"./grade.py"`
- **Note**: Cannot be used together with `grading_commands`

#### grading_commands
- **Type**: List of strings
- **Description**: A list of bash commands to execute sequentially in the container
- **Example**: 
  ```yaml
  grading_commands:
    - "gcc -o program *.c"
    - "python test_runner.py --yaml-output"
  ```
- **Note**: Cannot be used together with `grading_script`

#### working_dir
- **Type**: String
- **Description**: The working directory inside the container where files will be copied and commands will be executed
- **Default**: `/tmp/grading`
- **Example**: `"/tmp/grading"`

#### image
- **Type**: String  
- **Description**: Docker image to use as the base container
- **Default**: `"ubuntu"`
- **Example**: `"ubuntu:20.04"`

### Output Format

The grading script or final command in `grading_commands` must output YAML to stdout with the following format:

```yaml
score: 85.0
feedback: "Great work! All tests passed except one edge case."
```

### Usage Examples

#### Using a grading script:
```yaml
- name: PA6-script
  id: 487786
  kind: ProgrammingAssignment
  grader: docker-configurable
  kwargs:
    grading_script: "./grade.py"
    working_dir: "/tmp/grading"
```

#### Using command sequence:
```yaml
- name: PA7-commands  
  id: 487787
  kind: ProgrammingAssignment
  grader: docker-configurable
  kwargs:
    grading_commands:
      - "gcc -o program *.c"
      - "python test_runner.py --yaml-output"
    working_dir: "/tmp/grading"
```

### How It Works

1. A base Ubuntu container is started
2. Student submission files are copied to the specified `working_dir`
3. Either the `grading_script` is executed OR the `grading_commands` are run sequentially
4. The stdout is parsed for YAML containing `score` and `feedback` keys
5. Raw stdout and stderr are included as additional feedback for debugging