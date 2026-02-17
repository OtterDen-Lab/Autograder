import logging.config
import yaml
import os
import re
import sys


def _ensure_writable_log_dir(path: str) -> bool:
  try:
    os.makedirs(path, exist_ok=True)
  except OSError:
    return False
  return os.path.isdir(path) and os.access(path, os.W_OK)


def setup_logging() -> None:
  if "LOG_DIR" not in os.environ:
    candidates = [
      "/var/log/grading",
      os.path.abspath(os.path.expanduser("~/.autograder/logs")),
      "/tmp/autograder/logs",
    ]
    selected = None
    for candidate in candidates:
      if _ensure_writable_log_dir(candidate):
        selected = candidate
        break

    if selected is not None:
      os.environ["LOG_DIR"] = selected
      if selected != candidates[0]:
        print(
          f"Logging: unable to use {candidates[0]}; falling back to {selected}.",
          file=sys.stderr)
    else:
      # Last-resort fallback keeps logging usable without polluting repo/run dirs.
      os.environ["LOG_DIR"] = "/tmp"
      print(
        "Logging: unable to create dedicated log directory; falling back to /tmp.",
        file=sys.stderr)

  config_path = os.path.join(os.path.dirname(__file__), 'logging.yaml')
  if os.path.exists(config_path):
    with open(config_path, 'r') as f:
      config_text = f.read()

    # Process environment variables in the format ${VAR:-default}
    def replace_env_vars(match) -> str:
      var_name = match.group(1)
      default_value = match.group(2)
      return os.environ.get(var_name, default_value)

    config_text = re.sub(r'\$\{([^}:]+):-([^}]+)\}', replace_env_vars,
                         config_text)
    config = yaml.safe_load(config_text)
    logging.config.dictConfig(config)
  else:
    # Fallback to basic configuration if logging.yaml is not found
    logging.basicConfig(
      level=logging.INFO,
      format='%(asctime)s [T%(thread)d] - %(name)s - %(levelname)s - %(message)s',
      datefmt='%Y-%m-%d %H:%M:%S')


# Call this once when your application starts
setup_logging()
