import os

import Autograder


def test_setup_logging_falls_back_outside_working_directory(monkeypatch, tmp_path):
  run_dir = tmp_path / "run-dir"
  run_dir.mkdir()
  monkeypatch.chdir(run_dir)
  monkeypatch.delenv("LOG_DIR", raising=False)

  home_fallback = os.path.abspath(os.path.expanduser("~/.autograder/logs"))
  original_exists = Autograder.os.path.exists

  def fake_isdir(path):
    return path != "/var/log/grading"

  def fake_access(path, mode):
    return path != "/var/log/grading"

  def fake_makedirs(path, exist_ok=False):
    if path == "/var/log/grading":
      raise OSError("permission denied")
    return None

  def fake_exists(path):
    if str(path).endswith("logging.yaml"):
      return False
    return original_exists(path)

  monkeypatch.setattr(Autograder.os.path, "isdir", fake_isdir)
  monkeypatch.setattr(Autograder.os, "access", fake_access)
  monkeypatch.setattr(Autograder.os, "makedirs", fake_makedirs)
  monkeypatch.setattr(Autograder.os.path, "exists", fake_exists)
  monkeypatch.setattr(Autograder.logging, "basicConfig", lambda **kwargs: None)

  Autograder.setup_logging()

  assert os.environ["LOG_DIR"] == home_fallback
  assert os.environ["LOG_DIR"] != str(run_dir)
