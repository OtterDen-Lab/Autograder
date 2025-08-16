# Configuration Examples

This directory contains example YAML configuration files for the Autograder system.

## Docker Configurable Grader

The `docker-configurable` grader allows you to specify either a grading script or a series of bash commands to run in a Docker container for grading submissions.

### Configuration Options

#### Required (choose one)

##### grading_script
- **Type**: String
- **Description**: Path to a grading script to execute in the container
- **Example**: `"./grade.py"`
- **Note**: Cannot be used together with `grading_commands`

##### grading_commands
- **Type**: List of strings
- **Description**: A list of bash commands to execute sequentially in the container
- **Example**: 
  ```yaml
  grading_commands:
    - "gcc -o program *.c"
    - "python test_runner.py --yaml-output"
  ```
- **Note**: Cannot be used together with `grading_script`

#### Optional Configuration

##### working_dir
- **Type**: String
- **Description**: The working directory inside the container where files will be copied and commands will be executed
- **Default**: `/tmp/grading`
- **Example**: `"/tmp/grading"`

##### image
- **Type**: String  
- **Description**: Docker image to use as the base container (ignored if dockerfile_text is provided)
- **Default**: `"ubuntu"`
- **Example**: `"python:3.9"`

##### additional_installs
- **Type**: List of strings
- **Description**: Additional package installation commands to run during image build
- **Example**: 
  ```yaml
  additional_installs:
    - "apt-get update && apt-get install -y gcc"
    - "pip install pytest pyyaml"
  ```

##### dockerfile_text
- **Type**: String (multiline)
- **Description**: Complete Dockerfile content to build a custom image. Takes precedence over image + additional_installs
- **Example**: 
  ```yaml
  dockerfile_text: |
    FROM ubuntu:20.04
    RUN apt-get update && apt-get install -y python3
    WORKDIR /tmp/grading
  ```

##### additional_files
- **Type**: List of strings or objects
- **Description**: Additional files/directories to copy into the image during build
- **Example**: 
  ```yaml
  additional_files:
    - src: "./test_framework/*"
      dst: "/tmp/grading/tests/"
    - "./grade_template.py"  # copies to working_dir
  ```

##### dockercompose_text
- **Type**: String (multiline)
- **Description**: Docker Compose configuration (reserved for future use)
- **Status**: Not yet implemented

### Output Format

The grading script or final command in `grading_commands` must output YAML to stdout with the following format:

```yaml
score: 85.0
feedback: "Great work! All tests passed except one edge case."
```

### Usage Examples

#### Basic grading script:
```yaml
- name: PA6-script
  id: 487786
  kind: ProgrammingAssignment
  grader: docker-configurable
  kwargs:
    grading_script: "./grade.py"
    working_dir: "/tmp/grading"
```

#### Command sequence:
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

#### Advanced with custom image and additional tools:
```yaml
- name: PA8-advanced
  id: 487788
  kind: ProgrammingAssignment
  grader: docker-configurable
  kwargs:
    image: "python:3.9"
    additional_installs:
      - "apt-get update && apt-get install -y gcc"
      - "pip install pytest pyyaml"
    additional_files:
      - src: "./test_framework/*"
        dst: "/tmp/grading/tests/"
      - "./grade_template.py"
    grading_script: "python grade_template.py"
    working_dir: "/tmp/grading"
```

#### Complete Dockerfile customization:
```yaml
- name: PA9-dockerfile
  id: 487789
  kind: ProgrammingAssignment
  grader: docker-configurable
  kwargs:
    dockerfile_text: |
      FROM ubuntu:20.04
      RUN apt-get update && apt-get install -y python3 python3-pip gcc
      RUN pip3 install pytest pyyaml
      COPY ./grading_tools/ /opt/grading/
      ENV PATH="/opt/grading:${PATH}"
      WORKDIR /tmp/grading
      CMD ["/bin/bash"]
    grading_commands:
      - "python3 /opt/grading/auto_grade.py"
```

### How It Works

1. **Image Preparation**: If dockerfile_text, additional_installs, or additional_files are specified, a custom Docker image is built with these additions
2. **Container Start**: A container is started from the prepared image
3. **File Copy**: Student submission files are copied to the specified `working_dir`
4. **Grading Execution**: Either the `grading_script` is executed OR the `grading_commands` are run sequentially
5. **Result Parsing**: The stdout is parsed for YAML containing `score` and `feedback` keys
6. **Feedback Assembly**: Raw stdout and stderr are included as additional feedback for debugging