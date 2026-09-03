from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from agentgate.api.schemas import DecisionKind
from agentgate.session.state import RECENT_MAXLEN, SessionState
from agentgate.store.models import SessionRow
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


async def _seed_session(session_factory, session_id: str = "s1", **over) -> SessionState:
    st = SessionState(session_id=session_id, harness="t", profile_id="default", workspace="/w")
    for k, v in over.items():
        setattr(st, k, v)
    await SessionRepo(session_factory).upsert(st)
    return st


# --- brief's tests, verbatim -------------------------------------------------


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


# --- pagination: exhaustive traversal and cursor edges -----------------------


async def test_decision_list_pages_to_exhaustion_without_skip_or_duplicate(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    records = [rec() for _ in range(5)]
    for r in records:
        await repo.insert(r)
    expected_ids = sorted((r.id for r in records), reverse=True)

    collected: list[str] = []
    before = None
    for _ in range(10):  # generous upper bound; loop breaks on the first empty page
        page = await repo.list(session_id=None, model=None, limit=2, before=before)
        if not page:
            break
        collected.extend(r.id for r in page)
        before = page[-1].id

    assert collected == expected_ids


async def test_decision_list_cursor_edges(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    r1, r2, r3 = rec(), rec(), rec()
    for r in (r1, r2, r3):
        await repo.insert(r)
    all_ids = sorted((r1.id, r2.id, r3.id), reverse=True)
    lowest = all_ids[-1]

    # (a) a nonexistent id (last char of an existing id flipped): plain `<`
    # filtering, no special-casing on "this id must actually exist".
    nonexistent = lowest[:-1] + ("0" if lowest[-1] != "0" else "1")
    page = await repo.list(session_id=None, model=None, limit=10, before=nonexistent)
    expected = [i for i in all_ids if i < nonexistent]
    assert [r.id for r in page] == expected

    # (b) before == the lowest existing id: nothing is strictly less than it
    page = await repo.list(session_id=None, model=None, limit=10, before=lowest)
    assert page == []

    # (c) before below the entire id range (empty string sorts before anything)
    page = await repo.list(session_id=None, model=None, limit=10, before="")
    assert page == []

    # (d) before above the entire id range (lowercase sorts after all real,
    # uppercase-and-digit ULID strings) — returns everything, no crash
    page = await repo.list(session_id=None, model=None, limit=10, before="z" * 26)
    assert [r.id for r in page] == all_ids


# --- limit: boundaries and clamping ------------------------------------------


async def test_decision_list_limit_exceeding_remaining_rows_returns_all(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    for _ in range(3):
        await repo.insert(rec())
    page = await repo.list(session_id=None, model=None, limit=1000, before=None)
    assert len(page) == 3


async def test_decision_list_limit_is_clamped_to_valid_range(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    for _ in range(3):
        await repo.insert(rec())

    # zero and negative limits are clamped up to the minimum of 1, not passed
    # through to the database (limit=0 would return nothing; limit<0 raises
    # asyncpg.InvalidRowCountInLimitClauseError).
    page = await repo.list(session_id=None, model=None, limit=0, before=None)
    assert len(page) == 1
    page = await repo.list(session_id=None, model=None, limit=-1, before=None)
    assert len(page) == 1

    # a limit far beyond any realistic page size is clamped down to the
    # maximum of 1000, not passed through as an unbounded read.
    page = await repo.list(session_id=None, model=None, limit=10_000_000, before=None)
    assert len(page) == 3


# --- JSONB: non-ASCII, nesting, empty and null -------------------------------


async def test_decision_metadata_and_normalized_survive_non_ascii_and_nesting(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    payload_metadata = {
        "run_id": "запуск-1",
        "note": "команда: rm -rf / — «опасно», \"quoted\", line1\nline2\ttab",
        "nested": {"level": 2, "tags": ["ешь", "🔥", {"deep": True}]},
        "emoji": "🚀🧑‍💻",
    }
    payload_normalized = {"tool": "shell", "commands": [{"argv": ["echo", "привет мир"], "cwd": "/домой"}]}
    r = rec(metadata=payload_metadata, normalized=payload_normalized)
    await repo.insert(r)

    loaded = await repo.list(session_id=None, model=None, limit=10, before=None)
    got = next(x for x in loaded if x.id == r.id)
    assert got.metadata == payload_metadata
    assert got.normalized == payload_normalized


async def test_decision_empty_and_null_jsonb_distinguished_from_populated(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    empty = rec(metadata={}, model_raw_response=None)
    populated = rec(metadata={"a": 1}, model_raw_response={"choices": [{"text": "ok"}]})
    await repo.insert(empty)
    await repo.insert(populated)

    loaded = {x.id: x for x in await repo.list(session_id=None, model=None, limit=10, before=None)}
    assert loaded[empty.id].metadata == {}
    assert loaded[empty.id].model_raw_response is None
    assert loaded[populated.id].metadata == {"a": 1}
    assert loaded[populated.id].model_raw_response == {"choices": [{"text": "ok"}]}


# --- recent_decisions: capacity and empty ------------------------------------


async def test_recent_decisions_round_trips_at_and_above_capacity(session_factory):
    repo = SessionRepo(session_factory)
    s = SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w")
    decisions = [DecisionKind.deny, DecisionKind.allow, DecisionKind.ask] * 20  # 60 > RECENT_MAXLEN (50)
    for d in decisions:
        s.record(d)
    await repo.upsert(s)

    loaded = await repo.load_all()
    assert len(loaded) == 1
    got = loaded[0]
    expected_tail = [d.value for d in decisions][-RECENT_MAXLEN:]
    assert list(got.recent) == expected_tail
    assert len(got.recent) == RECENT_MAXLEN
    assert got.recent.maxlen == RECENT_MAXLEN


async def test_recent_decisions_round_trips_when_empty(session_factory):
    repo = SessionRepo(session_factory)
    s = SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w")
    await repo.upsert(s)

    loaded = await repo.load_all()
    got = loaded[0]
    assert list(got.recent) == []
    assert got.recent.maxlen == RECENT_MAXLEN


# --- duplicate id and FK ordering --------------------------------------------


async def test_duplicate_decision_id_raises_integrity_error(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    r = rec()
    await repo.insert(r)
    with pytest.raises(IntegrityError):
        await repo.insert(rec(id=r.id))


async def test_insert_orphan_decision_raises_integrity_error(session_factory):
    # No session was ever upserted — session_id references nothing.
    repo = DecisionRepo(session_factory)
    with pytest.raises(IntegrityError):
        await repo.insert(rec(session_id="does-not-exist"))


async def test_cache_put_orphan_decision_raises_integrity_error(session_factory):
    srepo = SessionRepo(session_factory)
    await _seed_session(session_factory)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    with pytest.raises(IntegrityError):
        await srepo.cache_put("s1", "hash", "does-not-exist-decision-id", future)


# --- created_at: excluded from upsert's update set ---------------------------


async def test_created_at_preserved_across_upsert(session_factory):
    repo = SessionRepo(session_factory)
    s = SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w")
    await repo.upsert(s)
    async with session_factory() as sess:
        first_created = (
            await sess.execute(select(SessionRow.created_at).where(SessionRow.id == "s1"))
        ).scalar_one()

    s.record(DecisionKind.allow)
    await repo.upsert(s)
    async with session_factory() as sess:
        second_created = (
            await sess.execute(select(SessionRow.created_at).where(SessionRow.id == "s1"))
        ).scalar_one()

    assert first_created == second_created


# --- cache_load_valid: full 4-tuple payload ----------------------------------


async def test_cache_load_valid_full_payload(session_factory):
    srepo = SessionRepo(session_factory)
    await _seed_session(session_factory)
    d = rec()
    await DecisionRepo(session_factory).insert(d)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    await srepo.cache_put("s1", "hash-live", d.id, future)

    rows = await srepo.cache_load_valid()
    assert len(rows) == 1
    session_id, action_hash, decision_id, expires_at = rows[0]
    assert session_id == "s1"
    assert action_hash == "hash-live"
    assert decision_id == d.id
    assert abs((expires_at - future).total_seconds()) < 1


# --- load_all: more than one session ------------------------------------------


async def test_load_all_returns_multiple_sessions(session_factory):
    repo = SessionRepo(session_factory)
    s1 = SessionState(session_id="s1", harness="h1", profile_id="p1", workspace="/w1")
    s1.record(DecisionKind.deny)
    s2 = SessionState(session_id="s2", harness="h2", profile_id="p2", workspace="/w2")
    s2.record(DecisionKind.allow)
    s2.record(DecisionKind.allow)
    await repo.upsert(s1)
    await repo.upsert(s2)

    loaded = {st.session_id: st for st in await repo.load_all()}
    assert set(loaded) == {"s1", "s2"}
    assert loaded["s1"].harness == "h1" and loaded["s1"].deny_total == 1
    assert loaded["s2"].harness == "h2" and loaded["s2"].decisions_total == 2


# --- DecisionRecord.to_dict(): the shape Task 10's JSONL writer reaches for --


def test_decision_record_to_dict():
    r = rec()
    d = r.to_dict()
    assert d["ts"] == r.ts.isoformat()
    assert isinstance(d["ts"], str)
    assert d["id"] == r.id
    assert d["decision"] == "allow"
    assert d["metadata"] == r.metadata


# --- expires_at must be timezone-aware ---------------------------------------


async def test_cache_put_requires_timezone_aware_expires_at(session_factory):
    srepo = SessionRepo(session_factory)
    await _seed_session(session_factory)
    d = rec()
    await DecisionRepo(session_factory).insert(d)
    naive = datetime(2099, 1, 1, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError):
        await srepo.cache_put("s1", "hash", d.id, naive)
