"""Inputs and collected results shared by local and future remote runners."""
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ExecutionInput:
  path: str
  content: bytes


@dataclass(frozen=True)
class ExecutionRequest:
  image: str
  command: str
  workdir: str | None = None
  inputs: tuple[ExecutionInput, ...] = ()
  output_paths: tuple[str, ...] = ()
  timeout_seconds: int = 60
  # Preserve existing resource/security settings. Backends must honor supported
  # settings or reject them explicitly; these are not an invitation to ignore caps.
  options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
  return_code: int
  stdout: str
  stderr: str
  files: Mapping[str, str | None] = field(default_factory=dict)

  def __iter__(self):
    # Existing error-reporting hooks unpack (return code, stdout, stderr).
    yield self.return_code
    yield self.stdout
    yield self.stderr


class Runner(Protocol):
  def run(self, request: ExecutionRequest) -> ExecutionResult:
    """Complete a grading operation and collect outputs before releasing resources."""
    ...


class ImageBuilder(Protocol):
  def find(self, tag: str) -> str | None: ...
  def build(self, context_path: str, tag: str) -> str: ...
  def release(self, image: str) -> None: ...
