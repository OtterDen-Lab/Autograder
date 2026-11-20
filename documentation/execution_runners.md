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
or building images. Image preparation is separate from submission execution.

## Kubernetes execution (optional)

Install the `kubernetes` extra alongside any other extras you use. Select the
backend in the existing template grader settings:

```yaml
assignment_types:
  programming:
    kind: ProgrammingAssignment
    grader: template-grader
    settings:
      execution_backend: kubernetes
      memory_limit: "2g"
      nano_cpus: 1000000000  # one CPU; requests and limits are equal
      command_timeout_seconds: 120
      # Optional; the kubeconfig's current context and namespace are used by default.
      # kubernetes:
      #   kubeconfig: "~/.kube/config"
      #   context: autograder
      #   namespace: autograder
      #   startup_timeout_seconds: 180
      #   poll_interval_seconds: 2
      #   image_pull_secrets: [registry-access]
```

For example, run one submission on the grader box as its normal `grader` user:

```sh
uv run --extra postgres --extra kubernetes grade-assignments \
  --yaml ~/.config/otter-autograder/grading-testing.yaml \
  --env ~/.tokens/autograder.env \
  --max_workers 1 --regrade --limit 1 --skip-cooldown --debug
```

The SDK honors `KUBECONFIG` or defaults to `~/.kube/config`; kubeconfig and context
can be overridden in the optional `kubernetes` mapping. Its context namespace is
used unless explicitly overridden, falling back to `default`. Keep cluster tokens
and certificates in kubeconfig, outside grading YAML.

Docker still resolves/builds/pushes the same registry images locally. The cluster
pulls those images independently. An open registry needs no `image_pull_secrets`;
node network access, registry TLS trust, and registry protocol configuration must
still work. An HTTP-only registry requires appropriate containerd/k3s node
configuration. This backend does not modify cluster or registry configuration.

### Temporary Kubernetes image builds (optional)

To also move image creation off the grader box, select the independent image
build backend. Local Docker remains the default for existing configurations:

```yaml
settings:
  execution_backend: kubernetes
  image_build_backend: kubernetes
  image_registry: docker-registry.p.zxqw.dev  # default; host[:port][/repository-prefix]
  memory_limit: "2g"                       # grading Pod
  kubernetes_build:
    namespace: autograder-test-build
    memory_limit: "4g"                     # build Pod, independent of grading limits
    nano_cpus: 2000000000
    storage_limit: "20Gi"
    startup_timeout_seconds: 180
    upload_timeout_seconds: 300
    build_timeout_seconds: 1800
    # builder_image: moby/buildkit:v0.35.0-rootless
    # registry_scheme: https              # http must be explicitly selected
    # registry_verify_tls: true           # or a local CA bundle path
    # registry_auth_file: "~/.docker/build-auth.json"
```

Use the same `--extra kubernetes` as for execution. When both backends are
Kubernetes, no local Docker engine, BuildKit service, `buildctl`, or Docker CLI
is needed on the coordinator. The Python Docker package remains a dependency for
existing utility code. Image builds can also run in Kubernetes while grading
uses local Docker. Repository cloning, golden-file selection, Dockerfile
generation, and commit-based image tags retain their existing behavior.

Image existence is checked using the registry's manifest API, without downloading
layers. Only structured `MANIFEST_UNKNOWN`/`NAME_UNKNOWN` responses are cache
misses; authentication failures, network errors, and generic/proxy 404s stop the
operation. The existing local-Docker missing-reference handling is unchanged.
Changing `image_registry` also changes the destination of local Docker builds.

On a cache miss the coordinator creates a uniquely named Job, then streams a
compressed build-context archive into its waiting init container over `pods/exec`.
This avoids the grading Secret's small-payload limit and needs no object store,
shared filesystem, upload server, or persistent builder deployment. The archive
is spooled to local temporary disk, so the coordinator still needs disk space
for repository checkouts and the compressed context. The upload uses a known byte
count to end the archive stream without relying on WebSocket stdin-close support.

The main container runs pinned **rootless BuildKit**, builds the existing
Dockerfile, and pushes the finished image. A successful build is followed by a
registry manifest check before grading proceeds. The build Pod, configuration
Secret, and temporary context/cache volumes are removed after success or failure.
An active deadline, Job TTL, and Secret owner reference provide cleanup if the
coordinator disappears. Build failure logs include a bounded tail before cleanup.
No layer cache is retained between build Jobs; already-published course images
remain reusable through the normal registry lookup.

**Administrator setup is required once per cluster.** The rootless builder still
needs user namespaces, setuid mapping helpers, and Unconfined seccomp/AppArmor.
It cannot run under the grading namespace's restricted Pod Security policy.
Provision a separate build namespace with an administrator-approved policy;
do not relax the student grading namespace. The builder itself does not request
`privileged: true`, host mounts, or a Kubernetes API token. It builds staff-owned
course contexts, never submitted student Dockerfiles. The upstream
`--oci-worker-no-process-sandbox` rootless mode is used; it is suitable here only
for those trusted build contexts.

The [builder namespace and RBAC reference](../docker/kubernetes/builder-rbac.yaml)
creates no persistent build workload. Adapt its ServiceAccount subject to the
grader's existing account. Additional permissions are scoped to the build
namespace: Jobs `create/get/delete`, Pods `get/list`, Pod logs `get`, `pods/exec`
`get/create`, and Secrets `create/delete`. `kubernetes_build.namespace` defaults to
`autograder-build`; kubeconfig, context, and image-pull-secret settings inherit
from `kubernetes` unless overridden in `kubernetes_build`. Choose the build
namespace explicitly when using per-environment provisioning.

For the infrastructure repository's `k3s-client` role, optional build access is
enabled with `k3s_client_build_enabled=true`; its default namespace is the grading
namespace plus `-build`. Rerun the access playbook with that flag before enabling
the new backend. Namespace creation and policy changes are deliberately not
performed by the grader.

An open internal registry needs no auth file. For authenticated registries,
`registry_auth_file` is a local Docker `config.json` with inline `auths`. The same
file is used for registry lookup and mounted in a temporary Secret for BuildKit's
pulls/pushes. Local `credsStore`/`credHelpers` and identity-token-only logins are
not supported. Keep the file private; do not put credentials directly in YAML.
Basic and Bearer-challenge lookup authentication are supported. Registry TLS and
HTTP settings apply to the destination registry in the builder; k3s worker nodes
must separately be able to pull it. Third-party base registries need normal
network/TLS access from the build Pod. Current build/execution scheduling assumes
compatible node architectures; it does not introduce multi-platform builds.

For an unprivileged LXC coordinator, enable both Kubernetes backends, retain Git,
Python, kubeconfig, registry/network access, and writable temporary storage, and
remove the requirement for a local container engine. A live build should be
verified in k3s before retiring the existing VM.

References: [upstream rootless build Job](https://github.com/moby/buildkit/blob/master/examples/kubernetes/job.rootless.yaml),
[rootless requirements](https://github.com/moby/buildkit/blob/master/docs/rootless.md),
[registry API errors](https://distribution.github.io/distribution/spec/api/).

Each submission gets a unique Job with no automatic retries (`backoffLimit: 0`).
A temporary Secret holds a small Python worker and the submission payload. The
worker copies files to their requested paths, removes preexisting output files,
runs the same grading command, and returns stdout/stderr and requested artifacts
through a JSON envelope in Pod logs. No shared filesystem, exec permission, or
additional worker image is required. The course image must provide Python 3,
`bash`, and a writable filesystem, as the existing template images do.

The namespace-scoped kubeconfig needs these RBAC permissions:

| API group | Resource | Verbs |
| --- | --- | --- |
| `batch` | `jobs` | `create`, `get`, `delete` |
| core | `pods` | `list` |
| core | `pods/log` | `get` |
| core | `secrets` | `create`, `delete` |

See [the example Role and binding](../docker/kubernetes/runner-rbac.yaml) if the
Ansible-provisioned account needs additional permissions. Adapt its namespace and
service-account name to the existing account; the runner never provisions RBAC.

Jobs and Secrets are deleted after results are collected, including on errors.
Secrets have a Job owner reference; a one-hour Job TTL and an active deadline
provide cleanup if the client disappears. Cleanup failures are logged with the
Job name for manual inspection. Submission contents and grading results pass
through the cluster's Secret storage and Pod logging system; use a namespace and
logging retention policy appropriate for student data.

Memory and CPU requests match their limits. Docker-style memory sizes such as
`2g` are converted to byte quantities, and `nano_cpus` becomes CPU units. If not
set, the existing global Docker memory/CPU defaults are used (normally 1 GiB and
two CPUs). Each Job also requests/limits 2 GiB of ephemeral storage. The worker
has no mounted Kubernetes API token or Canvas credentials; privilege escalation
is disabled and seccomp uses the cluster runtime's default profile. This is not
the custom Docker seccomp profile. Node-level PID caps and network policy are
cluster responsibilities. Explicit Docker-only options such as local mounts,
PID limits, custom seccomp profiles, GPUs, network disabling, shared-memory
overrides, and read-only root filesystems are rejected rather than ignored. The
ImageNet trial grader therefore continues to require Docker for its local dataset
mount and GPU settings.

Pods meet the Kubernetes restricted Pod Security policy: UID/GID 65532,
`runAsNonRoot`, no privilege escalation, all capabilities dropped, and runtime
default seccomp. An init container running with the same restrictions copies
the image's `/repo` into a per-Job `emptyDir` workspace and makes those copies
writable by the grading user. Kubernetes `fsGroup` grants access to the volume;
no root init container or namespace policy exception is needed. The main worker
mounts that workspace at `/repo`, with a writable HOME/cache under `/tmp`. Course
images must make `/repo` readable and its directories traversable by this user.
The workspace consumes the Job's ephemeral storage budget. Startup time includes
this copy. Commands that require root or write to other root-owned image paths
need to be adapted for non-root execution.

If an Ansible `job-submitter` Role already grants Job and Pod/log access, add
`create` and `delete` on core `secrets` to that Role and rerun its access playbook.
The runner reads status through `get jobs`, without a separate `jobs/status`
permission. A 403 error identifies the denied operation; cleanup warnings name
the resource that could not be deleted. Diagnose the actual account from the
grader box with, for example:

```sh
kubectl auth can-i create jobs.batch -n autograder-test
kubectl auth can-i create secrets -n autograder-test
kubectl auth can-i delete secrets -n autograder-test
kubectl auth can-i get jobs.batch -n autograder-test
kubectl auth can-i list pods -n autograder-test
kubectl auth can-i get pods --subresource=log -n autograder-test
```

This first transfer mechanism limits the entire encoded input request plus worker
to 900 KiB, below Kubernetes' 1 MiB Secret limit. Combined stdout, stderr, and text
artifacts are limited to 4 MiB. Larger requests/results fail without publishing
grades. Kubernetes startup has its own timeout (180 seconds by default); the
command timeout starts inside the worker. OOM kills, startup failures, timeouts,
and missing/truncated results are grading errors and never become zero grades or
success checkpoints. A valid feedback result with `grade: 0` remains supported.

References: [Python kubeconfig loading](https://github.com/kubernetes-client/python/blob/master/kubernetes/config/kube_config.py),
[Job lifecycle](https://kubernetes.io/docs/concepts/workloads/controllers/job/),
[Secret limits](https://kubernetes.io/docs/concepts/configuration/secret/#size-limit).

## Concurrency and verification

The runner interface is synchronous: it may perform local work or wait for remote
work to finish. Programming submissions still dispatch sequentially; assignment
concurrency still uses `--max_workers`. Increasing submission concurrency is a
separate step, not a behavior change made by this extraction.

Start Kubernetes tests with `--max_workers 1`. More assignment workers can submit
more Jobs; Kubernetes queues Pods when their resource requests do not fit. No
new per-submission concurrency is introduced here.

Run `pytest tests/test_execution_runners.py` for boundary and lifecycle tests.
An optional live test uses a local shell-capable image, with networking disabled:

```sh
AUTOGRADER_DOCKER_TEST_IMAGE=your-local-image pytest tests/test_execution_runners.py -k live_docker
```

The live test copies a file, executes a command, retrieves its output, and cleans
up the container. It does not build or publish images or connect to Canvas.
