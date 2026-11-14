"""Standalone worker mounted into a grading Job; only Python's stdlib is needed."""
import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def execute(payload):
  for source in payload['inputs']:
    path = Path(source['path'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(source['content'], validate=True))
  # A cached image must never supply stale feedback for this invocation.
  for output in payload['output_paths']:
    Path(output).unlink(missing_ok=True)

  with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
    process = subprocess.Popen(
      ['bash', '-lc', payload['command']], cwd=payload['workdir'],
      stdout=stdout, stderr=stderr, start_new_session=True)
    deadline = time.monotonic() + payload['timeout_seconds']
    try:
      while process.poll() is None:
        if time.monotonic() >= deadline:
          raise RuntimeError('Grading command timed out')
        if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > MAX_OUTPUT_BYTES:
          raise RuntimeError('Grading command exceeded the 4 MiB output limit')
        time.sleep(0.1)
    finally:
      # Also terminate any children left behind after the command exits.
      try:
        os.killpg(process.pid, signal.SIGKILL)
      except ProcessLookupError:
        pass
      process.wait()
    stdout.seek(0)
    stderr.seek(0)
    out = stdout.read(MAX_OUTPUT_BYTES + 1)
    err = stderr.read(MAX_OUTPUT_BYTES + 1)
    size = len(out) + len(err)
    files = {}
    for name in payload['output_paths']:
      try:
        with open(name, 'rb') as artifact:
          content = artifact.read(MAX_OUTPUT_BYTES + 1)
        size += len(content)
        files[name] = content.decode(errors='replace')
      except FileNotFoundError:
        files[name] = None
    if size > MAX_OUTPUT_BYTES:
      raise RuntimeError('Grading results exceeded the 4 MiB output limit')
    return {'return_code': process.returncode,
            'stdout': out.decode(errors='replace'),
            'stderr': err.decode(errors='replace'), 'files': files}


def main():
  payload = json.loads(Path(sys.argv[1]).read_text())
  try:
    result = execute(payload)
  except Exception as error:
    result = {'error': str(error)}
  # Grader stdout/stderr are captured above, keeping the transport separate.
  print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__':
  main()
