"""Exercise the runner boundary, artifact lifetime, and template workflows."""
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Autograder.graders.docker_graders import DockerGrader, Grader__template_grader
from Autograder.graders.template_grader import TemplateGrader
from Autograder.graders.imagenet_trial_grader import ImageNetTrialGrader
from Autograder.runners import ExecutionInput, ExecutionRequest, ExecutionResult
from Autograder.runners.docker import DockerRunner, DockerImageBuilder


def submission(content=b'int main(void) {}'):
  stream = io.BytesIO(content)
  stream.name = 'student.c'
  return SimpleNamespace(files=[stream])


def test_template_uses_injected_runner_and_detached_results_without_docker(monkeypatch):
  monkeypatch.setattr('Autograder.runners.docker.DockerClient',
                      Mock(side_effect=AssertionError('Docker must not be initialized')))
  runner = Mock()
  runner.run.side_effect = [
    ExecutionResult(0, '', '', {'/tmp/feedback.yaml': 'grade: 75\ncomments: first'}),
    ExecutionResult(0, '', '', {'/tmp/feedback.yaml': 'grade: 95\ncomments: second'})]
  builder = Mock()
  grader = TemplateGrader(assignment_name='PA1', runner=runner, image_builder=builder,
                          student_code_path='src/student.c', memory_limit='2g',
                          nano_cpus=1000000000, command_timeout_seconds=120)
  grader._get_image = Mock(return_value='registry/course:commit')
  upload = submission()
  upload.files[0].seek(3)
  with grader:
    first = grader.grade_submission(upload)
    second = grader.grade_submission(submission(b'second upload'))
  assert (first.percentage_score, second.percentage_score) == (75, 95)
  assert first.comments == 'first' and second.comments == 'second'
  assert upload.files[0].tell() == 3
  request = runner.run.call_args_list[0].args[0]
  assert request.image == 'registry/course:commit'
  assert request.inputs == (ExecutionInput('/repo/programming-assignments/PA1/src/student.c',
                                           b'int main(void) {}'),)
  assert request.timeout_seconds == 120
  assert request.options == {'memory_limit': '2g', 'nano_cpus': 1000000000}
  assert request.output_paths == ('/tmp/feedback.yaml',)
  assert '--PA PA1' in request.command
  builder.release.assert_called_once_with('registry/course:commit')
  grader.cleanup()  # Executor cleanup can run again after the context exits.
  builder.release.assert_called_once()
  assert not isinstance(grader, DockerGrader)
  assert Grader__template_grader is TemplateGrader
  assert not hasattr(grader, 'container')


def test_template_releases_image_after_runner_failure():
  runner = Mock()
  runner.run.side_effect = RuntimeError('remote execution failed')
  builder = Mock()
  grader = TemplateGrader(assignment_name='PA1', runner=runner, image_builder=builder)
  grader._get_image = Mock(return_value='registry/course:commit')
  with pytest.raises(RuntimeError, match='remote execution failed'):
    with grader:
      grader.grade_submission(submission())
  builder.release.assert_called_once_with('registry/course:commit')


@pytest.mark.parametrize('cached', [False, True])
def test_template_registry_image_preparation_keeps_existing_tag_and_build_context(monkeypatch, tmp_path, cached):
  repo = tmp_path / 'source'
  repo.mkdir()
  (repo / 'pyproject.toml').write_text('[project]\nname="sample"\nversion="0.1"\n')
  builder = Mock()
  tag = 'docker-registry.p.zxqw.dev/cst334-123:abcdef123456'
  builder.find.return_value = tag if cached else None
  def build(context_path, tag):
    from pathlib import Path
    assert (Path(context_path) / 'repo' / 'pyproject.toml').is_file()
    dockerfile = (Path(context_path) / 'Dockerfile').read_text()
    assert 'COPY repo /repo' in dockerfile
    assert 'uv sync' in dockerfile
    return tag
  builder.build.side_effect = build
  monkeypatch.setattr('Autograder.graders.template_grader.subprocess.check_output',
                      lambda *args, **kwargs: 'abcdef123456\n')
  grader = TemplateGrader(assignment_name='PA1', source_repo=str(repo), course_name='CST334',
                          course_id=123, image_builder=builder, runner=Mock())
  with grader:
    assert grader.image == tag
  builder.find.assert_called_once_with(tag)
  assert builder.build.called is (not cached)
  builder.release.assert_called_once_with(tag)


def test_docker_runner_collects_outputs_before_stopping_and_preserves_limits(monkeypatch):
  events = []
  container = Mock()
  container.start.side_effect = lambda: events.append('start')
  def copy(inputs):
    assert inputs[0][0].read() == b'input bytes'
    assert inputs[0][1] == '/repo/student.c'
    events.append('copy')
  container.copy_files.side_effect = copy
  container.execute_command.side_effect = lambda *a, **k: (events.append('execute') or (0, b'out', None))
  def read(path):
    assert 'stop' not in events
    events.append('read')
    return 'grade: 100'
  container.read_file.side_effect = read
  container.stop.side_effect = lambda: events.append('stop')
  factory = Mock(return_value=container)
  monkeypatch.setattr('Autograder.runners.docker.DockerContainer', factory)
  client = Mock()
  request = ExecutionRequest('image:tag', 'grade', '/repo',
    inputs=(ExecutionInput('/repo/student.c', b'input bytes'),),
    output_paths=('/tmp/feedback.yaml',), timeout_seconds=10,
    options={'memory_limit': '1g', 'network_disabled': True})
  result = DockerRunner(client).run(request)
  assert events == ['start', 'copy', 'execute', 'read', 'stop']
  assert result.files['/tmp/feedback.yaml'] == 'grade: 100'
  assert (result.return_code, result.stdout, result.stderr) == (0, 'out', '')
  container.execute_command.assert_called_once_with('grade', '/repo', timeout_seconds=10)
  factory.assert_called_once_with(client, 'image:tag', name_prefix='grader',
                                  memory_limit='1g', network_disabled=True)


@pytest.mark.parametrize('stage', ['start', 'copy_files', 'execute_command', 'read_file'])
def test_docker_runner_releases_resources_on_each_failure(monkeypatch, stage):
  container = Mock()
  container.execute_command.return_value = (0, b'', b'')
  getattr(container, stage).side_effect = RuntimeError(stage)
  monkeypatch.setattr('Autograder.runners.docker.DockerContainer', lambda *a, **k: container)
  request = ExecutionRequest('image:tag', 'grade',
    inputs=(ExecutionInput('/repo/file', b'code'),), output_paths=('/tmp/feedback.yaml',))
  with pytest.raises(RuntimeError, match=stage):
    DockerRunner(Mock()).run(request)
  container.stop.assert_called_once()


def test_docker_runner_keeps_concurrent_invocation_handles_separate(monkeypatch):
  barrier = Barrier(2)
  handles = []
  def factory(client, image, **kwargs):
    container = Mock()
    def execute(*args, **kwargs):
      barrier.wait(timeout=5)
      return 0, image.encode(), b''
    container.execute_command.side_effect = execute
    container.read_file.return_value = image
    handles.append(container)
    return container
  monkeypatch.setattr('Autograder.runners.docker.DockerContainer', factory)
  runner = DockerRunner(Mock())
  requests = [ExecutionRequest(image, 'grade', output_paths=('/tmp/result',))
              for image in ('first', 'second')]
  with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(runner.run, requests))
  assert [r.files['/tmp/result'] for r in results] == ['first', 'second']
  assert [r.stdout for r in results] == ['first', 'second']
  assert len(handles) == 2
  for container in handles:
    container.stop.assert_called_once()


def test_docker_image_builder_publishes_and_cleans_up_on_push_failure():
  client = Mock()
  client.push_cached_image.side_effect = RuntimeError('registry failed')
  builder = DockerImageBuilder(client)
  with pytest.raises(RuntimeError, match='registry failed'):
    builder.build('/tmp/context', 'registry/course:commit')
  client.remove_image.assert_called_once_with(client.build_image_from_context.return_value, force=True)
  builder.release('registry/course:commit')
  client.remove_image.assert_called_once()


def test_imagenet_trial_uses_runner_inputs_resources_and_collected_artifacts():
  run_json = json.dumps(dict(status='completed', final_validation_accuracy=.8,
    final_validation_loss=.2, parameter_count=100, elapsed_seconds=30))
  runner = Mock()
  runner.run.return_value = ExecutionResult(0, '', '', {
    '/tmp/trial-output/run.json': run_json,
    '/tmp/trial-output/metrics.csv': 'epoch,accuracy\n1,.8'})
  grader = ImageNetTrialGrader(assignment_name='PA3', runner=runner, image_builder=Mock(),
                              gpu_count=1, network_disabled=True, shm_size='2g')
  grader.image = 'registry/course:commit'
  feedback = grader.grade_submission(submission())
  request = runner.run.call_args.args[0]
  assert request.options['gpu_count'] == 1
  assert request.options['memory_limit'] is None and request.options['nano_cpus'] is None
  assert request.options['shm_size'] == '2g'
  assert request.options['network_disabled'] is True
  assert any(i.path == '/opt/autograder/imagenet_trial_runner.py' for i in request.inputs)
  assert request.workdir == '/repo/programming-assignments/PA3'
  assert 'AUTOGRADER_TRIAL_EPOCHS=15' in request.command
  assert feedback.percentage_score == 100
  assert [a.name for a in feedback.attachments] == ['metrics.csv', 'run.json']


@pytest.mark.skipif(not os.getenv('AUTOGRADER_DOCKER_TEST_IMAGE'),
                    reason='Set AUTOGRADER_DOCKER_TEST_IMAGE to a local shell-capable image')
def test_live_docker_runner_round_trip():
  request = ExecutionRequest(os.environ['AUTOGRADER_DOCKER_TEST_IMAGE'],
    'cat /tmp/student.txt > /tmp/result.txt', inputs=(ExecutionInput('/tmp/student.txt', b'round trip'),),
    output_paths=('/tmp/result.txt',), options={'network_disabled': True})
  result = DockerRunner().run(request)
  assert result.return_code == 0
  assert result.files['/tmp/result.txt'] == 'round trip'


@pytest.mark.parametrize('artifact', [None, 'grade: [invalid YAML'])
def test_template_error_reporting_uses_collected_result_without_live_container(artifact):
  grader = TemplateGrader(assignment_name='PA1', runner=Mock(), image_builder=Mock())
  grader._report_grading_error = Mock()
  result = ExecutionResult(1, 'build output', 'build errors', {'/tmp/feedback.yaml': artifact})
  feedback = grader.score_grading(result)
  assert feedback.percentage_score == 0
  grader._report_grading_error.assert_called_once()
  assert grader._report_grading_error.call_args.args[1] is result
  assert not hasattr(grader, 'container')
