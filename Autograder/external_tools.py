from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from typing import Any, Optional
from urllib.parse import urlencode, urlparse, parse_qs

import requests

log = logging.getLogger(__name__)


@dataclass
class ExternalWatchRecord:
  user_key: str
  percent_watched: Optional[float] = None
  viewed_seconds: Optional[float] = None
  duration_seconds: Optional[float] = None
  raw: Optional[dict[str, Any]] = None


@dataclass
class PanoptoTokenBundle:
  access_token: str
  refresh_token: Optional[str] = None
  expires_in: Optional[int] = None
  token_type: Optional[str] = None


def _first_path_value(payload: Any, candidate_paths: list[str]) -> Any:
  for path in candidate_paths:
    current = payload
    found = True
    for part in path.split("."):
      if not isinstance(current, dict) or part not in current:
        found = False
        break
      current = current[part]
    if found and current not in (None, ""):
      return current
  return None


def _coerce_float(value: Any) -> Optional[float]:
  if value is None or value == "":
    return None
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


def _normalize_percent(value: Optional[float]) -> Optional[float]:
  if value is None:
    return None
  if 0.0 <= value <= 1.0:
    return value * 100.0
  return max(0.0, min(100.0, value))


def extract_panopto_base_url(panopto_url: str) -> str:
  parsed = urlparse(panopto_url)
  if not parsed.scheme or not parsed.netloc:
    raise ValueError(f"Invalid Panopto URL: {panopto_url}")
  return f"{parsed.scheme}://{parsed.netloc}"


def extract_panopto_session_id(panopto_url: str) -> Optional[str]:
  parsed = urlparse(panopto_url)
  query = parse_qs(parsed.query)
  for key in ("id", "sessionid", "sessionId"):
    values = query.get(key)
    if values and values[0]:
      return values[0]

  path_parts = [part for part in parsed.path.split("/") if part]
  if not path_parts:
    return None

  for i, part in enumerate(path_parts[:-1]):
    if part.lower() in {"pages", "viewer"} and path_parts[i + 1]:
      return path_parts[i + 1]

  return None


def extract_student_identifier(student: Any, attribute: str) -> Optional[str]:
  candidates: list[Any] = [student, getattr(student, "_inner", None)]

  fallback_attrs = {
    "email": ("email", "login_id", "sis_user_id"),
    "login_id": ("login_id", "email", "sis_user_id"),
    "username": ("username", "login_id", "email", "sis_user_id"),
    "sis_user_id": ("sis_user_id", "login_id", "email"),
    "name": ("name",),
    "sortable_name": ("sortable_name", "name"),
  }

  for obj in candidates:
    if obj is None:
      continue
    for attr_name in fallback_attrs.get(attribute, (attribute,)):
      value = getattr(obj, attr_name, None)
      if value not in (None, ""):
        return str(value).strip().lower()
  return None


class PanoptoWatchClient:

  DEFAULT_RECORD_LIST_KEYS = (
    "Results",
    "results",
    "Items",
    "items",
    "Viewers",
    "viewers",
    "Data",
    "data",
  )

  def __init__(self,
               *,
               base_url: str,
               access_token: str,
               timeout_seconds: float = 30.0,
               session: requests.Session | None = None):
    self.base_url = base_url.rstrip("/")
    self.access_token = access_token
    self.timeout_seconds = timeout_seconds
    self.session = session or requests.Session()

  def fetch_watch_records(self,
                          *,
                          session_id: str,
                          path_template: str,
                          record_identifier_paths: list[str],
                          record_percent_paths: list[str],
                          record_viewed_seconds_paths: list[str],
                          record_duration_seconds_paths: list[str]
                          ) -> list[ExternalWatchRecord]:
    url = f"{self.base_url}{path_template.format(session_id=session_id)}"
    response = self.session.get(
      url,
      headers={
        "Authorization": f"Bearer {self.access_token}",
        "Accept": "application/json",
      },
      timeout=self.timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    raw_records = self._extract_record_list(payload)

    records: list[ExternalWatchRecord] = []
    for raw in raw_records:
      if not isinstance(raw, dict):
        continue

      user_key = _first_path_value(raw, record_identifier_paths)
      if user_key in (None, ""):
        continue

      percent_watched = _normalize_percent(
        _coerce_float(_first_path_value(raw, record_percent_paths)))
      viewed_seconds = _coerce_float(
        _first_path_value(raw, record_viewed_seconds_paths))
      duration_seconds = _coerce_float(
        _first_path_value(raw, record_duration_seconds_paths))

      if percent_watched is None and viewed_seconds is not None and duration_seconds:
        percent_watched = _normalize_percent(viewed_seconds / duration_seconds)

      records.append(
        ExternalWatchRecord(
          user_key=str(user_key).strip().lower().replace("unified\\", ""),
          percent_watched=percent_watched,
          viewed_seconds=viewed_seconds,
          duration_seconds=duration_seconds,
          raw=raw,
        ))

    return records

  def _extract_record_list(self, payload: Any) -> list[Any]:
    if isinstance(payload, list):
      return payload

    if not isinstance(payload, dict):
      raise ValueError(
        f"Panopto response must be a JSON list or object, got {type(payload).__name__}")

    for key in self.DEFAULT_RECORD_LIST_KEYS:
      value = payload.get(key)
      if isinstance(value, list):
        return value

    for value in payload.values():
      if isinstance(value, list):
        return value

    raise ValueError(
      "Panopto response did not contain a recognizable viewer record list")


def resolve_panopto_access_token(explicit_token: Optional[str],
                                 token_env: Optional[str],
                                 *,
                                 explicit_client_id: Optional[str] = None,
                                 explicit_client_secret: Optional[str] = None,
                                 client_id_env: Optional[str] = None,
                                 client_secret_env: Optional[str] = None,
                                 explicit_refresh_token: Optional[str] = None,
                                 refresh_token_env: Optional[str] = None,
                                 refresh_token_path: Optional[str] = None,
                                 token_url: Optional[str] = None,
                                 base_url: Optional[str] = None,
                                 scope: str = "api",
                                 timeout_seconds: float = 30.0,
                                 session: requests.Session | None = None) -> str:
  if explicit_token:
    return explicit_token
  if token_env:
    env_value = os.getenv(token_env)
    if env_value:
      return env_value

  client_id = explicit_client_id or _get_optional_env(client_id_env)
  client_secret = explicit_client_secret or _get_optional_env(client_secret_env)
  if client_id and client_secret:
    resolved_token_url = token_url
    if not resolved_token_url:
      if not base_url:
        raise ValueError(
          "Panopto OAuth token URL could not be determined. Set panopto_token_url or panopto_base_url."
        )
      # Inference based on Panopto OAuth deployments; keep configurable.
      resolved_token_url = f"{base_url.rstrip('/')}/Panopto/oauth2/connect/token"
    refresh_token = (explicit_refresh_token or _get_optional_env(refresh_token_env)
                     or load_panopto_refresh_token(refresh_token_path))
    if refresh_token:
      token_bundle = request_panopto_refresh_token(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        token_url=resolved_token_url,
        scope=scope,
        timeout_seconds=timeout_seconds,
        session=session,
      )
      persistable_refresh_token = token_bundle.refresh_token or refresh_token
      if refresh_token_path and persistable_refresh_token:
        save_panopto_refresh_token(refresh_token_path, persistable_refresh_token)
      return token_bundle.access_token

    token_bundle = request_panopto_access_token(
      client_id=client_id,
      client_secret=client_secret,
      token_url=resolved_token_url,
      scope=scope,
      timeout_seconds=timeout_seconds,
      session=session,
    )
    return token_bundle.access_token
  raise ValueError(
    "Panopto credentials are required. Set panopto_access_token, populate "
    "panopto_access_token_env, provide a Panopto refresh token, or provide "
    "panopto_client_id/panopto_client_secret directly or through env vars."
  )


def request_panopto_access_token(*,
                                 client_id: str,
                                 client_secret: str,
                                 token_url: str,
                                 scope: str = "api",
                                 timeout_seconds: float = 30.0,
                                 session: requests.Session | None = None
                                 ) -> PanoptoTokenBundle:
  http = session or requests.Session()
  response = http.post(
    token_url,
    data={
      "grant_type": "client_credentials",
      "scope": scope,
    },
    auth=(client_id, client_secret),
    headers={"Accept": "application/json"},
    timeout=timeout_seconds,
  )
  return _parse_panopto_token_response(response)


def request_panopto_refresh_token(*,
                                  client_id: str,
                                  client_secret: str,
                                  refresh_token: str,
                                  token_url: str,
                                  scope: str = "api",
                                  timeout_seconds: float = 30.0,
                                  session: requests.Session | None = None
                                  ) -> PanoptoTokenBundle:
  http = session or requests.Session()
  response = http.post(
    token_url,
    data={
      "grant_type": "refresh_token",
      "refresh_token": refresh_token,
      "scope": scope,
    },
    auth=(client_id, client_secret),
    headers={"Accept": "application/json"},
    timeout=timeout_seconds,
  )
  return _parse_panopto_token_response(response)


def request_panopto_authorization_code_token(*,
                                             client_id: str,
                                             client_secret: str,
                                             authorization_code: str,
                                             redirect_uri: str,
                                             token_url: str,
                                             scope: str = "api",
                                             timeout_seconds: float = 30.0,
                                             session: requests.Session | None = None
                                             ) -> PanoptoTokenBundle:
  http = session or requests.Session()
  response = http.post(
    token_url,
    data={
      "grant_type": "authorization_code",
      "code": authorization_code,
      "redirect_uri": redirect_uri,
      "scope": scope,
    },
    auth=(client_id, client_secret),
    headers={"Accept": "application/json"},
    timeout=timeout_seconds,
  )
  return _parse_panopto_token_response(response)


def build_panopto_authorization_url(*,
                                    authorize_url: str,
                                    client_id: str,
                                    redirect_uri: str,
                                    scope: str = "api",
                                    state: Optional[str] = None) -> str:
  query = {
    "response_type": "code",
    "client_id": client_id,
    "redirect_uri": redirect_uri,
    "scope": scope,
  }
  if state:
    query["state"] = state
  separator = "&" if "?" in authorize_url else "?"
  return f"{authorize_url}{separator}{urlencode(query)}"


def load_panopto_refresh_token(path: Optional[str]) -> Optional[str]:
  if not path:
    return None
  expanded = os.path.abspath(os.path.expanduser(path))
  if not os.path.exists(expanded):
    return None
  try:
    with open(expanded, "r", encoding="utf-8") as f:
      payload = json.load(f)
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
      return None
    return refresh_token.strip()
  except Exception as e:
    raise ValueError(
      f"Failed to load Panopto refresh token from '{expanded}': {e}") from e


def save_panopto_refresh_token(path: str, refresh_token: str) -> None:
  expanded = os.path.abspath(os.path.expanduser(path))
  os.makedirs(os.path.dirname(expanded), exist_ok=True)
  tmp_path = f"{expanded}.tmp"
  payload = {"refresh_token": refresh_token}
  with open(tmp_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
  os.replace(tmp_path, expanded)


def _parse_panopto_token_response(response) -> PanoptoTokenBundle:
  response.raise_for_status()
  payload = response.json()
  access_token = payload.get("access_token")
  if not isinstance(access_token, str) or not access_token.strip():
    raise ValueError(
      "Panopto token response did not include a usable access_token")
  expires_in = payload.get("expires_in")
  if not isinstance(expires_in, int):
    expires_in = None
  token_type = payload.get("token_type")
  if not isinstance(token_type, str):
    token_type = None
  refresh_token = payload.get("refresh_token")
  if not isinstance(refresh_token, str) or not refresh_token.strip():
    refresh_token = None
  else:
    refresh_token = refresh_token.strip()
  return PanoptoTokenBundle(
    access_token=access_token.strip(),
    refresh_token=refresh_token,
    expires_in=expires_in,
    token_type=token_type,
  )


def _get_optional_env(name: Optional[str]) -> Optional[str]:
  if not name:
    return None
  value = os.getenv(name)
  if not value:
    return None
  return value
