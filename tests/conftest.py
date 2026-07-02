import os
import sys
import types


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
  sys.path.insert(0, ROOT)


def _install_canvasapi_stub() -> None:
  canvasapi = types.ModuleType("canvasapi")
  sys.modules["canvasapi"] = canvasapi

  submodules = {}
  for name in [
      "assignment", "course", "exceptions", "quiz", "submission", "canvas"
  ]:
    mod = types.ModuleType(f"canvasapi.{name}")
    setattr(canvasapi, name, mod)
    sys.modules[f"canvasapi.{name}"] = mod
    submodules[name] = mod

  class _Canvas:
    def __init__(self, *args, **kwargs):
      pass

  class _CanvasException(Exception):
    pass

  class _ResourceDoesNotExist(_CanvasException):
    pass

  canvasapi.Canvas = _Canvas
  submodules["exceptions"].CanvasException = _CanvasException
  submodules["exceptions"].ResourceDoesNotExist = _ResourceDoesNotExist
  submodules["canvas"].User = object


try:
  import canvasapi  # noqa: F401
except ModuleNotFoundError:
  _install_canvasapi_stub()
