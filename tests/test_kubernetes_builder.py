"""Temporary image builds and registry cache semantics without a live cluster."""
import base64
import io
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import requests
from urllib3.response import HTTPResponse

from Autograder import config_models
from Autograder.exceptions import ImageBuildError
from Autograder.graders.template_grader import TemplateGrader
from Autograder.runners.kubernetes_builder import KubernetesImageBuilder
from Autograder.runners.registry import RegistryClient


IMAGE = 'registry.example:5000/course:commit'


def response(status, data, headers=None):
  result = Mock(status_code=status, headers=headers or {})
  result.json.return_value = data
  return result


@pytest.mark.parametrize('status,data,expected', [
  (200, {'schemaVersion': 2, 'layers': []}, IMAGE),
  (404, {'errors': [{'code': 'MANIFEST_UNKNOWN'}]}, None),
  (404, {'errors': [{'code': 'NAME_UNKNOWN'}]}, None),
])
def test_manifest_lookup_without_docker(status, data, expected):
  session = Mock()
  session.get.return_value = response(status, data)
  registry = RegistryClient(session=session)
  assert registry.find(IMAGE) == expected
  assert session.get.call_args.args[0] == 'https://registry.example:5000/v2/course/manifests/commit'
  session.get.return_value.close.assert_called_once()


@pytest.mark.parametrize('status,data', [
  (404, {}), (404, {'errors': [{'code': 'UNKNOWN'}]}),
  (403, {'errors': [{'code': 'DENIED'}]}), (502, {}), (200, {}),
])
def test_proxy_auth_and_invalid_manifest_are_not_cache_misses(status, data):
  session = Mock()
  session.get.return_value = response(status, data)
  with pytest.raises(ImageBuildError):
    RegistryClient(session=session).find(IMAGE)


def test_registry_connection_error_hides_request_secrets():
  session = Mock()
  session.get.side_effect = requests.ConnectionError('https://user:password@host')
  with pytest.raises(ImageBuildError) as error:
    RegistryClient(session=session).find(IMAGE)
  assert 'password' not in str(error.value)


def test_bearer_challenge_uses_scoped_token_and_closes_responses(tmp_path):
  auth = tmp_path / 'config.json'
  auth.write_text(json.dumps({'auths': {'registry.example:5000': {
    'auth': base64.b64encode(b'user:password').decode()}}}))
  session = Mock()
  denied = response(401, {}, {'WWW-Authenticate':
    'Bearer realm="https://auth.example/token",service="registry"'})
  token = response(200, {'token': 'test-token'})
  manifest = response(200, {'schemaVersion': 2})
  session.get.side_effect = [denied, token, manifest]
  registry = RegistryClient(session=session, auth_file=str(auth))
  assert registry.find(IMAGE) == IMAGE
  token_call = session.get.call_args_list[1]
  assert token_call.kwargs['auth'] == ('user', 'password')
  assert token_call.kwargs['params']['scope'] == 'repository:course:pull'
  assert session.get.call_args_list[2].kwargs['headers']['Authorization'] == 'Bearer test-token'
  for item in (denied, token, manifest):
    item.close.assert_called_once()


def state(name='upload-context', running=False, terminated=None, waiting=None):
  return NS(name=name, state=NS(running=NS() if running else None,
                              terminated=terminated, waiting=waiting))


def pod(phase, states):
  return NS(metadata=NS(name='build-pod'), status=NS(phase=phase, reason=None,
    init_container_statuses=states if phase == 'Pending' else [],
    container_statuses=states if phase != 'Pending' else []))


def builder():
  core, batch = Mock(), Mock()
  batch.create_namespaced_job.return_value = NS(metadata=NS(uid='build-uid'))
  batch.read_namespaced_job.return_value = NS(status=NS(failed=0))
  core.list_namespaced_pod.side_effect = [
    NS(items=[pod('Pending', [state(running=True)])]),
    NS(items=[pod('Succeeded', [])]),
  ]
  registry = RegistryClient(session=Mock())
  registry.find = Mock(return_value=IMAGE)
  backend = KubernetesImageBuilder(core_api=core, batch_api=batch, registry=registry)
  backend._exec = Mock()
  return backend


def context(tmp_path):
  (tmp_path / 'Dockerfile').write_text('FROM scratch\nCOPY data /data\n')
  (tmp_path / 'data').write_bytes(b'build input')
  return str(tmp_path)


def test_complete_build_streams_context_pushes_and_cleans_up(tmp_path):
  import tarfile
  backend = builder()
  uploads = []
  def capture(pod_name, command, source=None):
    if source is not None:
      assert 'head -c ' in command[2]
      with tarfile.open(fileobj=source, mode='r:gz') as tar:
        uploads.extend(tar.getnames())
        assert tar.extractfile('data').read() == b'build input'
  backend._exec.side_effect = capture
  assert backend.build(context(tmp_path), IMAGE) == IMAGE
  assert uploads == ['Dockerfile', 'data']
  job = backend.cluster.batch_api.create_namespaced_job.call_args.args[1]
  spec = job['spec']['template']['spec']
  container = spec['containers'][0]
  assert spec['automountServiceAccountToken'] is False
  assert spec['securityContext']['runAsUser'] == 1000
  assert container['securityContext']['seccompProfile']['type'] == 'Unconfined'
  assert not container['securityContext'].get('privileged', False)
  assert f'type=image,name={IMAGE},push=true' in container['args']
  assert job['spec']['backoffLimit'] == 0
  assert job['spec']['activeDeadlineSeconds'] > backend.build_timeout
  secret = backend.cluster.core_api.create_namespaced_secret.call_args.args[1]
  assert secret['metadata']['ownerReferences'][0]['uid'] == 'build-uid'
  assert 'Dockerfile' not in secret['stringData']  # Large context goes over exec.
  assert backend._exec.call_count == 2
  backend.cluster.batch_api.delete_namespaced_job.assert_called_once()
  backend.cluster.core_api.delete_namespaced_secret.assert_called_once()
  backend.registry.find.assert_called_once_with(IMAGE)
  backend.release(IMAGE)  # No local daemon or cluster resources needed.


@pytest.mark.parametrize('stage', ['job', 'secret', 'upload', 'verify'])
def test_build_failures_clean_up_without_returning_an_image(tmp_path, stage):
  backend = builder()
  if stage == 'job':
    backend.cluster.batch_api.create_namespaced_job.side_effect = RuntimeError('failed')
  elif stage == 'secret':
    backend.cluster.core_api.create_namespaced_secret.side_effect = RuntimeError('failed')
  elif stage == 'upload':
    backend._exec.side_effect = RuntimeError('failed')
  else:
    backend.registry.find.return_value = None
  with pytest.raises((RuntimeError, ImageBuildError)):
    backend.build(context(tmp_path), IMAGE)
  backend.cluster.batch_api.delete_namespaced_job.assert_called_once()
  backend.cluster.core_api.delete_namespaced_secret.assert_called_once()


def test_failed_build_records_logs_then_cleans_up(tmp_path, caplog):
  backend = builder()
  backend.cluster.core_api.list_namespaced_pod.side_effect = [
    NS(items=[pod('Pending', [state(running=True)])]),
    NS(items=[pod('Failed', [state('buildkit', terminated=NS(exit_code=1, reason='Error'))])]),
  ]
  backend.cluster.core_api.read_namespaced_pod_log.return_value = HTTPResponse(body=b'compiler error')
  with pytest.raises(ImageBuildError, match='buildkit failed'):
    backend.build(context(tmp_path), IMAGE)
  assert 'compiler error' in caplog.text
  backend.cluster.batch_api.delete_namespaced_job.assert_called_once()


def test_wait_timeout_reports_image_pull_problem(monkeypatch):
  backend = builder()
  backend.cluster.core_api.list_namespaced_pod.side_effect = None
  backend.cluster.core_api.list_namespaced_pod.return_value = NS(items=[
    pod('Pending', [state(waiting=NS(reason='ImagePullBackOff'))])])
  monkeypatch.setattr('Autograder.runners.kubernetes_builder.time.monotonic', Mock(side_effect=[0, 1000]))
  with pytest.raises(ImageBuildError, match='startup timed out.*ImagePullBackOff'):
    backend._wait('job', upload=True)


def test_tar_upload_uses_binary_chunks_and_waits_for_success():
  backend = builder()
  del backend._exec
  ws = Mock(returncode=0)
  # Two data chunks then the command finishes in the response drain.
  ws.is_open.side_effect = [True, True, True, False]
  backend.exec_stream = Mock(return_value=ws)
  content = b'\xff\x00' * 40_000
  backend._exec('pod', ['tar', '-xzf', '-'], io.BytesIO(content))
  assert b''.join(call.args[0] for call in ws.write_stdin.call_args_list) == content
  assert all(len(call.args[0]) <= 65536 for call in ws.write_stdin.call_args_list)
  ws.close.assert_called_once()
  assert backend.exec_stream.call_args.kwargs['container'] == 'upload-context'
  ws.read_stdout.assert_called_with(timeout=0)


def test_upload_errors_close_websocket():
  backend = builder()
  del backend._exec
  ws = Mock(returncode=1)
  ws.is_open.side_effect = [False]
  backend.exec_stream = Mock(return_value=ws)
  with pytest.raises(ImageBuildError, match='upload command failed'):
    backend._exec('pod', ['touch', '/workspace/.uploaded'])
  ws.close.assert_called_once()


def test_kubernetes_builder_selection_does_not_initialize_docker(monkeypatch):
  monkeypatch.setattr('Autograder.runners.docker.DockerClient',
    Mock(side_effect=AssertionError('No Docker daemon should be initialized')))
  settings = config_models.normalize_grader_settings('template-grader', {
    'execution_backend': 'kubernetes', 'image_build_backend': 'kubernetes',
    'image_registry': 'registry.example:5000/courses',
    'kubernetes': {'kubeconfig': '/tmp/config', 'context': 'grading', 'namespace': 'grading'},
    'kubernetes_build': {'namespace': 'builds', 'memory_limit': '6g'},
  }, 'test')
  grader = TemplateGrader(assignment_name='PA1', **settings)
  assert isinstance(grader.image_builder, KubernetesImageBuilder)
  assert grader.image_builder.cluster.namespace == 'builds'
  assert grader.image_builder.cluster.kubeconfig == '/tmp/config'
  assert grader.image_builder.memory_limit == '6g'
  assert grader.image_registry == 'registry.example:5000/courses'


@pytest.mark.parametrize('settings', [
  {'image_build_backend': 'unknown'},
  {'kubernetes_build': {'namespace': 'builds'}},
  {'image_registry': 'https://registry.example'},
  {'image_build_backend': 'kubernetes', 'kubernetes_build': {'build_timeout_seconds': 0}},
  {'image_build_backend': 'kubernetes', 'kubernetes_build': {'registry_verify_tls': None}},
  {'image_build_backend': 'kubernetes', 'kubernetes_build': {'registry_scheme': 'ftp'}},
])
def test_invalid_builder_settings_fail_validation(settings):
  with pytest.raises(ValueError, match='Config error'):
    config_models.normalize_grader_settings('template-grader', settings, 'test')


def test_registry_tls_and_auth_configuration_are_mounted(tmp_path):
  ca = tmp_path / 'ca.pem'
  ca.write_text('test CA')
  auth = tmp_path / 'config.json'
  auth.write_text('{"auths": {}}')
  backend = KubernetesImageBuilder(registry_verify_tls=str(ca), registry_auth_file=str(auth))
  _, data = backend._job('name', IMAGE)
  assert 'registry-ca.pem' in data
  assert data['registry-ca.pem'] == 'test CA'
  assert data['config.json'] == auth.read_text()
  assert 'ca = [' in data['buildkitd.toml']


def test_context_extractor_finishes_without_closing_websocket_stdin(tmp_path):
  backend = builder()
  source = tmp_path / 'source'
  source.mkdir()
  destination = tmp_path / 'destination'
  def transfer(pod_name, command, archive=None):
    if archive is None:
      return
    shell = command[2].replace('/workspace/context', shlex.quote(str(destination)))
    process = subprocess.Popen(['/bin/sh', '-ec', shell], stdin=subprocess.PIPE,
      stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
      process.stdin.write(archive.read())
      process.stdin.flush()
      # Deliberately keep stdin open, matching the Kubernetes exec WebSocket.
      assert process.wait(timeout=5) == 0
      assert (destination / 'data').read_bytes() == b'build input'
    finally:
      if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
      process.stdin.close()
      process.stdout.close()
      process.stderr.close()
  backend._exec.side_effect = transfer
  assert backend.build(context(source), IMAGE) == IMAGE
