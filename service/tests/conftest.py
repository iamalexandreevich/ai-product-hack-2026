import os

import pytest


@pytest.fixture(autouse=True)
def _clear_agentgate_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("AGENTGATE_"):
            monkeypatch.delenv(key, raising=False)
