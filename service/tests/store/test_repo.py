from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from agentgate.api.schemas import Cost, DecisionKind, InspectVerdict
from agentgate.domain.dialogue import Dialogue
from agentgate.domain.session import RECENT_MAXLEN, SessionState
from agentgate.domain.usage import Usage
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Latency
from agentgate.store.models import SessionRow
from agentgate.store.repo import DecisionRepo, SessionRepo
from agentgate.store.writer import PostgresDecisionWriter
from tests.conftest import requires_db
from tests.factories import (
    WORKSPACE,
    decide_request,
    decision,
    inspect_request,
    inspection,
    rule_set,
    session_state,
    shell_action,
    turn,
)

pytestmark = requires_db


def rec(session_id: str | None = "s1", model: str | None = None, metadata: dict | None = None,
        model_raw_response: dict | None = None, raw: str = "ls", cwd: str = WORKSPACE,
        id: str | None = None) -> Decision:
    """One decision, built the way the engine builds it, ready to be stored."""
    return decision(
        id=id or str(ULID()),
        request=decide_request(raw, session_id=session_id, args={"cwd": cwd},
                               metadata={"run_id": "r1"} if metadata is None else metadata),
        verdict=Verdict(decision=DecisionKind.allow, stage=1, rule_id="allowlist.readonly",
                        model=model, raw_response=model_raw_response),
        action=shell_action(raw, cwd),
        latency=Latency(total_ms=1, stage1_ms=1),
    )


async def _seed_session(session_factory, session_id: str = "s1", **over) -> SessionState:
    st = session_state(session_id, **over)
    await SessionRepo(session_factory).upsert(st)
    return st


async def test_session_upsert_and_load(session_factory):
    repo = SessionRepo(session_factory)
    s = session_state("s1")
    s.record(DecisionKind.deny)
    await repo.upsert(s)
    s.record(DecisionKind.allow)
    await repo.upsert(s)
    loaded = await repo.load_all()
    assert len(loaded) == 1
    assert loaded[0].deny_total == 1 and loaded[0].decisions_total == 2
    assert list(loaded[0].recent) == ["deny", "allow"]


async def test_decision_insert_and_list(session_factory):
    await _seed_session(session_factory)
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


async def test_decision_cost_roundtrips_through_postgres(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    base = rec(model="m")
    priced = decision(
        id=base.id, request=base.request, latency=base.latency,
        verdict=Verdict(
            decision=DecisionKind.deny, stage=2, model="m",
            cost=Cost.of(Usage(input_tokens=812, output_tokens=41), 0.15, 0.60),
        ),
    )
    await repo.insert(priced)
    rows = await repo.list(session_id=None, model=None, limit=10, before=None)
    row = next(r for r in rows if r.id == priced.id)
    assert row.cost.input_tokens == 812
    assert row.cost.output_tokens == 41
    assert row.cost.amount == (812 * 0.15 + 41 * 0.60) / 1_000_000


async def test_allow_cache_roundtrip(session_factory):
    srepo = SessionRepo(session_factory)
    await _seed_session(session_factory)
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
    r = rec(metadata=payload_metadata, raw='echo "привет мир"', cwd="/домой")
    await repo.insert(r)

    loaded = await repo.list(session_id=None, model=None, limit=10, before=None)
    got = next(x for x in loaded if x.id == r.id)
    assert got.metadata == payload_metadata
    assert got.normalized == r.action.to_dict()


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
    s = session_state("s1")
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
    s = session_state("s1")
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
    s = session_state("s1")
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


# --- SessionRepo.ensure: a bare row for an inspect that never decided anything ---


async def test_ensure_creates_a_session_row_for_a_session_that_never_decided(session_factory):
    repo = SessionRepo(session_factory)
    await repo.ensure("s1", "/w")
    loaded = await repo.load_all()
    assert len(loaded) == 1
    assert loaded[0].session_id == "s1"
    assert loaded[0].workspace == "/w"
    assert loaded[0].decisions_total == 0
    # The empty harness is the sentinel the in-memory store reads as
    # "no decide has claimed this session yet"; see session/memory.py.
    assert loaded[0].harness == ""


async def test_ensure_does_not_touch_an_existing_session_row(session_factory):
    repo = SessionRepo(session_factory)
    s = await _seed_session(session_factory, "s1")
    s.record(DecisionKind.allow)
    await repo.upsert(s)

    await repo.ensure("s1", "/somewhere-else")

    loaded = await repo.load_all()
    assert len(loaded) == 1
    assert loaded[0].decisions_total == 1
    assert loaded[0].workspace == s.workspace


# --- PostgresDecisionWriter: an inspect row must not be lost -----------------


async def test_writer_stores_an_inspection_when_no_decide_ever_created_the_session(session_factory):
    # This is the bug this test guards against: an Inspection carries a
    # session_id but never upserts session state, so without `ensure`
    # `decisions.session_id`'s foreign key has nothing to reference and the
    # insert below would raise ForeignKeyViolationError.
    writer = PostgresDecisionWriter(DecisionRepo(session_factory), SessionRepo(session_factory), 86400)
    outcome = inspection(
        id=str(ULID()), verdict=InspectVerdict.drop,
        request=inspect_request(session_id="fresh-session"), workspace="/w",
    )

    await writer.write(outcome)

    rows = await DecisionRepo(session_factory).list(session_id=None, model=None, limit=10, before=None, kind="inspect")
    assert [r.id for r in rows] == [outcome.id]
    sessions = await SessionRepo(session_factory).load_all()
    assert [s.session_id for s in sessions] == ["fresh-session"]
    assert sessions[0].decisions_total == 0


async def test_writer_stores_an_inspection_without_touching_an_existing_session(session_factory):
    s = await _seed_session(session_factory, "s1")
    s.record(DecisionKind.allow)
    await SessionRepo(session_factory).upsert(s)

    writer = PostgresDecisionWriter(DecisionRepo(session_factory), SessionRepo(session_factory), 86400)
    outcome = inspection(
        id=str(ULID()), verdict=InspectVerdict.pass_,
        request=inspect_request(session_id="s1"), workspace="/somewhere-else",
    )
    await writer.write(outcome)

    sessions = await SessionRepo(session_factory).load_all()
    assert len(sessions) == 1
    assert sessions[0].decisions_total == 1
    assert sessions[0].workspace == "/w"


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
    s1 = session_state("s1", harness="h1", profile_id="p1", workspace="/w1")
    s1.record(DecisionKind.deny)
    s2 = session_state("s2", harness="h2", profile_id="p2", workspace="/w2")
    s2.record(DecisionKind.allow)
    s2.record(DecisionKind.allow)
    await repo.upsert(s1)
    await repo.upsert(s2)

    loaded = {st.session_id: st for st in await repo.load_all()}
    assert set(loaded) == {"s1", "s2"}
    assert loaded["s1"].harness == "h1" and loaded["s1"].deny_total == 1
    assert loaded["s2"].harness == "h2" and loaded["s2"].decisions_total == 2


# --- expires_at must be timezone-aware ---------------------------------------


async def test_cache_put_requires_timezone_aware_expires_at(session_factory):
    srepo = SessionRepo(session_factory)
    await _seed_session(session_factory)
    d = rec()
    await DecisionRepo(session_factory).insert(d)
    naive = datetime(2099, 1, 1, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError):
        await srepo.cache_put("s1", "hash", d.id, naive)


# --- v2: history, protocol, idempotency key, replay --------------------------


async def test_v2_columns_round_trip(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    seen = Dialogue(
        turns=(turn(content="x"), turn(role="toolresult", author="system", content="y", tool="bash", call_id="c1")),
        omitted=2,
    )
    d = decision(id=str(ULID()), request=decide_request("ls", history=[turn(content="x")]),
                 action=shell_action("ls"), dialogue=seen, history_digest="h" * 64, idempotency_key="k-1")
    await repo.insert(d)
    row = (await repo.list(session_id=None, model=None, limit=1, before=None))[0]
    assert row.protocol == 1 and row.history_digest == "h" * 64 and row.idempotency_key == "k-1"
    assert [t.content for t in row.history] == ["x", "y"] and row.history[1].tool == "bash"
    assert row.history_omitted == 2


async def test_second_insert_with_the_same_idempotency_key_creates_no_row(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    await repo.insert(decision(id=str(ULID()), request=decide_request("ls"), action=shell_action("ls"), idempotency_key="dup"))
    await repo.insert(decision(id=str(ULID()), request=decide_request("ls"), action=shell_action("ls"), idempotency_key="dup"))
    rows = await repo.list(session_id=None, model=None, limit=10, before=None)
    assert len(rows) == 1


async def test_insert_reports_whether_a_row_landed(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    first = await repo.insert(decision(id=str(ULID()), request=decide_request("ls"), action=shell_action("ls"), idempotency_key="dup2"))
    second = await repo.insert(decision(id=str(ULID()), request=decide_request("ls"), action=shell_action("ls"), idempotency_key="dup2"))
    assert first is True
    assert second is False


async def test_rows_without_a_key_never_conflict_with_each_other(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    await repo.insert(rec())
    await repo.insert(rec())
    assert len(await repo.list(session_id=None, model=None, limit=10, before=None)) == 2


async def test_load_replayable_returns_keyed_rows_newer_than_the_cutoff(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    old_ts = datetime.now(timezone.utc) - timedelta(days=2)
    await repo.insert(decision(id=str(ULID()), ts=old_ts, request=decide_request("ls"), action=shell_action("ls"), idempotency_key="old"))
    await repo.insert(decision(id=str(ULID()), request=decide_request("ls"), action=shell_action("ls"), idempotency_key="fresh"))
    await repo.insert(rec())
    loaded = await repo.load_replayable(datetime.now(timezone.utc) - timedelta(days=1))
    assert [r.idempotency_key for r in loaded] == ["fresh"]


async def test_load_replayable_limit_keeps_the_newest_keyed_rows(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    now = datetime.now(timezone.utc)
    for i, key in enumerate(("oldest", "middle", "newest")):
        await repo.insert(decision(
            id=str(ULID()), ts=now - timedelta(hours=2 - i),
            request=decide_request("ls"), action=shell_action("ls"), idempotency_key=key,
        ))
    loaded = await repo.load_replayable(now - timedelta(days=1), limit=2)
    assert [r.idempotency_key for r in loaded] == ["newest", "middle"]


async def test_v3_columns_round_trip(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    d = decision(id=str(ULID()), request=decide_request("ls", call_id="c1", rules=rule_set().model_dump()), action=shell_action("ls"))
    await repo.insert(d)
    row = (await repo.list(session_id=None, model=None, limit=1, before=None))[0]
    assert row.kind == "decide" and row.call_id == "c1" and row.rules_level == "medium" and len(row.rules_digest) == 64


async def test_list_filters_by_kind(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    await repo.insert(rec())
    assert len(await repo.list(session_id=None, model=None, limit=10, before=None, kind="decide")) == 1
    assert await repo.list(session_id=None, model=None, limit=10, before=None, kind="inspect") == []


# --- v4: spans, redaction count, redacted raw --------------------------------


async def test_inspect_spans_and_redaction_roundtrip_through_postgres(session_factory):
    from agentgate.api.schemas import Span

    repo = DecisionRepo(session_factory)
    await SessionRepo(session_factory).ensure("s1", WORKSPACE)
    stored = inspection(
        id=str(ULID()), verdict=InspectVerdict.mask, replacement="K=[gate: secret redacted]\n",
        spans=(Span(line_start=0, line_end=0, kind="secret", source="detector"), Span(line_start=2, line_end=3, kind="instruction", source="model", confidence=0.7)),
        redacted=1, spans_rejected=1, redacted_output="K=[gate: secret redacted]\n",
    )
    await repo.insert(stored)
    rows = await repo.list(session_id="s1", model=None, limit=10, before=None, kind="inspect")
    record = rows[0]
    assert [s.model_dump() for s in record.spans] == [
        {"line_start": 0, "line_end": 0, "kind": "secret", "source": "detector"},
        {"line_start": 2, "line_end": 3, "kind": "instruction", "source": "model", "confidence": 0.7},
    ]
    assert (record.redacted, record.spans_rejected) == (1, 1)
    assert record.raw == "K=[gate: secret redacted]\n"
