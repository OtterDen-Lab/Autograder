"""
Docker mocks and fixtures for tests.

Provides:
- MockDockerModule: patchable docker SDK shim for docker_utils tests
- MockDockerClient: lightweight stand-in for DockerClient interface
- MockDockerContainer: lightweight stand-in for DockerContainer behavior
- patch_docker_module fixture: patches Autograder.docker_utils.docker
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Callable
from unittest.mock import MagicMock

import pytest


class MockDockerException(Exception):
  """Base exception for docker mock errors."""


class MockDockerAPIError(MockDockerException):
  """Mock docker.errors.APIError with optional status code."""

  def __init__(self, message: str, status_code: int | None = None):
    super().__init__(message)
    self.status_code = status_code


class MockDockerBuildError(MockDockerException):
  """Mock docker.errors.BuildError."""

  def __init__(self, message: str, build_log: list | None = None):
    super().__init__(message)
    self.build_log = build_log or []


class MockDockerContainerError(MockDockerException):
  """Mock docker.errors.ContainerError."""


class MockDockerImageNotFound(MockDockerException):
  """Mock docker.errors.ImageNotFound."""


class MockDockerNotFound(MockDockerException):
  """Mock docker.errors.NotFound."""


@dataclass
class MockDockerImage:
  """Simple mock image object with Docker-like surface."""

  tags: list[str] = field(default_factory=list)

  def __post_init__(self):
    self.remove = MagicMock()


@dataclass
class MockDockerCommandResult:
  """Configurable command result for mock containers."""

  exit_code: int = 0
  stdout: bytes = b""
  stderr: bytes = b""


class MockDockerContainer:
  """
  Lightweight test double for DockerContainer-like behavior.

  Configure command outcomes with command_results where each value is:
  - MockDockerCommandResult
  - (exit_code, stdout, stderr) tuple
  """

  def __init__(
      self,
      *,
      command_results: dict[str, MockDockerCommandResult | tuple[int, bytes,
                                                                  bytes]] | None = None,
      files: dict[str, str] | None = None,
      default_result: MockDockerCommandResult | None = None):
    self.command_results = command_results or {}
    self.files = files or {}
    self.default_result = default_result or MockDockerCommandResult()

    self.started = False
    self.stopped = False
    self.copied_files: list[tuple[object, str]] = []
    self.executed_commands: list[tuple[str, str | None, int]] = []

  def start(self) -> None:
    self.started = True

  def stop(self) -> None:
    self.stopped = True

  def copy_files(self, files_to_copy: list[tuple[object, str]]) -> None:
    self.copied_files.extend(files_to_copy)

  def execute_command(self,
                      command: str,
                      workdir: str | None = None,
                      timeout_seconds: int = 60) -> tuple[int, bytes, bytes]:
    self.executed_commands.append((command, workdir, timeout_seconds))
    result = self.command_results.get(command, self.default_result)

    if isinstance(result, tuple):
      return result
    return result.exit_code, result.stdout, result.stderr

  def read_file(self, file_path: str) -> str | None:
    return self.files.get(file_path)


class MockDockerClient:
  """
  Lightweight test double for DockerClient-like behavior.

  Supports configurable image build responses and per-call container creation.
  """

  def __init__(
      self,
      *,
      build_responses: dict[str, MockDockerImage] | None = None,
      build_errors: dict[str, Exception] | None = None,
      context_build_errors: dict[str, Exception] | None = None,
      run_factory: Callable[[], MockDockerContainer] | None = None):
    self.build_responses = build_responses or {}
    self.build_errors = build_errors or {}
    self.context_build_errors = context_build_errors or {}
    self.run_factory = run_factory or (lambda: MockDockerContainer())

    self.built_images: list[str] = []
    self.context_built_images: list[str] = []
    self.run_calls: list[dict] = []

  def build_image(self, dockerfile_content: str, tag: str) -> MockDockerImage:
    del dockerfile_content
    if tag in self.build_errors:
      raise self.build_errors[tag]
    self.built_images.append(tag)
    return self.build_responses.get(tag, MockDockerImage(tags=[tag]))

  def build_image_from_context(self,
                               context_path: str,
                               tag: str,
                               use_cached: bool = True) -> MockDockerImage:
    del context_path, use_cached
    if tag in self.context_build_errors:
      raise self.context_build_errors[tag]
    self.context_built_images.append(tag)
    return self.build_responses.get(tag, MockDockerImage(tags=[tag]))

  def run_image(self, *args, **kwargs) -> MockDockerContainer:
    del args
    self.run_calls.append(kwargs)
    container = self.run_factory()
    container.start()
    return container


class MockDockerModule:
  """Patchable docker SDK shim for docker_utils tests."""

  def __init__(self, sdk_client: object | None = None):
    self.errors = SimpleNamespace(
      DockerException=MockDockerException,
      APIError=MockDockerAPIError,
      BuildError=MockDockerBuildError,
      ContainerError=MockDockerContainerError,
      ImageNotFound=MockDockerImageNotFound,
      NotFound=MockDockerNotFound,
    )

    if sdk_client is None:
      sdk_client = MagicMock()
      sdk_client.ping.return_value = True

    self.from_env = MagicMock(return_value=sdk_client)


@pytest.fixture
def patch_docker_module(monkeypatch):
  """Return helper to patch Autograder.docker_utils.docker for a test."""
  from Autograder import docker_utils

  def _patch(module: MockDockerModule | None = None) -> MockDockerModule:
    chosen_module = module or MockDockerModule()
    monkeypatch.setattr(docker_utils, "docker", chosen_module)
    return chosen_module

  return _patch
