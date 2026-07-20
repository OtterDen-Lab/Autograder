import os
from types import SimpleNamespace

import pytest

from Autograder.helpers import panopto


def test_refresh_panopto_token_loads_selected_env_file(monkeypatch):
  loaded_paths = []
  args = SimpleNamespace(env="~/custom.env",
                         client_id=None,
                         client_secret=None)
  monkeypatch.delenv("PANOPTO_CLIENT_ID", raising=False)
  monkeypatch.delenv("PANOPTO_CLIENT_SECRET", raising=False)
  monkeypatch.setattr(panopto.dotenv, "load_dotenv",
                      lambda path: loaded_paths.append(path))

  with pytest.raises(SystemExit, match="client id/secret are required"):
    panopto.refresh_panopto_token(args)

  assert loaded_paths == [os.path.expanduser("~/custom.env")]
