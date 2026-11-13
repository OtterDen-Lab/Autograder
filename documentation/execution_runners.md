# Template grading execution

`TemplateGrader` describes the course grading workflow: prepare the course image,
map submission files to their configured paths, choose the grading command, and
interpret feedback. It inherits from `FileBasedGrader`, independently of the
legacy `DockerGrader` class.

Execution is supplied through two injectable Python interfaces:

- `Runner.run(ExecutionRequest) -> ExecutionResult` executes one submission.
- `ImageBuilder.find/build/release` resolves registry references, builds missing
  images, and releases local image resources.

The default implementations are `DockerRunner` and `DockerImageBuilder`, so existing
YAML configurations and the `template-grader` registry name continue to work.
`Autograder.graders.docker_graders.TemplateGrader`, `Grader__template_grader`, and
`Grader__docker` remain import aliases. The old `DockerGrader` API remains available
for independent Docker-specific subclasses. Subclasses of `TemplateGrader` that
previously manipulated live containers should use the request/result hooks below.

## Request and result boundary

A request contains the image reference, command, working directory, input file
paths and bytes, requested output paths, timeout, and execution options. An input
is copied into the request without changing the caller's stream position. A
runner returns the exit status, decoded stdout/stderr, and collected text outputs.
It must collect outputs before releasing its worker resources. Scoring and error
reporting consume these collected artifacts and do not need a live container.

Template feedback must contain a finite numeric `grade`. Missing or malformed
feedback, or feedback marked `err: true` / `error: true`, fails the submission's
grading operation even if the command exits successfully. Such failures leave
feedback unset, so they do not publish to Canvas or record a success checkpoint.
A legitimate student failure may still produce `grade: 0` with `err: false`.

The Docker runner owns each invocation's container in a local variable, and stops
it on startup, upload, execution, or output-retrieval failures as well as success.
The runner does not publish grades or hold Canvas credentials. The existing
orchestrator continues to own Canvas access, assignment leases, and cooldowns.
The existing Docker security defaults and YAML resource options remain in use.
`ExecutionRequest.options` currently retains the existing Docker option names;
a future backend must translate supported settings and reject unsupported ones.

To customize a template workflow, override:

- `_execution_files(files_to_copy)` to add inputs.
- `_execution_command()` to return the command and working directory.
- `output_paths` to declare the text artifacts to collect.
- `score_grading(result, ...)` to interpret `result.files` and its exit status.

The ImageNet trial grader uses these hooks for its runner script, GPU/dataset
options, and collected metrics. It does not read files from a live container.

A Python caller can pass `runner=` and `image_builder=` when constructing the
grader. Injecting both permits another backend to operate without initializing
Docker. The default image builder still needs a local Docker daemon when resolving
or building images. Image preparation is separate from submission execution, so
a later Kubernetes runner can use prebuilt images or a separately supplied builder.
There is no Kubernetes backend or YAML backend selector in this change.

## Concurrency and verification

The runner interface is synchronous: it may perform local work or wait for remote
work to finish. Programming submissions still dispatch sequentially; assignment
concurrency still uses `--max_workers`. Increasing submission concurrency is a
separate step, not a behavior change made by this extraction.

Run `pytest tests/test_execution_runners.py` for boundary and lifecycle tests.
An optional live test uses a local shell-capable image, with networking disabled:

```sh
AUTOGRADER_DOCKER_TEST_IMAGE=your-local-image pytest tests/test_execution_runners.py -k live_docker
```

The live test copies a file, executes a command, retrieves its output, and cleans
up the container. It does not build or publish images or connect to Canvas.
