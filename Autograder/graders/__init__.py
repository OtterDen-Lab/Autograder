# This package contains specific grader implementations
# Import all graders to ensure they are registered

try:
  from . import manual
  from . import docker_graders
  from . import cst334
  from . import step_by_step
except ImportError as e:
  # Handle import errors gracefully
  import logging
  log = logging.getLogger(__name__)
  log.warning(f"Failed to import some grader modules: {e}")
