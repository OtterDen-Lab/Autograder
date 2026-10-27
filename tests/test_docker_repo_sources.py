import pathlib
import subprocess

import pytest

from Autograder.graders.docker_graders import TemplateGrader


@pytest.mark.parametrize("repo_path", [
  "https://github.com/example/course.git",
  "ssh://git@github.com/example/course.git",
  "git://github.com/example/course.git",
  "git@github.com:example/course.git",
])
def test_remote_git_sources_are_detected(repo_path):
  assert TemplateGrader._is_remote_git_source(repo_path)


def test_get_repo_clones_https_url_without_checking_it_as_a_path(
    monkeypatch, tmp_path):
  repo_url = "https://github.com/example/course.git"
  captured = {}
  original_exists = pathlib.Path.exists

  def guarded_exists(path):
    if str(path).startswith("https:"):
      raise AssertionError("A remote URL must not be checked as a local path")
    return original_exists(path)

  def fake_run(command, **kwargs):
    captured["command"] = command
    captured["kwargs"] = kwargs
    return subprocess.CompletedProcess(command, 0)

  monkeypatch.setattr(pathlib.Path, "exists", guarded_exists)
  monkeypatch.setattr(subprocess, "run", fake_run)

  destination = tmp_path / "course"
  TemplateGrader._get_repo(repo_url, destination, depth=1, branch="main")

  assert captured["command"] == [
    "git", "clone", "--branch", "main", "--depth", "1", repo_url,
    str(destination.resolve()),
  ]
