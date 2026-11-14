"""Optional Kubernetes Jobs backend for template grading."""
import base64
from decimal import Decimal
import json
import logging
from pathlib import Path
import posixpath
import time
import uuid

from Autograder.exceptions import GradingError
from .contracts import ExecutionRequest, ExecutionResult

log = logging.getLogger(__name__)
BOOTSTRAP_PATH = '/opt/autograder-job'
MAX_SECRET_BYTES = 900 * 1024
API_TIMEOUT = (5, 20)


class KubernetesRunner:
  def __init__(self, kubeconfig=None, context=None, namespace=None,
               startup_timeout_seconds=180, poll_interval_seconds=2,
               image_pull_secrets=None, *, core_api=None, batch_api=None):
    self.kubeconfig = str(Path(kubeconfig).expanduser()) if kubeconfig else None
    self.context = context
    self.namespace = namespace
    self.startup_timeout_seconds = startup_timeout_seconds
    self.poll_interval_seconds = poll_interval_seconds
    self.image_pull_secrets = image_pull_secrets or []
    self.core_api = core_api
    self.batch_api = batch_api

  def _connect(self):
    if self.core_api is not None and self.batch_api is not None:
      self.namespace = self.namespace or 'default'
      return
    try:
      from kubernetes import client, config
    except ImportError:
      raise GradingError('Kubernetes execution requires the kubernetes extra; '
                         'install with uv sync --extra kubernetes') from None
    # A private client avoids changing global SDK configuration across threads.
    api_client = config.new_client_from_config(
      config_file=self.kubeconfig, context=self.context, persist_config=False)
    self.core_api = client.CoreV1Api(api_client)
    self.batch_api = client.BatchV1Api(api_client)
    if not self.namespace:
      contexts, active = config.list_kube_config_contexts(config_file=self.kubeconfig)
      selected = next((item for item in contexts if item['name'] == self.context), None) if self.context else active
      self.namespace = (selected or {}).get('context', {}).get('namespace') or 'default'

  def _pod_spec(self, request, name):
    from docker.utils import parse_bytes
    from Autograder.docker_utils import (
      DEFAULT_DOCKER_MEMORY_LIMIT, DEFAULT_DOCKER_NANO_CPUS,
      DEFAULT_DOCKER_READ_ONLY_ROOT_FS,
    )
    options = request.options
    supported = {'memory_limit', 'nano_cpus', 'read_only_root_fs'}
    unsupported = set(options) - supported
    if unsupported:
      raise GradingError('Kubernetes runner does not support execution option(s): '
                         + ', '.join(sorted(unsupported)))
    if options.get('read_only_root_fs', DEFAULT_DOCKER_READ_ONLY_ROOT_FS):
      raise GradingError('Kubernetes runner requires a writable image filesystem '
                         'to place submission files')
    limits = {}
    memory = options.get('memory_limit', DEFAULT_DOCKER_MEMORY_LIMIT)
    cpus = options.get('nano_cpus', DEFAULT_DOCKER_NANO_CPUS)
    if memory is not None:
      memory_bytes = parse_bytes(memory)
      if memory_bytes <= 0:
        raise GradingError('memory_limit must be positive for Kubernetes execution')
      limits['memory'] = str(memory_bytes)
    if cpus is not None:
      if isinstance(cpus, bool) or cpus <= 0:
        raise GradingError('nano_cpus must be positive for Kubernetes execution')
      limits['cpu'] = str(Decimal(cpus) / Decimal(1_000_000_000))
    limits['ephemeral-storage'] = '2Gi'
    shell = (
      'mkdir -p "$HOME"; '
      'if [ -x /repo/.venv/bin/python ]; then p=/repo/.venv/bin/python; '
      'elif command -v python3 >/dev/null 2>&1; then p=python3; else p=python; fi; '
      f'exec "$p" {BOOTSTRAP_PATH}/worker.py {BOOTSTRAP_PATH}/request.json')
    return {
      'restartPolicy': 'Never', 'automountServiceAccountToken': False,
      'enableServiceLinks': False,
      'securityContext': {'runAsNonRoot': True, 'runAsUser': 65532,
                          'runAsGroup': 65532, 'fsGroup': 65532,
                          'seccompProfile': {'type': 'RuntimeDefault'}},
      'imagePullSecrets': [{'name': value} for value in self.image_pull_secrets],
      # Existing course images install /repo as root. Copy it into a volume
      # owned by the Pod user so grading can compile without root privileges.
      'initContainers': [{
        'name': 'prepare-workspace', 'image': request.image, 'imagePullPolicy': 'IfNotPresent',
        'command': ['/bin/sh', '-ec',
                    'mkdir -p /workspace/repo && cp -R /repo/. /workspace/repo/ '
                    '&& chmod -R u+rwX /workspace/repo'],
        'securityContext': {'allowPrivilegeEscalation': False,
                            'capabilities': {'drop': ['ALL']}},
        'resources': {'limits': dict(limits), 'requests': dict(limits)},
        'volumeMounts': [{'name': 'workspace', 'mountPath': '/workspace'}],
      }],
      'containers': [{
        'name': 'grader', 'image': request.image, 'imagePullPolicy': 'IfNotPresent',
        'command': ['/bin/sh', '-c', shell],
        'securityContext': {'allowPrivilegeEscalation': False,
                            'capabilities': {'drop': ['ALL']},
                            'seccompProfile': {'type': 'RuntimeDefault'}},
        'env': [{'name': 'HOME', 'value': '/tmp/autograder-home'},
                {'name': 'XDG_CACHE_HOME', 'value': '/tmp/autograder-home/.cache'}],
        'resources': {'limits': limits, 'requests': dict(limits)},
        'volumeMounts': [{'name': 'request', 'mountPath': BOOTSTRAP_PATH, 'readOnly': True},
                         {'name': 'workspace', 'mountPath': '/repo', 'subPath': 'repo'}],
      }],
      'volumes': [{'name': 'request', 'secret': {'secretName': name}},
                  {'name': 'workspace', 'emptyDir': {'sizeLimit': '2Gi'}}],
    }

  def _payload(self, request):
    if isinstance(request.timeout_seconds, bool) or request.timeout_seconds <= 0:
      raise GradingError('Grading command timeout must be positive')
    for path in [source.path for source in request.inputs] + list(request.output_paths):
      normalized = posixpath.normpath(path)
      if (not path.startswith('/') or normalized == '/'
          or normalized == BOOTSTRAP_PATH or normalized.startswith(BOOTSTRAP_PATH + '/')):
        raise GradingError('Kubernetes input/output paths must be absolute and outside '
                           + BOOTSTRAP_PATH)
    if sum(4 * ((len(source.content) + 2) // 3) for source in request.inputs) > MAX_SECRET_BYTES:
      raise GradingError('Kubernetes submission exceeds the 900 KiB encoded request limit; '
                         'use Docker execution for larger submissions')
    payload = json.dumps({
      'command': request.command, 'workdir': request.workdir,
      'timeout_seconds': request.timeout_seconds,
      'inputs': [{'path': source.path,
                  'content': base64.b64encode(source.content).decode('ascii')}
                 for source in request.inputs],
      'output_paths': list(request.output_paths),
    })
    worker = Path(__file__).with_name('kubernetes_worker.py').read_text()
    if len(payload.encode()) + len(worker.encode()) > MAX_SECRET_BYTES:
      raise GradingError('Kubernetes submission exceeds the 900 KiB encoded request limit; '
                         'use Docker execution for larger submissions')
    return {'request.json': payload, 'worker.py': worker}

  def run(self, request: ExecutionRequest) -> ExecutionResult:
    name = 'autograder-' + uuid.uuid4().hex
    data = self._payload(request)
    pod_spec = self._pod_spec(request, name)
    self._connect()
    log.info('Submitting Kubernetes grading Job %s/%s', self.namespace, name)
    job = {
      'apiVersion': 'batch/v1', 'kind': 'Job',
      'metadata': {'name': name, 'labels': {'app.kubernetes.io/name': 'autograder'}},
      'spec': {
        'backoffLimit': 0, 'ttlSecondsAfterFinished': 3600,
        'activeDeadlineSeconds': int(self.startup_timeout_seconds + request.timeout_seconds + 30),
        'template': {'metadata': {'labels': {'app.kubernetes.io/name': 'autograder'}},
                     'spec': pod_spec},
      },
    }
    operation = 'create Job'
    try:
      created = self.batch_api.create_namespaced_job(
        self.namespace, job, _request_timeout=API_TIMEOUT)
      # The owner reference also removes submission data if the grader crashes
      # and Kubernetes later expires the completed/failed Job via its TTL.
      secret = {
        'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
        'metadata': {'name': name, 'ownerReferences': [{
          'apiVersion': 'batch/v1', 'kind': 'Job', 'name': name,
          'uid': created.metadata.uid,
        }]},
        'stringData': data,
      }
      operation = 'create submission Secret'
      self.core_api.create_namespaced_secret(self.namespace, secret, _request_timeout=API_TIMEOUT)
      operation = 'monitor Job or read Pod logs'
      return self._wait(name, request)
    except Exception as error:
      if getattr(error, 'status', None) is not None:
        raise GradingError(f'Kubernetes could not {operation} (HTTP {error.status}); '
                           f'check access, resource policy, and permissions in namespace {self.namespace}') from None
      raise
    finally:
      self._delete(self.batch_api.delete_namespaced_job, name, 'Job',
                   propagation_policy='Background')
      self._delete(self.core_api.delete_namespaced_secret, name, 'submission Secret')

  def _delete(self, method, name, resource, **kwargs):
    try:
      method(name, self.namespace, _request_timeout=API_TIMEOUT, **kwargs)
    except Exception as error:
      if getattr(error, 'status', None) != 404:
        # Never log API response bodies, which can include submission data.
        log.warning('Kubernetes cleanup failed deleting %s %s/%s (%s, HTTP %s); check namespace resources',
                    resource, self.namespace, name, type(error).__name__, getattr(error, 'status', 'unknown'))

  def _wait(self, name, request):
    deadline = time.monotonic() + self.startup_timeout_seconds
    started = False
    detail = 'waiting for a Pod'
    previous_detail = None
    while True:
      # Reading the Job includes its status and uses the existing jobs/get RBAC
      # permission, without requiring access to the jobs/status subresource.
      job = self.batch_api.read_namespaced_job(name, self.namespace, _request_timeout=API_TIMEOUT)
      pods = self.core_api.list_namespaced_pod(
        self.namespace, label_selector='job-name=' + name, _request_timeout=API_TIMEOUT).items
      for pod in pods:
        status = pod.status
        containers = (getattr(status, 'init_container_statuses', None) or []) + (status.container_statuses or [])
        for container in containers:
          state = container.state
          if state.waiting:
            detail = state.waiting.reason or 'container waiting'
            if detail != previous_detail:
              log.info('Kubernetes Job %s/%s: %s', self.namespace, name, detail)
              previous_detail = detail
          if state.terminated and state.terminated.exit_code != 0:
            raise GradingError(f'Kubernetes grading worker failed: {state.terminated.reason} '
                               f'(exit {state.terminated.exit_code}); Job {self.namespace}/{name}')
        if status.phase == 'Failed':
          raise GradingError(f'Kubernetes grading Pod failed: {status.reason}; Job {self.namespace}/{name}')
        if not started and status.phase in {'Running', 'Succeeded'}:
          started = True
          deadline = time.monotonic() + request.timeout_seconds + 30
          log.info('Kubernetes grading Pod started: %s/%s', self.namespace, pod.metadata.name)
        if status.phase == 'Succeeded':
          response = self.core_api.read_namespaced_pod_log(
            pod.metadata.name, self.namespace, container='grader',
            limit_bytes=26 * 1024 * 1024, _request_timeout=API_TIMEOUT,
            _preload_content=False)
          # The generated SDK otherwise JSON-decodes these logs and coerces the
          # mapping to a Python repr, corrupting our JSON transport envelope.
          try:
            content = response.data
          finally:
            response.close()
            response.release_conn()
          return self._result(content, request.output_paths)
      if job.status.failed:
        raise GradingError(f'Kubernetes grading Job failed: {self.namespace}/{name}')
      if time.monotonic() >= deadline:
        phase = 'execution' if started else 'startup'
        raise GradingError(f'Kubernetes grading {phase} timed out ({detail}); Job {self.namespace}/{name}')
      time.sleep(self.poll_interval_seconds)

  @staticmethod
  def _result(content, output_paths):
    try:
      result = json.loads(content)
      if not isinstance(result, dict):
        raise ValueError('Expected a result mapping')
      if result.get('error'):
        raise GradingError('Kubernetes grading worker: ' + str(result['error']))
      if (type(result['return_code']) is not int
          or not isinstance(result['stdout'], str) or not isinstance(result['stderr'], str)
          or not isinstance(result['files'], dict)
          or set(result['files']) != set(output_paths)
          or any(value is not None and not isinstance(value, str) for value in result['files'].values())):
        raise ValueError('Unexpected result fields')
      return ExecutionResult(result['return_code'], result['stdout'], result['stderr'], result['files'])
    except (ValueError, KeyError, TypeError) as error:
      raise GradingError('Kubernetes worker returned missing, malformed, or truncated results') from error
