import pytest

from agentgate.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    s = Settings()
    assert s.bind == "127.0.0.1:8400"
    assert s.bind_is_localhost is True
    assert s.token is None
    assert s.default_profile == "default"


def test_non_localhost_bind_requires_token(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    monkeypatch.setenv("AGENTGATE_BIND", "0.0.0.0:8400")
    s = Settings()
    assert s.bind_is_localhost is False
    with pytest.raises(ValueError):
        s.validate_token_for_bind()
    monkeypatch.setenv("AGENTGATE_TOKEN", "secret")
    Settings().validate_token_for_bind()
