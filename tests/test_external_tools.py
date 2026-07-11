import json

import pytest

from Autograder.external_tools import (
  build_panopto_authorization_url,
  load_panopto_refresh_token,
  normalize_panopto_identifier,
  request_panopto_authorization_code_token,
  request_panopto_access_token,
  request_panopto_refresh_token,
  resolve_panopto_access_token,
)


class _MockResponse:
  def __init__(self, payload):
    self.payload = payload
    self.status_code = 200

  def raise_for_status(self):
    return None

  def json(self):
    return self.payload


class _RecordingSession:
  def __init__(self, payloads):
    if isinstance(payloads, list):
      self.payloads = list(payloads)
    else:
      self.payloads = [payloads]
    self.calls = []

  def post(self, url, data=None, auth=None, headers=None, timeout=None):
    self.calls.append({
      "url": url,
      "data": data,
      "auth": auth,
      "headers": headers,
      "timeout": timeout,
    })
    payload = self.payloads.pop(0)
    return _MockResponse(payload)


class _RecordingGetSession(_RecordingSession):
  def get(self, url, headers=None, timeout=None, params=None):
    self.calls.append({
      "url": url,
      "headers": headers,
      "timeout": timeout,
      "params": params,
    })
    payload = self.payloads.pop(0)
    response = _MockResponse(payload["payload"])
    response.status_code = payload.get("status_code", 200)
    return response


def test_request_panopto_access_token_uses_client_credentials_grant():
  session = _RecordingSession({"access_token": "token-123"})

  token = request_panopto_access_token(
    client_id="client-id",
    client_secret="client-secret",
    token_url="https://csumb.hosted.panopto.com/Panopto/oauth2/connect/token",
    timeout_seconds=12.0,
    session=session,
  )

  assert token.access_token == "token-123"
  assert session.calls == [{
    "url": "https://csumb.hosted.panopto.com/Panopto/oauth2/connect/token",
    "data": {
      "grant_type": "client_credentials",
      "scope": "api",
    },
    "auth": ("client-id", "client-secret"),
    "headers": {
      "Accept": "application/json"
    },
    "timeout": 12.0,
  }]


def test_request_panopto_refresh_token_uses_refresh_grant():
  session = _RecordingSession({
    "access_token": "token-456",
    "refresh_token": "refresh-456",
  })

  token = request_panopto_refresh_token(
    client_id="client-id",
    client_secret="client-secret",
    refresh_token="refresh-123",
    token_url="https://csumb.hosted.panopto.com/Panopto/oauth2/connect/token",
    timeout_seconds=14.0,
    session=session,
  )

  assert token.access_token == "token-456"
  assert token.refresh_token == "refresh-456"
  assert session.calls == [{
    "url": "https://csumb.hosted.panopto.com/Panopto/oauth2/connect/token",
    "data": {
      "grant_type": "refresh_token",
      "refresh_token": "refresh-123",
      "scope": "api",
    },
    "auth": ("client-id", "client-secret"),
    "headers": {
      "Accept": "application/json"
    },
    "timeout": 14.0,
  }]


def test_request_panopto_authorization_code_token_uses_authorization_code_grant():
  session = _RecordingSession({
    "access_token": "token-789",
    "refresh_token": "refresh-789",
  })

  token = request_panopto_authorization_code_token(
    client_id="client-id",
    client_secret="client-secret",
    authorization_code="auth-code-123",
    redirect_uri="http://127.0.0.1:8765/callback",
    token_url="https://csumb.hosted.panopto.com/Panopto/oauth2/connect/token",
    timeout_seconds=13.0,
    session=session,
  )

  assert token.access_token == "token-789"
  assert token.refresh_token == "refresh-789"
  assert session.calls == [{
    "url": "https://csumb.hosted.panopto.com/Panopto/oauth2/connect/token",
    "data": {
      "grant_type": "authorization_code",
      "code": "auth-code-123",
      "redirect_uri": "http://127.0.0.1:8765/callback",
      "scope": "api",
    },
    "auth": ("client-id", "client-secret"),
    "headers": {
      "Accept": "application/json"
    },
    "timeout": 13.0,
  }]


def test_build_panopto_authorization_url_includes_expected_query_params():
  url = build_panopto_authorization_url(
    authorize_url="https://csumb.hosted.panopto.com/Panopto/oauth2/connect/authorize",
    client_id="client-id",
    redirect_uri="http://127.0.0.1:8765/callback",
    scope="api",
    state="state-123",
  )

  assert url.startswith(
    "https://csumb.hosted.panopto.com/Panopto/oauth2/connect/authorize?")
  assert "response_type=code" in url
  assert "client_id=client-id" in url
  assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback" in url
  assert "scope=api" in url
  assert "state=state-123" in url


def test_extract_student_identifier_supports_username():
  class _Student:
    username = "unified\\0abc123"

  from Autograder.external_tools import extract_student_identifier

  assert extract_student_identifier(_Student(), "username") == "unified\\0abc123"


def test_normalize_panopto_identifier_strips_unified_prefix():
  assert normalize_panopto_identifier("unified\\0abc123") == "0abc123"
  assert normalize_panopto_identifier(" unified/0abc123 ") == "0abc123"


def test_resolve_panopto_access_token_uses_client_credentials_env(monkeypatch):
  monkeypatch.setenv("PANOPTO_CLIENT_ID", "client-id")
  monkeypatch.setenv("PANOPTO_CLIENT_SECRET", "client-secret")

  session = _RecordingSession({"access_token": "minted-token"})
  token = resolve_panopto_access_token(
    None,
    None,
    client_id_env="PANOPTO_CLIENT_ID",
    client_secret_env="PANOPTO_CLIENT_SECRET",
    base_url="https://csumb.hosted.panopto.com",
    timeout_seconds=15.0,
    session=session,
  )

  assert token == "minted-token"
  assert session.calls[0]["url"] == (
    "https://csumb.hosted.panopto.com/Panopto/oauth2/connect/token")
  assert session.calls[0]["data"]["scope"] == "api"


def test_resolve_panopto_access_token_uses_refresh_token_and_rotates_file(
    monkeypatch, tmp_path):
  refresh_path = tmp_path / "panopto_refresh_token.json"
  refresh_path.write_text(json.dumps({"refresh_token": "refresh-123"}),
                          encoding="utf-8")
  monkeypatch.setenv("PANOPTO_CLIENT_ID", "client-id")
  monkeypatch.setenv("PANOPTO_CLIENT_SECRET", "client-secret")

  session = _RecordingSession({
    "access_token": "rotated-access-token",
    "refresh_token": "refresh-456",
  })
  token = resolve_panopto_access_token(
    None,
    None,
    client_id_env="PANOPTO_CLIENT_ID",
    client_secret_env="PANOPTO_CLIENT_SECRET",
    refresh_token_path=str(refresh_path),
    base_url="https://csumb.hosted.panopto.com",
    timeout_seconds=15.0,
    session=session,
  )

  assert token == "rotated-access-token"
  assert session.calls[0]["data"]["grant_type"] == "refresh_token"
  assert load_panopto_refresh_token(str(refresh_path)) == "refresh-456"


def test_resolve_panopto_access_token_caches_refresh_results(
    monkeypatch, tmp_path):
  from Autograder import external_tools

  external_tools._PANOPTO_TOKEN_CACHE.clear()
  refresh_path = tmp_path / "panopto_refresh_token.json"
  refresh_path.write_text(json.dumps({"refresh_token": "refresh-123"}),
                          encoding="utf-8")
  monkeypatch.setenv("PANOPTO_CLIENT_ID", "client-id")
  monkeypatch.setenv("PANOPTO_CLIENT_SECRET", "client-secret")

  calls = []

  def fake_request_panopto_refresh_token(**kwargs):
    calls.append(kwargs["refresh_token"])
    return external_tools.PanoptoTokenBundle(
      access_token="cached-access-token",
      refresh_token="refresh-456",
    )

  monkeypatch.setattr(external_tools, "request_panopto_refresh_token",
                      fake_request_panopto_refresh_token)

  token1 = resolve_panopto_access_token(
    None,
    None,
    client_id_env="PANOPTO_CLIENT_ID",
    client_secret_env="PANOPTO_CLIENT_SECRET",
    refresh_token_path=str(refresh_path),
    base_url="https://csumb.hosted.panopto.com",
    timeout_seconds=15.0,
  )
  token2 = resolve_panopto_access_token(
    None,
    None,
    client_id_env="PANOPTO_CLIENT_ID",
    client_secret_env="PANOPTO_CLIENT_SECRET",
    refresh_token_path=str(refresh_path),
    base_url="https://csumb.hosted.panopto.com",
    timeout_seconds=15.0,
  )

  assert token1 == "cached-access-token"
  assert token2 == "cached-access-token"
  assert calls == ["refresh-123"]


def test_fetch_watch_records_treats_404_as_empty_list():
  from Autograder.external_tools import PanoptoWatchClient

  session = _RecordingGetSession([{
    "status_code": 404,
    "payload": {"Error": {"Message": "not found"}},
  }])
  client = PanoptoWatchClient(
    base_url="https://csumb.hosted.panopto.com",
    access_token="token",
    session=session,
  )

  records = client.fetch_watch_records(
    session_id="missing-session",
    path_template="/Panopto/api/v1/sessions/{session_id}/viewers",
    record_identifier_paths=["User.Username"],
    record_percent_paths=["PercentCompleted"],
    record_viewed_seconds_paths=["MostRecentViewPositionInSeconds"],
    record_duration_seconds_paths=[],
  )

  assert records == []


def test_fetch_session_duration_seconds_reads_session_metadata():
  from Autograder.external_tools import PanoptoWatchClient

  session = _RecordingGetSession([{
    "payload": {
      "Session": {
        "DurationSeconds": 3723.5,
      }
    }
  }])
  client = PanoptoWatchClient(
    base_url="https://csumb.hosted.panopto.com",
    access_token="token",
    session=session,
  )

  duration = client.fetch_session_duration_seconds(
    session_id="abc123",
    path_template="/Panopto/api/v1/sessions/{session_id}",
  )

  assert duration == 3723.5
  assert session.calls[0]["url"] == (
    "https://csumb.hosted.panopto.com/Panopto/api/v1/sessions/abc123")


def test_fetch_watch_records_uses_session_duration_for_percent_calculation():
  from Autograder.external_tools import PanoptoWatchClient

  session = _RecordingGetSession([{
    "payload": [
      {
        "User": {"Username": "unified\\student@example.edu"},
        "PercentCompleted": 100.0,
        "MostRecentViewPositionInSeconds": 0.385631,
        "LastViewedDateTime": "2026-07-10T14:31:33.193Z",
      }
    ]
  }, {
    "payload": []
  }])
  client = PanoptoWatchClient(
    base_url="https://csumb.hosted.panopto.com",
    access_token="token",
    session=session,
  )

  records = client.fetch_watch_records(
    session_id="abc123",
    path_template="/Panopto/api/v1/sessions/{session_id}/viewers",
    session_duration_seconds=1200.0,
    record_identifier_paths=["User.Username"],
    record_percent_paths=["PercentCompleted"],
    record_viewed_seconds_paths=["MostRecentViewPositionInSeconds"],
    record_duration_seconds_paths=[],
  )

  assert len(records) == 1
  assert records[0].viewed_seconds == 0.385631
  assert records[0].duration_seconds == 1200.0
  assert records[0].percent_watched == pytest.approx(0.032135916666666664)
  assert records[0].last_viewed_at is not None
  assert records[0].last_viewed_at.isoformat() == "2026-07-10T14:31:33.193000+00:00"
