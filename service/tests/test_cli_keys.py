"""Tests for `python -m agentgate keys ...` (agentgate.cli.run_keys_cli).

`run_keys_cli` is the *synchronous* entrypoint `agentgate.__main__.main`
calls -- it owns its own `asyncio.run`, exactly like `main()` owns its own
`asyncio.run(build_app())`. Nesting another `asyncio.run` inside pytest-
asyncio's per-test event loop raises, so these tests are plain (non-async)
functions and never touch a pytest-asyncio-managed event loop at all --
not even for their own DB setup/assertions, which is why they don't use the
project's `db_engine`/`session_factory` fixtures (those are async fixtures
tied to pytest-asyncio's loop). Instead, `_reset_schema` and `_run` each
open a fresh engine, do one thing, and dispose it -- one self-contained
`asyncio.run` call at a time, same as `run_keys_cli` itself does.

Needs a real Postgres: the CLI's whole job is reading/writing the
`api_keys` table. Follows the project's established DB-test guard.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agentgate.cli import run_keys_cli
from agentgate.config import Settings
from agentgate.store.db import make_engine, make_session_factory
from agentgate.store.keys import ApiKeyRepo, hash_key
from agentgate.store.models import Base
from tests.conftest import TEST_DB_URL, requires_db


async def _reset_schema() -> None:
    engine = make_engine(TEST_DB_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture
def fresh_db():
    """Reset all tables (including api_keys) before a CLI test, without
    depending on pytest-asyncio's async fixtures -- see module docstring.
    """
    asyncio.run(_reset_schema())


def _run(coro):
    return asyncio.run(coro)


async def _with_repo(fn):
    engine = make_engine(TEST_DB_URL)
    try:
        return await fn(ApiKeyRepo(make_session_factory(engine)))
    finally:
        await engine.dispose()


def _seed_key(**kwargs):
    return _run(_with_repo(lambda repo: repo.create(**kwargs)))


def _fetch_by_hash(key_hash: str):
    return _run(_with_repo(lambda repo: repo.get_by_hash(key_hash)))


def _settings(tmp_path):
    return Settings(db_url=TEST_DB_URL, log_path=tmp_path / "d.jsonl")


@requires_db
def test_create_prints_only_the_plaintext_key_to_stdout(fresh_db, tmp_path, capsys):
    settings = _settings(tmp_path)
    rc = run_keys_cli(["create", "--label", "kilo-ci"], settings=settings)
    assert rc == 0
    out = capsys.readouterr()
    assert out.err == ""
    lines = out.out.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("agk_")


@requires_db
def test_create_stores_only_the_hash_of_the_printed_key(fresh_db, tmp_path, capsys):
    settings = _settings(tmp_path)
    rc = run_keys_cli(["create", "--label", "kilo-ci"], settings=settings)
    assert rc == 0
    plaintext = capsys.readouterr().out.strip()

    found = _fetch_by_hash(hash_key(plaintext))
    assert found is not None
    assert found.label == "kilo-ci"


@requires_db
def test_create_with_expires_sets_a_future_expiry(fresh_db, tmp_path, capsys):
    settings = _settings(tmp_path)
    rc = run_keys_cli(["create", "--label", "temp", "--expires", "1d"], settings=settings)
    assert rc == 0
    plaintext = capsys.readouterr().out.strip()

    found = _fetch_by_hash(hash_key(plaintext))
    assert found.expires_at is not None
    expected = datetime.now(timezone.utc) + timedelta(days=1)
    assert abs((found.expires_at - expected).total_seconds()) < 30


@requires_db
def test_create_with_invalid_duration_errors_to_stderr_nonzero_exit(fresh_db, tmp_path, capsys):
    settings = _settings(tmp_path)
    rc = run_keys_cli(["create", "--label", "x", "--expires", "not-a-duration"], settings=settings)
    assert rc != 0
    out = capsys.readouterr()
    assert out.err != ""
    assert out.out == ""  # nothing sensitive or partial ever hits stdout on failure


@requires_db
def test_list_shows_id_label_timestamps_never_key_or_hash(fresh_db, tmp_path, capsys):
    plaintext, record = _seed_key(label="kilo-ci")

    settings = _settings(tmp_path)
    rc = run_keys_cli(["list"], settings=settings)
    assert rc == 0
    out = capsys.readouterr().out
    assert record.id in out
    assert "kilo-ci" in out
    assert plaintext not in out
    assert hash_key(plaintext) not in out


@requires_db
def test_revoke_soft_revokes_by_id(fresh_db, tmp_path, capsys):
    plaintext, record = _seed_key(label="kilo-ci")

    settings = _settings(tmp_path)
    rc = run_keys_cli(["revoke", record.id], settings=settings)
    assert rc == 0

    found = _fetch_by_hash(hash_key(plaintext))
    assert found.revoked_at is not None
    assert found.is_valid() is False


@requires_db
def test_revoke_unknown_id_errors_to_stderr_nonzero_exit(fresh_db, tmp_path, capsys):
    settings = _settings(tmp_path)
    rc = run_keys_cli(["revoke", "does-not-exist"], settings=settings)
    assert rc != 0
    assert capsys.readouterr().err != ""


@requires_db
def test_unknown_subcommand_errors_nonzero_exit(fresh_db, tmp_path, capsys):
    settings = _settings(tmp_path)
    rc = run_keys_cli(["bogus"], settings=settings)
    assert rc != 0


@requires_db
def test_issuing_a_key_needs_no_profiles_and_no_token_for_the_bind(fresh_db, tmp_path, capsys):
    """The CLI must not go through service assembly to mint a key.

    These settings would fail `build_service` twice over -- a non-localhost
    bind with no token, and a profiles directory that does not exist -- so a
    key coming back proves the CLI never assembled the service.
    """
    settings = Settings(db_url=TEST_DB_URL, log_path=tmp_path / "d.jsonl",
                        bind="0.0.0.0:8400", profiles_dir=tmp_path / "no-such-dir")
    rc = run_keys_cli(["create", "--label", "smoke"], settings=settings)
    assert rc == 0
    assert capsys.readouterr().out.strip().startswith("agk_")


@requires_db
def test_keys_argument_dispatches_to_the_cli_instead_of_the_server(fresh_db, tmp_path, monkeypatch, capsys):
    """`python -m agentgate keys create ...` exits through the CLI.

    `main` never reaches `build_service`/`uvicorn.run` for this argv: it
    returns an exit code from `run_keys_cli` instead. The profiles directory
    is deliberately absent, so falling through to the server would raise
    rather than hang.
    """
    from agentgate import __main__ as main_mod
    from agentgate.config import get_settings

    monkeypatch.setenv("AGENTGATE_DB_URL", TEST_DB_URL)
    monkeypatch.setenv("AGENTGATE_LOG_PATH", str(tmp_path / "d.jsonl"))
    monkeypatch.setenv("AGENTGATE_PROFILES_DIR", str(tmp_path / "no-such-dir"))

    # This is the one path in the whole suite that lets `run_keys_cli` fall
    # back to the process-global `get_settings()` (production's real
    # entrypoint takes no settings) -- clear its lru_cache before and after
    # so this test cannot leak a cached Settings instance into any other
    # test that happens to run in the same process.
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exc_info:
            main_mod.main(["keys", "create", "--label", "smoke"])
    finally:
        get_settings.cache_clear()
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip().startswith("agk_")
