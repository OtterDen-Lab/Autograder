"""Build and push course images with a temporary rootless BuildKit Job."""
import json
import logging
from pathlib import Path
from decimal import Decimal
import tarfile
import tempfile
import time
import uuid

from Autograder.exceptions import ImageBuildError
from .kubernetes import KubernetesRunner, API_TIMEOUT
from .registry import RegistryClient

log = logging.getLogger(__name__)
DEFAULT_BUILDKIT_IMAGE = 'moby/buildkit:v0.35.0-rootless'


class KubernetesImageBuilder:
  def __init__(self, kubeconfig=None, context=None, namespace='autograder-build',
               builder_image=DEFAULT_BUILDKIT_IMAGE, image_pull_secrets=None,
               startup_timeout_seconds=180, upload_timeout_seconds=300,
               build_timeout_seconds=1800, poll_interval_seconds=2,
               memory_limit='4g', nano_cpus=2_000_000_000, storage_limit='20Gi',
               registry_scheme='https', registry_verify_tls=True, registry_auth_file=None,
               *, core_api=None, batch_api=None, exec_stream=None, registry=None):
    self.cluster = KubernetesRunner(kubeconfig=kubeconfig, context=context, namespace=namespace,
      core_api=core_api, batch_api=batch_api, image_pull_secrets=image_pull_secrets)
    self.builder_image = builder_image
    self.startup_timeout = startup_timeout_seconds
    self.upload_timeout = upload_timeout_seconds
    self.build_timeout = build_timeout_seconds
    self.poll_interval = poll_interval_seconds
    self.memory_limit = memory_limit
    self.nano_cpus = nano_cpus
    self.storage_limit = storage_limit
    self.exec_stream = exec_stream
    self.registry = registry if registry is not None else RegistryClient(
      scheme=registry_scheme, verify=registry_verify_tls, auth_file=registry_auth_file)

  def find(self, tag):
    return self.registry.find(tag)

  def release(self, image):
    # Only the registry owns the finished image; no local Docker image exists.
    pass

  def _job(self, name, tag):
    from docker.utils import parse_bytes
    host = tag.split('/', 1)[0]
    config = f'[registry.{json.dumps(host)}]\n'
    if self.registry.scheme == 'http':
      config += '  http = true\n'
    if self.registry.verify is False:
      config += '  insecure = true\n'
    if isinstance(self.registry.verify, str):
      config += '  ca = ["/run/autograder-build/registry-ca.pem"]\n'
    data = {'buildkitd.toml': config, 'config.json': self.registry.docker_config()}
    if isinstance(self.registry.verify, str):
      data['registry-ca.pem'] = Path(self.registry.verify).read_text()
    if sum(len(value.encode()) for value in data.values()) > 900 * 1024:
      raise ImageBuildError('Build registry configuration exceeds the 900 KiB Secret limit')
    limits = {'memory': str(parse_bytes(self.memory_limit)),
              'cpu': str(Decimal(self.nano_cpus) / Decimal(1_000_000_000)),
              'ephemeral-storage': self.storage_limit}
    spec = {
      'restartPolicy': 'Never', 'automountServiceAccountToken': False,
      'enableServiceLinks': False,
      'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000,
                          'runAsGroup': 1000, 'fsGroup': 1000},
      'imagePullSecrets': [{'name': value} for value in self.cluster.image_pull_secrets],
      'initContainers': [{
        'name': 'upload-context', 'image': self.builder_image,
        'command': ['/bin/sh', '-ec', 'mkdir -p /workspace/context; '
                    'while [ ! -f /workspace/.uploaded ]; do sleep 1; done'],
        'securityContext': {'allowPrivilegeEscalation': False,
                            'capabilities': {'drop': ['ALL']},
                            'seccompProfile': {'type': 'RuntimeDefault'}},
        'resources': {'requests': dict(limits), 'limits': dict(limits)},
        'volumeMounts': [{'name': 'context', 'mountPath': '/workspace'}],
      }],
      'containers': [{
        'name': 'buildkit', 'image': self.builder_image,
        'command': ['buildctl-daemonless.sh'],
        'args': ['build', '--frontend', 'dockerfile.v0',
                 '--local', 'context=/workspace/context',
                 '--local', 'dockerfile=/workspace/context',
                 '--output', f'type=image,name={tag},push=true', '--progress', 'plain'],
        'env': [{'name': 'BUILDKITD_FLAGS', 'value': '--oci-worker-no-process-sandbox '
                 '--config /run/autograder-build/buildkitd.toml'},
                {'name': 'DOCKER_CONFIG', 'value': '/run/autograder-build'}],
        # Rootless BuildKit still needs user namespaces and mount syscalls.
        # This is deliberately separate from restricted student grading Pods.
        'securityContext': {'allowPrivilegeEscalation': True,
                            'seccompProfile': {'type': 'Unconfined'},
                            'appArmorProfile': {'type': 'Unconfined'}},
        'resources': {'requests': dict(limits), 'limits': dict(limits)},
        'volumeMounts': [{'name': 'context', 'mountPath': '/workspace', 'readOnly': True},
                        {'name': 'buildkit', 'mountPath': '/home/user/.local/share/buildkit'},
                        {'name': 'registry', 'mountPath': '/run/autograder-build', 'readOnly': True}],
      }],
      'volumes': [{'name': 'context', 'emptyDir': {'sizeLimit': self.storage_limit}},
                  {'name': 'buildkit', 'emptyDir': {'sizeLimit': self.storage_limit}},
                  {'name': 'registry', 'secret': {'secretName': name}}],
    }
    return ({'apiVersion': 'batch/v1', 'kind': 'Job',
      'metadata': {'name': name, 'labels': {'app.kubernetes.io/name': 'autograder-build'}},
      'spec': {'backoffLimit': 0, 'ttlSecondsAfterFinished': 3600,
        'activeDeadlineSeconds': self.startup_timeout + self.upload_timeout + self.build_timeout + 30,
        'template': {'metadata': {'labels': {'app.kubernetes.io/name': 'autograder-build'}},
                     'spec': spec}}}, data)

  def build(self, context_path, tag):
    root = Path(context_path)
    if not (root / 'Dockerfile').is_file():
      raise ImageBuildError('Build context must contain a Dockerfile')
    name = 'autograder-build-' + uuid.uuid4().hex
    job, data = self._job(name, tag)
    self.cluster._connect()
    core, batch, namespace = self.cluster.core_api, self.cluster.batch_api, self.cluster.namespace
    operation = 'create build Job'
    log.info('Submitting temporary image build Job %s/%s for %s', namespace, name, tag)
    try:
      created = batch.create_namespaced_job(namespace, job, _request_timeout=API_TIMEOUT)
      operation = 'create build registry Secret'
      core.create_namespaced_secret(namespace, {'apiVersion': 'v1', 'kind': 'Secret',
        'metadata': {'name': name, 'ownerReferences': [{'apiVersion': 'batch/v1',
          'kind': 'Job', 'name': name, 'uid': created.metadata.uid}]},
        'type': 'Opaque', 'stringData': data}, _request_timeout=API_TIMEOUT)
      operation = 'wait for context upload Pod'
      pod_name = self._wait(name, upload=True)
      operation = 'upload build context using pods/exec'
      # Contexts are streamed from disk instead of being embedded in API objects.
      with tempfile.TemporaryFile() as archive:
        with tarfile.open(fileobj=archive, mode='w:gz', dereference=False) as tar:
          for path in sorted(root.iterdir()):
            tar.add(path, arcname=path.name)
        size = archive.tell()
        log.info('Uploading %.1f MiB compressed build context to %s/%s', size / 1048576, namespace, pod_name)
        archive.seek(0)
        self._exec(pod_name, ['/bin/sh', '-ec', 'mkdir -p /workspace/context; '
                              f'head -c {size} | tar -xzf - -C /workspace/context'], archive)
      self._exec(pod_name, ['touch', '/workspace/.uploaded'])
      operation = 'wait for BuildKit and registry push'
      self._wait(name)
      # Do not hand grading an image whose push did not become visible.
      if self.registry.find(tag) is None:
        raise ImageBuildError('Build Job completed but its image manifest is absent from the registry')
      log.info('Image built and pushed successfully: %s', tag)
      return tag
    except Exception as error:
      if getattr(error, 'status', None) is not None:
        raise ImageBuildError(f'Kubernetes could not {operation} (HTTP {error.status}); '
                              f'check build namespace {namespace} permissions and admission policy') from None
      raise
    finally:
      self.cluster._delete(batch.delete_namespaced_job, name, 'build Job', propagation_policy='Background')
      self.cluster._delete(core.delete_namespaced_secret, name, 'build registry Secret')

  def _exec(self, pod_name, command, source=None):
    if self.exec_stream is None:
      from kubernetes.stream import stream
      self.exec_stream = stream
    connection = self.exec_stream(self.cluster.core_api.connect_get_namespaced_pod_exec,
      pod_name, self.cluster.namespace, container='upload-context', command=command,
      stdin=source is not None, stdout=True, stderr=True, tty=False,
      _preload_content=False, _request_timeout=API_TIMEOUT)
    deadline = time.monotonic() + self.upload_timeout
    try:
      connection.sock.settimeout(20)
      if source is not None:
        while chunk := source.read(64 * 1024):
          if time.monotonic() >= deadline or not connection.is_open():
            raise ImageBuildError('Build context upload timed out or disconnected')
          connection.write_stdin(chunk)
          connection.update(timeout=0)
          connection.read_stdout(timeout=0)
          connection.read_stderr(timeout=0)
      while connection.is_open():
        if time.monotonic() >= deadline:
          raise ImageBuildError('Build context upload command timed out')
        connection.update(timeout=1)
        connection.read_stdout(timeout=0)
        connection.read_stderr(timeout=0)
      if connection.returncode != 0:
        raise ImageBuildError('Build context upload command failed; check Pod storage and permissions')
    finally:
      connection.close()

  def _wait(self, name, upload=False):
    deadline = time.monotonic() + (self.startup_timeout if upload else self.build_timeout)
    detail = 'waiting for Pod scheduling'
    core, batch, namespace = self.cluster.core_api, self.cluster.batch_api, self.cluster.namespace
    while True:
      job = batch.read_namespaced_job(name, namespace, _request_timeout=API_TIMEOUT)
      pods = core.list_namespaced_pod(namespace, label_selector='job-name=' + name,
                                      _request_timeout=API_TIMEOUT).items
      for pod in pods:
        status = pod.status
        states = (getattr(status, 'init_container_statuses', None) or []) + (status.container_statuses or [])
        for item in states:
          state = item.state
          if state.waiting:
            detail = state.waiting.reason
          if state.terminated and state.terminated.exit_code != 0:
            self._log_failure(pod.metadata.name, item.name)
            raise ImageBuildError(f'Build container {item.name} failed: {state.terminated.reason} '
                                  f'(exit {state.terminated.exit_code}); Job {namespace}/{name}')
          if upload and item.name == 'upload-context' and state.running:
            return pod.metadata.name
        if status.phase == 'Failed':
          raise ImageBuildError(f'Build Pod failed: {status.reason}; Job {namespace}/{name}')
        if not upload and status.phase == 'Succeeded':
          return pod.metadata.name
      if job.status.failed:
        reasons = ', '.join(item.reason or item.type for item in job.status.conditions or [])
        raise ImageBuildError(f'Build Job failed: {reasons}; Job {namespace}/{name}')
      if time.monotonic() >= deadline:
        phase = 'startup' if upload else 'execution'
        raise ImageBuildError(f'Build Job {phase} timed out ({detail}); Job {namespace}/{name}')
      time.sleep(self.poll_interval)

  def _log_failure(self, pod_name, container):
    try:
      response = self.cluster.core_api.read_namespaced_pod_log(pod_name, self.cluster.namespace,
        container=container, tail_lines=80, limit_bytes=16384, _preload_content=False,
        _request_timeout=API_TIMEOUT)
      try:
        content = response.data.decode(errors='replace')
      finally:
        response.close()
        response.release_conn()
      log.error('BuildKit failure logs for %s/%s:\n%s', self.cluster.namespace, pod_name, content)
    except Exception:
      log.warning('Could not collect failed build logs for %s/%s', self.cluster.namespace, pod_name)
