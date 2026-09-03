"""Tests for the process-entrypoint wiring (agentgate.__main__.build_app).

`main()` itself calls `uvicorn.run`, which blocks driving its own event loop
and cannot be exercised from a unit test -- see agentgate/__main__.py's
module docstring for why the wiring is factored into `build_app()`
specifically so it can be tested here. `build_app()` needs a real database
to restore session state and the allow-cache from (Task 9's SessionRepo),
so these tests run against the live Postgres test instance and are skipped
if it is not configured, per the project's established DB-test guard.

Not covered by any test here: `main()`'s own body (logging setup, the
`asyncio.run` call, and the `uvicorn.run` call) is boot-only and only
exercised by actually starting the process (service/superpowers/sdd task-11
brief's Step 6 manual run).
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport
from ulid import ULID

from agentgate import __main__ as main_mod
from agentgate.config import Settings
from agentgate.session.state import SessionState
from agentgate.store.repo import DecisionRecord, DecisionRepo, SessionRepo
from tests.conftest import TEST_DB_URL, requires_db

pytestmark = requires_db


def rec(**over) -> DecisionRecord:
    base = dict(
        id=str(ULID()), session_id="s1", ts=datetime.now(timezone.utc), harness="t", tool="shell", raw="ls",
        normalized={"tool": "shell"}, user_request="x", profile_id="default", profile_hash="h" * 64,
        decision="allow", reason="", suggest="", stage=1, rule_id="allowlist.readonly", model=None,
        model_raw_response=None, latency_stage1_ms=1, latency_stage2_ms=None, latency_total_ms=1,
        error=None, cached=False, metadata={},
    )
    base.update(over)
    return DecisionRecord(**base)


def write_profile(profiles_dir, profile_id="default"):
    profiles_dir.mkdir(exist_ok=True)
    (profiles_dir / f"{profile_id}.yaml").write_text(f"""
id: {profile_id}
allowed_paths: ["${{WORKSPACE}}"]
protected_paths: [".env*"]
network:
  mode: allowlist
  allowed_domains: ["pypi.org"]
models:
  default: m
  configs:
    m:
      base_url: "http://llm/v1"
      model: "q"
      timeout_ms: 500
escalation:
  deny_consecutive: 2
  deny_window: {{count: 10, of_last: 50}}
""")


async def test_build_app_restores_session_state_and_allow_cache(session_factory, tmp_path, monkeypatch):
    captured: dict = {}

    class CapturingStore:
        def preload(self, states):
            captured["preloaded"] = states

        async def cache_put(self, session_id, key, decision_id, ttl_seconds):
            captured.setdefault("cache_puts", []).append((session_id, key, decision_id, ttl_seconds))

    monkeypatch.setattr(main_mod, "InMemorySessionStateStore", CapturingStore)

    srepo = SessionRepo(session_factory)
    seeded = SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w",
                          deny_consecutive=1, deny_total=3, decisions_total=5)
    await srepo.upsert(seeded)

    r = rec(session_id="s1")
    await DecisionRepo(session_factory).insert(r)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=3600)
    await srepo.cache_put("s1", "hash1", r.id, expires_at)

    profiles_dir = tmp_path / "profiles"
    write_profile(profiles_dir)
    settings = Settings(db_url=TEST_DB_URL, profiles_dir=profiles_dir, bind="127.0.0.1:8400", log_path=tmp_path / "d.jsonl")

    app, returned_settings = await main_mod.build_app(settings)

    assert returned_settings is settings
    assert len(captured["preloaded"]) == 1
    restored = captured["preloaded"][0]
    assert restored.session_id == "s1" and restored.deny_total == 3 and restored.decisions_total == 5

    assert len(captured["cache_puts"]) == 1
    cp_session_id, cp_key, cp_decision_id, cp_ttl = captured["cache_puts"][0]
    assert cp_session_id == "s1" and cp_key == "hash1" and cp_decision_id == r.id
    assert 3500 <= cp_ttl <= 3600  # derived from (expires_at - now); allow a little clock slack

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        health = await c.get("/healthz")
    assert health.status_code == 200 and health.json()["db"] is True


async def test_build_app_raises_when_default_profile_missing(session_factory, tmp_path):
    profiles_dir = tmp_path / "profiles"
    write_profile(profiles_dir, profile_id="other")
    settings = Settings(db_url=TEST_DB_URL, profiles_dir=profiles_dir, bind="127.0.0.1:8400",
                        log_path=tmp_path / "d.jsonl", default_profile="default")
    with pytest.raises(SystemExit):
        await main_mod.build_app(settings)


async def test_build_app_rejects_non_localhost_bind_without_token(tmp_path):
    profiles_dir = tmp_path / "profiles"
    write_profile(profiles_dir)
    settings = Settings(db_url=TEST_DB_URL, profiles_dir=profiles_dir, bind="0.0.0.0:8400", log_path=tmp_path / "d.jsonl")
    with pytest.raises(ValueError, match="AGENTGATE_TOKEN"):
        await main_mod.build_app(settings)
