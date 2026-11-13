"""Docker implementations of execution and image preparation."""
import io

from Autograder.docker_utils import DockerClient, DockerContainer
from .contracts import ExecutionRequest, ExecutionResult

class DockerRunner:
  def __init__(self, client=None):
    self._client = client

  @property
  def client(self):
    if self._client is None:
      self._client = DockerClient()
    return self._client

  def run(self, request: ExecutionRequest) -> ExecutionResult:
    # A handle belongs to one invocation, never to the grader or shared runner.
    container = DockerContainer(self.client, request.image,
                                name_prefix='grader', **request.options)
    try:
      container.start()
      inputs = []
      for source in request.inputs:
        stream = io.BytesIO(source.content)
        stream.name = source.path.rsplit('/', 1)[-1]
        inputs.append((stream, source.path))
      if inputs:
        container.copy_files(inputs)
      rc, stdout, stderr = container.execute_command(
        request.command, request.workdir, timeout_seconds=request.timeout_seconds)
      files = {path: container.read_file(path) for path in request.output_paths}
      def decode(value):
        return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
      return ExecutionResult(rc, decode(stdout), decode(stderr), files)
    finally:
      container.stop()


class DockerImageBuilder:
  def __init__(self, client=None):
    self._client = client
    self._images = {}

  @property
  def client(self):
    if self._client is None:
      self._client = DockerClient()
    return self._client

  def find(self, tag):
    image = self.client.pull_cached_image(tag)
    if image is None:
      return None
    self._images[tag] = image
    return tag

  def build(self, context_path, tag):
    image = self.client.build_image_from_context(context_path=context_path, tag=tag,
                                                use_cached=True)
    self._images[tag] = image
    try:
      self.client.push_cached_image(tag)
    except Exception:
      self.release(tag)
      raise
    return tag

  def release(self, image):
    handle = self._images.pop(image, None)
    if handle is not None:
      self.client.remove_image(handle, force=True)
