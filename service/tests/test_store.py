from datetime import datetime, timedelta, timezone

from ulid import ULID

from agentgate.api.schemas import DecisionKind
from agentgate.session.state import SessionState
from agentgate.store.repo import DecisionRecord, DecisionRepo, SessionRepo
from tests.conftest import requires_db

pytestmark = requires_db


def rec(**over) -> DecisionRecord:
    base = dict(
        id=str(ULID()), session_id="s1", ts=datetime.now(timezone.utc), harness="t", tool="shell", raw="ls",
        normalized={"tool": "shell"}, user_request="x", profile_id="default", profile_hash="h" * 64,
        decision="allow", reason="", suggest="", stage=1, rule_id="allowlist.readonly", model=None,
        model_raw_response=None, latency_stage1_ms=1, latency_stage2_ms=None, latency_total_ms=1,
        error=None, cached=False, metadata={"run_id": "r1"},
    )
    base.update(over)
    return DecisionRecord(**base)


async def test_session_upsert_and_load(session_factory):
    repo = SessionRepo(session_factory)
    s = SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w")
    s.record(DecisionKind.deny)
    await repo.upsert(s)
    s.record(DecisionKind.allow)
    await repo.upsert(s)
    loaded = await repo.load_all()
    assert len(loaded) == 1
    assert loaded[0].deny_total == 1 and loaded[0].decisions_total == 2
    assert list(loaded[0].recent) == ["deny", "allow"]


async def test_decision_insert_and_list(session_factory):
    await SessionRepo(session_factory).upsert(SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w"))
    repo = DecisionRepo(session_factory)
    r1, r2, r3 = rec(), rec(model="m"), rec(session_id=None)
    for r in (r1, r2, r3):
        await repo.insert(r)
    all_rows = await repo.list(session_id=None, model=None, limit=10, before=None)
    assert [r.id for r in all_rows] == sorted([r1.id, r2.id, r3.id], reverse=True)
    assert all_rows[0].metadata == {"run_id": "r1"}
    only_s1 = await repo.list(session_id="s1", model=None, limit=10, before=None)
    assert {r.id for r in only_s1} == {r1.id, r2.id}
    only_m = await repo.list(session_id=None, model="m", limit=10, before=None)
    assert [r.id for r in only_m] == [r2.id]
    page = await repo.list(session_id=None, model=None, limit=1, before=all_rows[0].id)
    assert page[0].id == all_rows[1].id


async def test_allow_cache_roundtrip(session_factory):
    srepo = SessionRepo(session_factory)
    await srepo.upsert(SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w"))
    d = rec()
    await DecisionRepo(session_factory).insert(d)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await srepo.cache_put("s1", "hash-live", d.id, future)
    await srepo.cache_put("s1", "hash-dead", d.id, past)
    rows = await srepo.cache_load_valid()
    assert [(r[0], r[1]) for r in rows] == [("s1", "hash-live")]
