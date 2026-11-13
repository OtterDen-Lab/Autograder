"""Cooldown/publishing contracts and opt-in tests against real PostgreSQL."""
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Autograder.coordination import (
  AssignmentLease, CoordinationError, PostgresCoordinator, cooldown_seconds,
)
from test_assignment_finalize import DummyAssignment, DummyLmsAssignment, _make_submission


@pytest.mark.parametrize('value', [True, -1, '5m', float('nan'), float('inf')])
def test_reject_invalid_cooldown(value):
  with pytest.raises(ValueError, match='grading_cooldown'):
    cooldown_seconds(value)


def test_panopto_cutoff_uses_course_push_but_respects_assignment_checkpoint():
  from datetime import datetime, timedelta, timezone
  now = datetime(2026, 11, 13, tzinfo=timezone.utc)
  store = PostgresCoordinator('unused')
  store._query = Mock(return_value=[(now, now - timedelta(minutes=1),
                                    now - timedelta(hours=2), now - timedelta(days=1))])
  started, cutoff = store.panopto_scan_window(1, 2)
  assert started == now
  assert cutoff == now - timedelta(hours=2, minutes=15)
  assert store._query.call_args.args[1] == ('1', '1', '2', '1', '2')


def test_panopto_cutoff_falls_back_to_full_scan_for_stale_checkpoint():
  from datetime import datetime, timedelta, timezone
  now = datetime(2026, 11, 13, tzinfo=timezone.utc)
  store = PostgresCoordinator('unused')
  store._query = Mock(return_value=[(now, now - timedelta(minutes=1),
                                    now - timedelta(days=1), now - timedelta(days=8))])
  assert store.panopto_scan_window(1, 2) == (now, None)


@pytest.mark.parametrize("database_student_id", ["1", b"1", 1])
def test_filter_keeps_text_batch_consistent(database_student_id):
  store = Mock(lease_seconds=300)
  store._query.side_effect = [[('token',)], [(database_student_id,)]]
  lease = AssignmentLease(store, '10', '20', 'token')
  submissions = [_make_submission(1), _make_submission(2)]
  assignment = SimpleNamespace(submissions=submissions, submission_data=[
    {'submission_obj': s, 'student_id': s.student.user_id} for s in submissions])
  lease.filter_submissions(assignment, 300)
  assert assignment.submissions == [submissions[1]]
  assert assignment.cooldown_deferred_count == 1
  assert [r['student_id'] for r in assignment.submission_data] == [2]


def test_lost_lease_cannot_resume():
  store = Mock(lease_seconds=300)
  store._query.return_value = []
  lease = AssignmentLease(store, '10', '20', 'token')
  with pytest.raises(CoordinationError):
    lease.check()
  store._query.reset_mock()
  with pytest.raises(CoordinationError):
    lease.check()
  store._query.assert_not_called()


def test_publish_records_only_successful_canvas_updates():
  lms = DummyLmsAssignment(points_possible=100, fail_user_ids={2}, exception_user_ids={3})
  assignment = DummyAssignment(lms_assignment=lms)
  assignment.submissions = [_make_submission(i) for i in (1, 2, 3)]
  assignment.coordination_lease = Mock()
  assignment.grading_cooldown = 300
  result = assignment.finalize(push=True)
  assignment.coordination_lease.succeeded.assert_called_once_with(1)
  assert result['push_succeeded'] == 1
  assert result['push_failed'] == 2


def test_lost_lease_prevents_canvas_mutation():
  lms = DummyLmsAssignment(points_possible=100)
  assignment = DummyAssignment(lms_assignment=lms)
  assignment.submissions = [_make_submission(1)]
  assignment.coordination_lease = Mock()
  assignment.coordination_lease.check.side_effect = CoordinationError('lost')
  with pytest.raises(CoordinationError):
    assignment.finalize(push=True, clobber_feedback=True)
  assert lms.push_calls == []


@pytest.mark.parametrize('push,cooldown', [(False, 300), (True, None)])
def test_no_success_tracking_when_disabled(push, cooldown):
  assignment = DummyAssignment(lms_assignment=DummyLmsAssignment(points_possible=100))
  assignment.submissions = [_make_submission(1)]
  assignment.coordination_lease = Mock()
  assignment.grading_cooldown = cooldown
  assignment.finalize(push=push)
  assignment.coordination_lease.succeeded.assert_not_called()


def test_success_record_failure_stops_remaining_publishes():
  lms = DummyLmsAssignment(points_possible=100)
  assignment = DummyAssignment(lms_assignment=lms)
  assignment.submissions = [_make_submission(1), _make_submission(2)]
  assignment.coordination_lease = Mock()
  assignment.grading_cooldown = 300
  assignment.coordination_lease.succeeded.side_effect = CoordinationError('offline')
  with pytest.raises(CoordinationError):
    assignment.finalize(push=True)
  assert lms.push_calls == [1]


@pytest.fixture
def postgres():
  dsn = os.getenv('AUTOGRADER_TEST_DATABASE_URL')
  if not dsn:
    pytest.skip('Set AUTOGRADER_TEST_DATABASE_URL to a disposable PostgreSQL database')
  store = PostgresCoordinator(dsn)
  store.migrate()
  store.migrate()  # migration is repeatable
  # Unique assignment keys avoid touching other tests or existing records.
  import uuid
  course = 'test-' + str(uuid.uuid4())
  yield store, course
  store._query('DELETE FROM autograder_student_successes WHERE course_id = %s', (course,))
  store._query('DELETE FROM autograder_assignment_leases WHERE course_id = %s', (course,))


def test_postgres_concurrent_claims_and_assignment_independence(postgres):
  store, course = postgres
  with ThreadPoolExecutor(max_workers=8) as pool:
    claims = list(pool.map(lambda _: store.claim(course, '1'), range(8)))
  winners = [claim for claim in claims if claim is not None]
  assert len(winners) == 1
  other = store.claim(course, '2')
  assert other is not None
  winners[0].close()
  replacement = store.claim(course, '1')
  assert replacement is not None
  replacement.close()
  other.close()


def test_postgres_expired_owner_cannot_renew_record_or_release_successor(postgres):
  store, course = postgres
  old = store.claim(course, '1')
  store._query("UPDATE autograder_assignment_leases SET expires_at = clock_timestamp() - interval '1 second' WHERE course_id = %s", (course,))
  new = store.claim(course, '1')
  assert new is not None
  with pytest.raises(CoordinationError):
    old.check()
  with pytest.raises(CoordinationError):
    old.succeeded(1)
  old.close()
  new.check()
  new.close()


@pytest.mark.parametrize("client_encoding", ["UTF8", "SQL_ASCII"])
def test_postgres_student_cooldown_and_assignment_isolation(postgres, monkeypatch, client_encoding):
  monkeypatch.setenv("PGCLIENTENCODING", client_encoding)
  store, course = postgres
  lease = store.claim(course, '1')
  lease.succeeded(1)
  assignment = SimpleNamespace(submissions=[_make_submission(1), _make_submission(2)])
  lease.filter_submissions(assignment, 300)
  assert [s.student.user_id for s in assignment.submissions] == [2]
  store._query("UPDATE autograder_student_successes SET last_succeeded_at = clock_timestamp() - interval '301 seconds' WHERE course_id = %s", (course,))
  assignment.submissions = [_make_submission(1)]
  lease.filter_submissions(assignment, 300)
  assert len(assignment.submissions) == 1
  lease.close()


def test_cooldown_survives_grader_settings_validation(monkeypatch):
  from Autograder import config_models
  from Autograder.registry import GraderRegistry
  grader = SimpleNamespace(normalize_settings=lambda settings, context: dict(settings))
  monkeypatch.setattr(GraderRegistry, 'get_class', lambda name: grader)
  assert config_models.normalize_grader_settings('example', {'grading_cooldown': 300}, 'test') == {'grading_cooldown': 300}
  assert config_models.normalize_grader_settings('example', {'grading_cooldown': None}, 'test') == {}


@pytest.mark.parametrize('mode', ['busy', 'offline', 'missing_config'])
def test_executor_does_not_start_assignment_without_coordination(monkeypatch, mode):
  from Autograder.orchestration import executor
  from Autograder.config_models import AssignmentRunRequest
  coordinator = Mock()
  coordinator.claim.return_value = None
  if mode == 'offline':
    coordinator.claim.side_effect = CoordinationError('offline')
  monkeypatch.setattr(executor, 'coordinator_from_environment',
                      lambda: None if mode == 'missing_config' else coordinator)
  monkeypatch.setattr(executor, 'get_active_grader_compatibility',
                      lambda **kwargs: ({'ProgrammingAssignment'}, {'ProgrammingAssignment': {'template-grader'}}))
  course = Mock()
  request = AssignmentRunRequest(
    course=course, course_name='test', course_id=10, assignment_id=20,
    assignment_type='programming', assignment_kind='ProgrammingAssignment',
    grader_name='template-grader', settings={'grading_cooldown': 300},
    repo_path=None, assignment_name=None, args=SimpleNamespace(),
    push_grades=True, slack_channel=None)
  result = executor.grade_single_assignment(request)
  course.get_assignment.assert_not_called()
  if mode == 'busy':
    assert result['skipped_reason'] == 'assignment_claimed'
    from Autograder.schedule_state import ScheduleStateManager
    assert not ScheduleStateManager._result_completes_schedule(result)
  else:
    assert not result['success']


def test_heartbeat_failure_marks_lease_lost():
  store = Mock(lease_seconds=0.03)
  store._query.side_effect = CoordinationError('offline')
  lease = AssignmentLease(store, '10', '20', 'token')
  lease.start()
  try:
    assert lease._lost.wait(2)
    with pytest.raises(CoordinationError):
      lease.check()
  finally:
    store._query.side_effect = None
    lease.close()


def test_postgres_heartbeat_keeps_long_running_assignment_claimed(postgres):
  store, course = postgres
  store.lease_seconds = 0.6
  lease = store.claim(course, '1')
  import threading
  renewed = threading.Event()
  original_check = lease.check
  def check():
    original_check()
    renewed.set()
  lease.check = check
  lease.start()
  try:
    assert renewed.wait(2)
    import time
    time.sleep(0.7)
    assert store.claim(course, '1') is None
    lease.check()
  finally:
    lease.close()


@pytest.mark.parametrize('fail_prepare', [False, True])
def test_executor_holds_lease_across_pipeline_and_releases_on_error(monkeypatch, fail_prepare):
  from contextlib import nullcontext
  from Autograder.orchestration import executor
  from Autograder.config_models import AssignmentRunRequest
  from Autograder.reporting.contracts import PrepareStageResult, GradeStageResult, PublishStageResult
  lease = Mock()
  coordinator = Mock()
  coordinator.claim.return_value = lease
  monkeypatch.setattr(executor, 'coordinator_from_environment', lambda: coordinator)
  monkeypatch.setattr(executor, 'get_active_grader_compatibility',
                      lambda **kwargs: ({'ProgrammingAssignment'}, {'ProgrammingAssignment': {'template-grader'}}))
  assignment = SimpleNamespace()
  monkeypatch.setattr(executor.AssignmentRegistry, 'create', lambda *a, **k: nullcontext(assignment))
  # The grader instance itself must carry context and cleanup methods.
  grader_context = Mock()
  grader_context.__enter__ = Mock(return_value=grader_context)
  grader_context.__exit__ = Mock(return_value=False)
  monkeypatch.setattr(executor.GraderRegistry, 'create', lambda *a, **k: grader_context)
  def prepare(*args):
    lease.start.assert_called_once()
    lease.close.assert_not_called()
    assert assignment.coordination_lease is lease
    assert assignment.grading_cooldown == 300
    if fail_prepare:
      raise RuntimeError('prepare failed')
    return PrepareStageResult(needed_preparation=True, submission_count=1, has_submissions=True)
  def grade(*args, **kwargs):
    lease.close.assert_not_called()
    return GradeStageResult(submission_count=1, graded_count=1)
  def publish(*args, **kwargs):
    lease.close.assert_not_called()
    return PublishStageResult(finalized=True, finalize_summary={'push_enabled': True})
  monkeypatch.setattr(executor, 'run_prepare_stage', prepare)
  monkeypatch.setattr(executor, 'run_grade_stage', grade)
  monkeypatch.setattr(executor, 'run_publish_stage', publish)
  course = Mock()
  course.get_assignment.return_value.name = 'Example'
  request = AssignmentRunRequest(
    course=course, course_name='test', course_id=10, assignment_id=20,
    assignment_type='programming', assignment_kind='ProgrammingAssignment',
    grader_name='template-grader', settings={'grading_cooldown': 300},
    repo_path=None, assignment_name=None,
    args=SimpleNamespace(do_regrade=False), push_grades=True, slack_channel=None)
  result = executor.grade_single_assignment(request)
  assert result['success'] is (not fail_prepare)
  lease.close.assert_called_once()


def test_claimed_assignments_are_reported_as_skipped(caplog):
  from Autograder.reporting.reports import print_results_summary
  import logging
  with caplog.at_level(logging.INFO):
    print_results_summary([{'success': True, 'assignment_id': 20,
                            'skipped_reason': 'assignment_claimed'}])
  assert '0 successful, 0 failed, 1 skipped' in caplog.text


@pytest.mark.parametrize('remaining,deferred,pushed,failed,ungraded,complete,advance', [
  (0, 0, 0, 0, 0, True, False),  # No work is not a successful publish.
  (0, 2, 0, 0, 0, True, False),  # All students are cooling down.
  (1, 1, 1, 0, 0, True, False),  # A push cannot consume another student's deferred work.
  (1, 0, 0, 0, 0, True, False),  # Canvas no-op / idempotency skip.
  (1, 0, 1, 0, 0, True, True),   # Confirmed push after a complete scan.
  (2, 0, 1, 1, 0, True, False),  # Partial publish failure.
  (2, 0, 1, 0, 1, True, False),  # Missing grading feedback.
  (1, 0, 1, 0, 0, False, False), # Incomplete Panopto scan.
])
def test_panopto_checkpoint_requires_push_without_deferred_work(
    monkeypatch, remaining, deferred, pushed, failed, ungraded, complete, advance):
  from contextlib import nullcontext
  from Autograder.orchestration import executor
  from Autograder.config_models import AssignmentRunRequest
  from Autograder.reporting.contracts import GradeStageResult, PublishStageResult
  from datetime import datetime, timezone
  store = Mock()
  lease = store.claim.return_value
  started = datetime(2026, 11, 13, tzinfo=timezone.utc)
  store.panopto_scan_window.return_value = (started, None)
  assignment = SimpleNamespace(submissions=[], panopto_scan_complete=complete,
                               panopto_scan_was_full=True)
  def prepare(**kwargs):
    assignment.submissions = [_make_submission(i) for i in range(remaining + deferred)]
  assignment.prepare = prepare
  def filter_submissions(current, cooldown):
    current.cooldown_deferred_count = deferred
    current.submissions = current.submissions[:remaining]
  lease.filter_submissions.side_effect = filter_submissions
  grader = Mock()
  grader.assignment_needs_preparation.return_value = True
  grader.__enter__ = Mock(return_value=grader)
  grader.__exit__ = Mock(return_value=False)
  monkeypatch.setattr(executor, 'coordinator_from_environment', lambda: store)
  monkeypatch.setattr(executor, 'get_active_grader_compatibility',
    lambda **kwargs: ({'ExternalToolAssignment'}, {'ExternalToolAssignment': {'panopto-watch-grader'}}))
  monkeypatch.setattr(executor.AssignmentRegistry, 'create', lambda *a, **k: nullcontext(assignment))
  monkeypatch.setattr(executor.GraderRegistry, 'create', lambda *a, **k: grader)
  monkeypatch.setattr(executor, 'run_grade_stage', lambda *a, **k: GradeStageResult(
    submission_count=remaining, graded_count=pushed))
  monkeypatch.setattr(executor, 'run_publish_stage', lambda *a, **k: PublishStageResult(
    finalized=True, finalize_summary={'push_enabled': True, 'push_succeeded': pushed,
      'push_failed': failed, 'push_skipped_ungraded': ungraded}))
  course = Mock()
  course.get_assignment.return_value.name = 'Video'
  request = AssignmentRunRequest(
    course=course, course_name='test', course_id=1, assignment_id=2,
    assignment_type='video', assignment_kind='ExternalToolAssignment',
    grader_name='panopto-watch-grader', settings={'grading_cooldown': 21600},
    repo_path=None, assignment_name=None,
    args=SimpleNamespace(do_regrade=False, limit=None, student_id=None, test=False),
    push_grades=True, slack_channel=None)
  result = executor.grade_single_assignment(request)
  assert result['success']
  assert store.complete_panopto_scan.called is advance
  if advance:
    store.complete_panopto_scan.assert_called_once_with(1, 2, started, True)
  lease.close.assert_called_once()
