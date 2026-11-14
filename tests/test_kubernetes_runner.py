"""Kubernetes lifecycle tests without cluster credentials or student submissions."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from urllib3.response import HTTPResponse

from Autograder.exceptions import GradingError
from Autograder.graders.template_grader import TemplateGrader
from Autograder import config_models
from Autograder.runners import ExecutionInput, ExecutionRequest
from Autograder.runners.kubernetes import KubernetesRunner
from Autograder.runners import kubernetes_worker


def request(**kwargs):
  values = dict(image='registry.internal/course:commit', command='grade',
                workdir='/repo', inputs=(ExecutionInput('/repo/student.c', b'code'),),
                output_paths=('/tmp/feedback.yaml',), options={'memory_limit': '2g'})
  values.update(kwargs)
  return ExecutionRequest(**values)


def pod(phase='Succeeded', reason=None, exit_code=0):
  return NS(metadata=NS(name='worker-pod'), status=NS(
    phase=phase, reason=reason, container_statuses=[NS(state=NS(
      waiting=None, terminated=NS(reason=reason, exit_code=exit_code)))]))


def runner():
  core, batch = Mock(), Mock()
  batch.create_namespaced_job.return_value = NS(metadata=NS(uid='job-uid'))
  batch.read_namespaced_job_status.return_value = NS(status=NS(failed=0))
  core.list_namespaced_pod.return_value = NS(items=[pod()])
  core.read_namespaced_pod_log.return_value = HTTPResponse(body=json.dumps({
    'return_code': 0, 'stdout': 'complete', 'stderr': '',
    'files': {'/tmp/feedback.yaml': 'grade: 90\nerr: false'}}).encode())
  return KubernetesRunner(namespace='grading', core_api=core, batch_api=batch)


def test_job_round_trip_collects_results_and_cleans_up():
  backend = runner()
  result = backend.run(request())
  assert result.return_code == 0
  assert result.files['/tmp/feedback.yaml'] == 'grade: 90\nerr: false'
  job = backend.batch_api.create_namespaced_job.call_args.args[1]
  name = job['metadata']['name']
  spec = job['spec']['template']['spec']
  container = spec['containers'][0]
  assert container['image'] == 'registry.internal/course:commit'
  assert container['resources']['limits']['memory'] == '2147483648'
  assert container['resources']['requests'] == container['resources']['limits']
  assert container['resources']['limits']['cpu'] == '2'
  assert container['securityContext']['allowPrivilegeEscalation'] is False
  assert spec['automountServiceAccountToken'] is False
  assert spec['enableServiceLinks'] is False
  assert 'env' not in container
  assert job['spec']['backoffLimit'] == 0
  assert job['spec']['ttlSecondsAfterFinished'] == 3600
  secret = backend.core_api.create_namespaced_secret.call_args.args[1]
  assert secret['metadata']['ownerReferences'][0]['uid'] == 'job-uid'
  payload = json.loads(secret['stringData']['request.json'])
  assert payload['inputs'] == [{'path': '/repo/student.c', 'content': 'Y29kZQ=='}]
  backend.batch_api.delete_namespaced_job.assert_called_once()
  backend.core_api.delete_namespaced_secret.assert_called_once()
  assert backend.core_api.delete_namespaced_secret.call_args.args == (name, 'grading')


@pytest.mark.parametrize('phase', ['job', 'secret', 'poll', 'logs'])
def test_api_failures_always_attempt_both_cleanups(phase):
  backend = runner()
  method = {
    'job': backend.batch_api.create_namespaced_job,
    'secret': backend.core_api.create_namespaced_secret,
    'poll': backend.batch_api.read_namespaced_job_status,
    'logs': backend.core_api.read_namespaced_pod_log,
  }[phase]
  method.side_effect = RuntimeError('API unavailable')
  with pytest.raises(RuntimeError, match='API unavailable'):
    backend.run(request())
  backend.batch_api.delete_namespaced_job.assert_called_once()
  backend.core_api.delete_namespaced_secret.assert_called_once()


def test_cleanup_failure_does_not_discard_collected_feedback():
  backend = runner()
  backend.batch_api.delete_namespaced_job.side_effect = RuntimeError('API unavailable')
  assert backend.run(request()).return_code == 0
  backend.core_api.delete_namespaced_secret.assert_called_once()


def test_oom_is_a_grading_error_not_a_zero():
  backend = runner()
  backend.core_api.list_namespaced_pod.return_value = NS(items=[pod('Failed', 'OOMKilled', 137)])
  with pytest.raises(GradingError, match='OOMKilled.*137'):
    backend.run(request())
  backend.core_api.read_namespaced_pod_log.assert_not_called()
  backend.batch_api.delete_namespaced_job.assert_called_once()


def test_image_pull_timeout_reports_reason_and_cleans_up(monkeypatch):
  backend = runner()
  pending = pod('Pending')
  pending.status.container_statuses[0].state.waiting = NS(reason='ImagePullBackOff')
  backend.core_api.list_namespaced_pod.return_value = NS(items=[pending])
  monkeypatch.setattr('Autograder.runners.kubernetes.time.monotonic', Mock(side_effect=[0, 1000]))
  with pytest.raises(GradingError, match='startup timed out.*ImagePullBackOff'):
    backend.run(request())
  backend.core_api.delete_namespaced_secret.assert_called_once()


def test_running_job_gets_command_deadline_and_returns_completed_result(monkeypatch):
  backend = runner()
  backend.core_api.list_namespaced_pod.side_effect = [
    NS(items=[pod('Running')]), NS(items=[pod()])]
  monkeypatch.setattr('Autograder.runners.kubernetes.time.sleep', lambda _: None)
  assert backend.run(request()).stdout == 'complete'


@pytest.mark.parametrize('options', [
  {'mounts': []}, {'network_disabled': True}, {'pids_limit': 256},
  {'seccomp_profile': '/local/profile'}, {'read_only_root_fs': True},
])
def test_unsupported_options_fail_before_any_api_call(options):
  backend = runner()
  with pytest.raises(GradingError):
    backend.run(request(options=options))
  backend.batch_api.create_namespaced_job.assert_not_called()


def test_large_submission_fails_before_cluster_access():
  backend = runner()
  with pytest.raises(GradingError, match='900 KiB'):
    backend.run(request(inputs=(ExecutionInput('/repo/large', b'x' * 800_000),)))
  backend.batch_api.create_namespaced_job.assert_not_called()


@pytest.mark.parametrize('path', ['relative', '/', '/opt/autograder-job/request.json'])
def test_invalid_input_paths_fail_before_cluster_access(path):
  backend = runner()
  with pytest.raises(GradingError, match='paths must be absolute'):
    backend.run(request(inputs=(ExecutionInput(path, b'code'),)))


@pytest.mark.parametrize('content', ['', '{}', '[]', '{truncated',
  '{"return_code": 0, "stdout": "", "stderr": "", "files": {}}'])
def test_missing_or_truncated_results_fail_closed(content):
  backend = runner()
  backend.core_api.read_namespaced_pod_log.return_value = HTTPResponse(body=content.encode())
  with pytest.raises(GradingError, match='malformed, or truncated'):
    backend.run(request())


def test_worker_reported_error_fails_closed():
  backend = runner()
  backend.core_api.read_namespaced_pod_log.return_value = HTTPResponse(
    body=b'{"error": "Grading command timed out"}')
  with pytest.raises(GradingError, match='command timed out'):
    backend.run(request())


def test_backend_config_reaches_kubernetes_without_loading_sdk(monkeypatch):
  monkeypatch.setitem(sys.modules, 'kubernetes', None)
  settings = config_models.normalize_grader_settings('template-grader', {
    'execution_backend': 'kubernetes', 'memory_limit': '2g', 'nano_cpus': 1_000_000_000,
    'command_timeout_seconds': 120,
    'kubernetes': {'kubeconfig': '~/.kube/config', 'namespace': 'grading',
                   'image_pull_secrets': ['registry-access']},
  }, 'test')
  grader = TemplateGrader(assignment_name='PA1', **settings, image_builder=Mock())
  assert isinstance(grader.runner, KubernetesRunner)
  assert grader.runner.namespace == 'grading'
  assert grader.runner.image_pull_secrets == ['registry-access']
  assert grader.runner.kubeconfig == str(Path('~/.kube/config').expanduser())
  assert grader.command_timeout_seconds == 120
  with pytest.raises(GradingError, match='kubernetes extra'):
    grader.runner._connect()


@pytest.mark.parametrize('settings', [
  {'execution_backend': 'unknown'},
  {'kubernetes': {'namespace': 'grading'}},
  {'execution_backend': 'kubernetes', 'kubernetes': {'token': 'do not put credentials in YAML'}},
  {'execution_backend': 'kubernetes', 'kubernetes': {'startup_timeout_seconds': 0}},
  {'execution_backend': 'kubernetes', 'kubernetes': {'poll_interval_seconds': True}},
  {'command_timeout_seconds': -1}, {'nano_cpus': 0},
])
def test_invalid_backend_config_is_rejected(settings):
  with pytest.raises(ValueError, match='Config error'):
    config_models.normalize_grader_settings('template-grader', settings, 'test')


def test_standalone_worker_executes_and_collects_artifacts(tmp_path):
  source = tmp_path / 'input' / 'file.txt'
  output = tmp_path / 'feedback.yaml'
  missing = tmp_path / 'missing.txt'
  output.write_text('stale feedback')
  backend = runner()
  req = request(inputs=(ExecutionInput(str(source), b'uploaded'),), workdir=str(tmp_path),
                command=f'cat "{source}"; echo stderr >&2; printf "grade: 75" > "{output}"; exit 1',
                output_paths=(str(output), str(missing)))
  payload_path = tmp_path / 'request.json'
  payload_path.write_text(backend._payload(req)['request.json'])
  worker = subprocess.run([sys.executable, kubernetes_worker.__file__, str(payload_path)],
                           capture_output=True, text=True, timeout=5)
  result = backend._result(worker.stdout, req.output_paths)
  assert result.return_code == 1
  assert result.stdout == 'uploaded'
  assert result.stderr == 'stderr\n'
  assert result.files == {str(output): 'grade: 75', str(missing): None}


def test_worker_timeout_does_not_return_stale_feedback(tmp_path):
  output = tmp_path / 'feedback.yaml'
  output.write_text('grade: 100')
  payload = {'inputs': [], 'output_paths': [str(output)], 'command': 'sleep 5',
             'workdir': str(tmp_path), 'timeout_seconds': 0.05}
  with pytest.raises(RuntimeError, match='timed out'):
    kubernetes_worker.execute(payload)
  assert not output.exists()


def test_worker_rejects_excessive_output(monkeypatch, tmp_path):
  monkeypatch.setattr(kubernetes_worker, 'MAX_OUTPUT_BYTES', 100)
  payload = {'inputs': [], 'output_paths': [], 'command': 'printf "%0200d" 1',
             'workdir': str(tmp_path), 'timeout_seconds': 2}
  with pytest.raises(RuntimeError, match='output limit'):
    kubernetes_worker.execute(payload)


@pytest.mark.parametrize('context,namespace', [(None, 'first-space'), ('other', 'second-space')])
def test_real_sdk_uses_selected_context_namespace_without_global_changes(tmp_path, context, namespace):
  sdk = pytest.importorskip('kubernetes')
  kubeconfig = tmp_path / 'config'
  kubeconfig.write_text('''apiVersion: v1
kind: Config
current-context: first
clusters:
  - name: test
    cluster: {server: "https://k3.example:6443"}
users:
  - name: test
    user: {token: test-only}
contexts:
  - name: first
    context: {cluster: test, user: test, namespace: first-space}
  - name: other
    context: {cluster: test, user: test, namespace: second-space}
''')
  original_host = sdk.client.Configuration.get_default_copy().host
  backend = KubernetesRunner(kubeconfig=str(kubeconfig), context=context)
  backend._connect()
  try:
    assert backend.namespace == namespace
    assert backend.core_api.api_client.configuration.host == 'https://k3.example:6443'
    assert sdk.client.Configuration.get_default_copy().host == original_host
  finally:
    backend.core_api.api_client.close()


def test_real_sdk_http_round_trip_preserves_json_log_transport(monkeypatch):
  sdk = pytest.importorskip('kubernetes')
  api = sdk.client.ApiClient(sdk.client.Configuration(host='https://k3.example'))
  requests = []
  def http(method, url, **kwargs):
    requests.append((method, url, kwargs))
    if url.endswith('/log'):
      body = {'return_code': 0, 'stdout': 'graded', 'stderr': '',
              'files': {'/tmp/feedback.yaml': 'grade: 100'}}
    elif url.endswith('/pods'):
      body = {'items': [{'metadata': {'name': 'worker'},
                         'status': {'phase': 'Succeeded', 'containerStatuses': []}}]}
    elif '/jobs' in url:
      body = {'metadata': {'name': 'job', 'uid': 'job-uid'}, 'status': {'failed': 0}}
    else:
      body = {'metadata': {'name': 'secret'}}
    return HTTPResponse(body=json.dumps(body).encode(), status=200,
                         headers={'Content-Type': 'application/json'})
  monkeypatch.setattr(api.rest_client, 'request', http)
  backend = KubernetesRunner(namespace='grading', core_api=sdk.client.CoreV1Api(api),
                              batch_api=sdk.client.BatchV1Api(api))
  try:
    result = backend.run(request())
    assert result.stdout == 'graded'
    assert result.files == {'/tmp/feedback.yaml': 'grade: 100'}
    assert any(method == 'DELETE' and '/jobs/' in url for method, url, _ in requests)
    assert any(method == 'DELETE' and '/secrets/' in url for method, url, _ in requests)
  finally:
    api.close()


def test_api_error_does_not_expose_response_body():
  backend = runner()
  class ApiError(Exception):
    status = 403
  backend.core_api.create_namespaced_secret.side_effect = ApiError('sensitive request body')
  with pytest.raises(GradingError, match='HTTP 403') as error:
    backend.run(request())
  assert 'sensitive' not in str(error.value)
  assert error.value.__suppress_context__
