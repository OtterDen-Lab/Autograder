#!/usr/bin/env python
from __future__ import annotations

import json
import os
import re
import threading

import logging

log = logging.getLogger(__name__)


class PrivacyContext:
  """
  Centralized privacy controls for student label resolution.

  Supports:
  - `none`: real names (when available)
  - `id_only`: `Student <canvas_user_id>`
  - `blind`: stable anonymous labels (`Anon 0001`, ...)
  """

  def __init__(self,
               *,
               privacy_mode: str = "id_only",
               reveal_identity: bool = False,
               blind_id_map_path: str | None = None):
    if privacy_mode not in {"none", "id_only", "blind"}:
      raise ValueError("privacy_mode must be one of: none, id_only, blind.")
    self.privacy_mode = privacy_mode
    self.reveal_identity = bool(reveal_identity)
    self._anon_by_user_id: dict[int, str] = {}
    self._anon_lock = threading.Lock()
    self._next_anon_index = 1
    self._blind_id_map_path = self._resolve_blind_id_map_path(blind_id_map_path)

    if self.privacy_mode == "blind":
      self._load_blind_id_map()

  @staticmethod
  def _resolve_blind_id_map_path(path: str | None) -> str:
    if isinstance(path, str) and path.strip():
      return os.path.abspath(os.path.expanduser(path))
    env_path = os.getenv("AUTOGRADER_BLIND_ID_MAP_PATH")
    if env_path and env_path.strip():
      return os.path.abspath(os.path.expanduser(env_path))
    return os.path.abspath(
      os.path.expanduser("~/.autograder/privacy/blind_id_map.json"))

  def _load_blind_id_map(self) -> None:
    path = self._blind_id_map_path
    if not os.path.exists(path):
      return

    try:
      with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
      users = payload.get("users", {})
      if not isinstance(users, dict):
        return

      max_idx = 0
      for user_id_raw, label in users.items():
        try:
          user_id = int(user_id_raw)
        except Exception:
          continue
        if not isinstance(label, str):
          continue
        label = label.strip()
        if not label:
          continue
        self._anon_by_user_id[user_id] = label
        match = re.search(r"(\d+)$", label)
        if match:
          try:
            max_idx = max(max_idx, int(match.group(1)))
          except Exception:
            pass
      self._next_anon_index = max(max_idx + 1, len(self._anon_by_user_id) + 1)
    except Exception as e:
      log.warning(f"Failed to load blind ID map from '{path}': {e}")

  def _save_blind_id_map_locked(self) -> None:
    path = self._blind_id_map_path
    try:
      os.makedirs(os.path.dirname(path), exist_ok=True)
      payload = {
        "users": {
          str(user_id): label
          for user_id, label in sorted(self._anon_by_user_id.items())
        }
      }
      tmp_path = f"{path}.tmp"
      with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
      os.replace(tmp_path, path)
      try:
        os.chmod(path, 0o600)
      except Exception:
        pass
    except Exception as e:
      log.warning(f"Failed to persist blind ID map to '{path}': {e}")

  def _anonymous_label_for_user(self, user_id: int) -> str:
    with self._anon_lock:
      if user_id not in self._anon_by_user_id:
        label = f"Anon {self._next_anon_index:04d}"
        self._anon_by_user_id[user_id] = label
        self._next_anon_index += 1
        self._save_blind_id_map_locked()
      return self._anon_by_user_id[user_id]

  def resolve_student_name(self,
                           user_id: int,
                           raw_name: str | None = None) -> str:
    if self.privacy_mode == "none":
      if raw_name:
        return raw_name
      return f"Student {user_id}"
    if self.privacy_mode == "id_only":
      return f"Student {user_id}"
    return self._anonymous_label_for_user(user_id)

  def get_label(self, student) -> str:
    if student is None:
      return "Unknown Student"
    user_id = getattr(student, "user_id", None)
    raw_name = getattr(student, "name", None)
    if user_id is None:
      return str(raw_name or "Unknown Student")

    label = self.resolve_student_name(int(user_id), raw_name=raw_name)
    if self.reveal_identity and str(user_id) not in str(label):
      return f"{label} [canvas_user_id={user_id}]"
    return str(label)
