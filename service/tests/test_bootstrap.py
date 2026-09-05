"""Tests for the composition root (agentgate.bootstrap.build_service).

`main()` itself calls `uvicorn.run`, which blocks driving its own event loop
and cannot be exercised from a unit test -- which is why the wiring lives in
`build_service` and is tested here instead. It restores session state and
the allow-cache from Postgres, so these tests run against the live test
instance and are skipped if it is not configured.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport
from ulid import ULID

from agentgate.api.schemas import DecisionKind
from agentgate.bootstrap import build_service
from agentgate.config import Settings
from agentgate.domain.session import SessionState
from agentgate.session.replay import InMemoryReplayStore
from agentgate.store.repo import DecisionRepo, SessionRepo
from tests.conftest import TEST_DB_URL, requires_db
from tests.factories import WORKSPACE, decide_request, decision, session_state, shell_action

pytestmark = requires_db


class RecordingStore:
    """A restorable session state store that only records being restored."""

    def __init__(self) -> None:
        self.restores = 0

    async def restore(self) -> None:
        self.restores += 1

    async def get_or_create(self, session_id, harness, profile_id, workspace) -> SessionState:
        return session_state(session_id)

    async def save(self, state) -> None: ...

    async def cache_get(self, session_id, key) -> str | None:
        return None

    async def cache_put(self, session_id, key, decision_id, ttl_seconds) -> None: ...


class _NoRestore(InMemoryReplayStore):
    """An injected replay store, restored the same way the injected session
    state store is: build_service calls `restore()` on whatever it is given,
    not only on the one it builds itself."""

    async def restore(self) -> None: ...


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


def settings_for(tmp_path, **overrides) -> Settings:
    profiles_dir = tmp_path / "profiles"
    write_profile(profiles_dir)
    data = dict(db_url=TEST_DB_URL, profiles_dir=profiles_dir, bind="127.0.0.1:8400",
                log_path=tmp_path / "d.jsonl")
    data.update(overrides)
    return Settings(**data)


async def _seed(session_factory) -> tuple[SessionState, str]:
    """One persisted session with one decision and one live cache entry."""
    sessions = SessionRepo(session_factory)
    seeded = session_state("s1", workspace="/w", deny_consecutive=1, deny_total=3, decisions_total=5)
    await sessions.upsert(seeded)
    stored = decision(id=str(ULID()), request=decide_request("ls", session_id="s1"))
    await DecisionRepo(session_factory).insert(stored)
    await sessions.cache_put("s1", "hash1", stored.id, datetime.now(timezone.utc) + timedelta(hours=1))
    return seeded, stored.id


async def test_build_service_restores_the_persisted_session(session_factory, tmp_path):
    seeded, _ = await _seed(session_factory)
    service = await build_service(settings_for(tmp_path))
    restored = await service.state_store.get_or_create("s1", "t", "default", lambda: "/w")
    assert restored.deny_total == seeded.deny_total
    assert restored.decisions_total == seeded.decisions_total


async def test_build_service_restores_the_allow_cache(session_factory, tmp_path):
    _, decision_id = await _seed(session_factory)
    service = await build_service(settings_for(tmp_path))
    assert await service.state_store.cache_get("s1", "hash1") == decision_id


async def test_build_service_restores_the_store_it_was_given(session_factory, tmp_path):
    store = RecordingStore()
    service = await build_service(settings_for(tmp_path), state_store=store)
    assert service.state_store is store and store.restores == 1


async def test_built_gate_decides_against_the_wired_profile(session_factory, tmp_path):
    service = await build_service(settings_for(tmp_path))
    made = await service.gate.decide(decide_request("ls -la", session_id=None))
    assert made.verdict.decision is DecisionKind.allow and made.profile_id == "default"


async def test_build_service_returns_the_settings_it_was_given(session_factory, tmp_path):
    settings = settings_for(tmp_path)
    service = await build_service(settings)
    assert service.settings is settings


async def test_built_app_reports_a_healthy_database(session_factory, tmp_path):
    service = await build_service(settings_for(tmp_path))
    async with httpx.AsyncClient(transport=ASGITransport(app=service.app), base_url="http://test") as c:
        health = await c.get("/healthz")
    assert health.status_code == 200 and health.json()["db"] is True


async def test_build_service_raises_when_default_profile_missing(session_factory, tmp_path):
    profiles_dir = tmp_path / "profiles"
    write_profile(profiles_dir, profile_id="other")
    settings = Settings(db_url=TEST_DB_URL, profiles_dir=profiles_dir, bind="127.0.0.1:8400",
                        log_path=tmp_path / "d.jsonl", default_profile="default")
    with pytest.raises(SystemExit):
        await build_service(settings)


async def test_build_service_rejects_non_localhost_bind_without_token(tmp_path):
    with pytest.raises(ValueError, match="AGENTGATE_TOKEN"):
        await build_service(settings_for(tmp_path, bind="0.0.0.0:8400"))


async def test_build_service_restores_replayable_decisions(session_factory, tmp_path):
    stored_id = str(ULID())
    await DecisionRepo(session_factory).insert(decision(
        id=stored_id, request=decide_request("ls", session_id=None), action=shell_action("ls"),
        idempotency_key="restored",
    ))
    service = await build_service(settings_for(tmp_path))
    async with httpx.AsyncClient(transport=ASGITransport(app=service.app), base_url="http://test") as c:
        r = await c.post("/v1/decide", json={
            "harness": "t", "tool": "shell", "raw": "ls", "args": {"cwd": WORKSPACE}, "user_request": "task",
        }, headers={"idempotency-key": "restored"})
    # The stored id coming back is the proof: the same command decided afresh
    # would allow too, but under an id of its own.
    assert r.json()["decision_id"] == stored_id


async def test_a_restored_key_does_not_answer_a_different_request(session_factory, tmp_path):
    """The restored answer belongs to the call it was taken for, not to the key.

    A key is caller-supplied and global to the service, so a different action
    arriving under it must be judged, not handed a stranger's `allow`.
    """
    await DecisionRepo(session_factory).insert(decision(
        id=str(ULID()), request=decide_request("ls", session_id=None), action=shell_action("ls"),
        idempotency_key="restored-too",
    ))
    service = await build_service(settings_for(tmp_path))
    async with httpx.AsyncClient(transport=ASGITransport(app=service.app), base_url="http://test") as c:
        r = await c.post("/v1/decide", json={
            "harness": "t", "tool": "shell", "raw": "rm -rf /", "args": {"cwd": "/"}, "user_request": "x",
        }, headers={"idempotency-key": "restored-too"})
    assert r.json()["decision"] == "deny" and r.json()["rule_id"] == "hard-deny.destructive"


async def test_build_service_uses_the_replay_store_it_was_given(session_factory, tmp_path):
    replay = _NoRestore()
    service = await build_service(settings_for(tmp_path), replay_store=replay)
    assert service.replay_store is replay


async def test_built_service_wires_an_inspector(session_factory, tmp_path):
    service = await build_service(settings_for(tmp_path))
    assert service.inspector is not None


async def test_built_app_answers_inspect(session_factory, tmp_path):
    service = await build_service(settings_for(tmp_path))
    async with httpx.AsyncClient(transport=ASGITransport(app=service.app), base_url="http://test") as c:
        r = await c.post("/v1/inspect", json={
            "session_id": "s1", "harness": "t", "call_id": "c1", "tool": "shell", "tool_name": "bash",
            "status": "completed", "output": "On branch main\n",
            "provenance": {"kind": "shell", "command": "git status"},
            "args": {"cwd": WORKSPACE}, "user_request": "status",
        })
    assert r.status_code == 200 and r.json()["verdict"] == "pass"


async def test_a_session_created_only_by_inspect_never_pins_the_workspace_for_decide(session_factory, tmp_path):
    """An inspect call resolves its own workspace from `args.cwd` and hands it
    to `SessionRepo.ensure`, which creates a bare session row so
    `decisions.session_id`'s foreign key is satisfied -- see
    `agentgate.engine.inspection.Inspection.session_ref`. Across a restart
    that row is preloaded like any other persisted session (see
    `PersistentSessionStateStore.restore`), and before the fix in
    `InMemorySessionStateStore.get_or_create` the session's *first decide*
    would inherit that inspect-only workspace instead of establishing its
    own -- proven here with `cwd: "/"`, which would widen `allowed_paths` to
    the filesystem root.
    """
    session_id = "restart-pin"
    settings = settings_for(tmp_path)

    service_before_restart = await build_service(settings)
    async with httpx.AsyncClient(transport=ASGITransport(app=service_before_restart.app), base_url="http://test") as c:
        inspect_response = await c.post("/v1/inspect", json={
            "session_id": session_id, "harness": "t", "call_id": "c1", "tool": "shell", "tool_name": "bash",
            "status": "completed", "output": "root listing\n",
            "provenance": {"kind": "shell", "command": "ls /"},
            "args": {"cwd": "/"}, "user_request": "list root",
        })
    assert inspect_response.status_code == 200

    # A fresh build_service against the same database simulates a restart:
    # it restores session state from Postgres before serving traffic.
    service_after_restart = await build_service(settings)
    async with httpx.AsyncClient(transport=ASGITransport(app=service_after_restart.app), base_url="http://test") as c:
        decide_response = await c.post("/v1/decide", json={
            "session_id": session_id, "harness": "t", "tool": "file_write",
            "args": {"cwd": WORKSPACE, "paths": ["/etc/passwd"]}, "user_request": "task",
        })
    body = decide_response.json()
    assert (body["decision"], body["rule_id"]) == ("deny", "profile.path")

    state = await service_after_restart.state_store.get_or_create(session_id, "t", "default", lambda: WORKSPACE)
    assert state.workspace == WORKSPACE
