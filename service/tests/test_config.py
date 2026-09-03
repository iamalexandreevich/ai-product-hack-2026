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


@pytest.mark.parametrize(
    "bind,expected_host,expected_port,expected_localhost",
    [
        ("127.0.0.1:8400", "127.0.0.1", 8400, True),
        ("localhost:8400", "localhost", 8400, True),
        ("0.0.0.0:8400", "0.0.0.0", 8400, False),
        ("[::1]:8400", "::1", 8400, True),
        ("example.com:8400", "example.com", 8400, False),
    ],
)
def test_valid_binds(monkeypatch, bind, expected_host, expected_port, expected_localhost):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    monkeypatch.setenv("AGENTGATE_BIND", bind)
    s = Settings()
    assert s.bind_host == expected_host
    assert s.bind_port == expected_port
    assert s.bind_is_localhost is expected_localhost


@pytest.mark.parametrize(
    "bind",
    [
        "localhost",
        "0.0.0.0",
        "127.0.0.1:",
        "::1",
        "127.0.0.1:notaport",
        "127.0.0.1:0",
        "127.0.0.1:70000",
        ":8400",
    ],
)
def test_invalid_binds_rejected_at_construction(monkeypatch, bind):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    monkeypatch.setenv("AGENTGATE_BIND", bind)
    with pytest.raises(ValueError):
        Settings()


def test_missing_db_url_raises(monkeypatch):
    with pytest.raises(ValueError):
        Settings()


def test_validate_token_for_bind_localhost_without_token(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    s = Settings()
    s.validate_token_for_bind()


def test_validate_token_for_bind_non_localhost_with_token(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    monkeypatch.setenv("AGENTGATE_BIND", "0.0.0.0:8400")
    monkeypatch.setenv("AGENTGATE_TOKEN", "secret")
    s = Settings()
    s.validate_token_for_bind()
