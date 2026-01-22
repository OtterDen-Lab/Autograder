import logging.config
import yaml
import os
import re
import sys


def setup_logging() -> None:
  if "LOG_DIR" not in os.environ:
    default_log_dir = "/var/log/grading"
    if os.path.isdir(default_log_dir) and os.access(default_log_dir,
                                                    os.W_OK):
      os.environ["LOG_DIR"] = default_log_dir
    else:
      try:
        os.makedirs(default_log_dir, exist_ok=True)
        os.environ["LOG_DIR"] = default_log_dir
      except OSError:
        fallback_dir = os.getcwd()
        os.environ["LOG_DIR"] = fallback_dir
        print(
          f"Logging: unable to use {default_log_dir}; have you created it with write permissions? "
          f"Falling back to {fallback_dir}.",
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
    logging.basicConfig(level=logging.INFO)


# Call this once when your application starts
setup_logging()
