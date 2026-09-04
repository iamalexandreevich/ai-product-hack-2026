import logging

from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.store.writer import CompositeDecisionWriter, JsonlDecisionWriter, PostgresDecisionWriter
from tests.factories import (
    FailingDecisionWriter,
    FakeSessionRecords,
    RecordingDecisionWriter,
    decision,
    session_state,
)


class FakeDecisionRepo:
    def __init__(self, insert_returns: bool = True) -> None:
        self.inserted: list[Decision] = []
        self._insert_returns = insert_returns

    async def insert(self, decision) -> bool:
        self.inserted.append(decision)
        return self._insert_returns


class CollectingLogger:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, record: dict) -> None:
        self.lines.append(record)


async def test_jsonl_writer_writes_the_view_shape():
    logger = CollectingLogger()
    await JsonlDecisionWriter(logger).write(decision())
    assert logger.lines[0]["decision_id"] == "01J0"
    assert logger.lines[0]["id"] == "01J0"


async def test_postgres_writer_inserts_the_decision_row():
    sessions, decisions = FakeSessionRecords(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(decision())
    assert len(decisions.inserted) == 1


async def test_postgres_writer_upserts_the_session_row():
    sessions, decisions = FakeSessionRecords(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(decision(state=session_state()))
    assert sessions.upserts == ["s1"]


async def test_postgres_writer_writes_no_session_row_for_a_sessionless_call():
    sessions, decisions = FakeSessionRecords(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(decision(state=None))
    assert sessions.upserts == [] and len(decisions.inserted) == 1


async def test_postgres_writer_caches_an_allow():
    sessions, decisions = FakeSessionRecords(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(state=session_state(), cache_key="k" * 64)
    )
    assert sessions.cache_puts == [("s1", "k" * 64, "01J0")]


async def test_postgres_writer_never_caches_a_deny():
    sessions, decisions = FakeSessionRecords(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(verdict=Verdict.deny("profile.path", "outside"), state=session_state(), cache_key="k" * 64)
    )
    assert sessions.cache_puts == []


async def test_postgres_writer_never_recaches_a_cache_hit():
    sessions, decisions = FakeSessionRecords(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(state=session_state(), cache_key="k" * 64, cached=True)
    )
    assert sessions.cache_puts == []


async def test_postgres_writer_caches_nothing_for_a_sessionless_call():
    sessions, decisions = FakeSessionRecords(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(decision(state=None))
    assert sessions.cache_puts == [] and len(decisions.inserted) == 1


async def test_composite_runs_every_writer():
    first, second = RecordingDecisionWriter(), RecordingDecisionWriter()
    await CompositeDecisionWriter([first, second]).write(decision())
    assert len(first.decisions) == 1 and len(second.decisions) == 1


async def test_composite_isolates_a_failing_writer():
    failing, healthy = FailingDecisionWriter(), RecordingDecisionWriter()
    await CompositeDecisionWriter([failing, healthy]).write(decision())
    assert failing.calls == 1 and len(healthy.decisions) == 1


async def test_composite_logs_the_failure_it_swallowed(caplog):
    with caplog.at_level(logging.ERROR):
        await CompositeDecisionWriter([FailingDecisionWriter()]).write(decision())
    assert "01J0" in caplog.text


class OrderRecordingRepos:
    """Both repos sharing one order log, so the FK ordering is asserted as an
    observable sequence rather than as a call count.
    """

    def __init__(self) -> None:
        self.order: list[str] = []

    async def upsert(self, state) -> None:
        self.order.append("session")

    async def cache_put(self, session_id, action_hash, decision_id, expires_at) -> None:
        self.order.append("cache")

    async def insert(self, decision) -> None:
        self.order.append("decision")


async def test_postgres_writer_writes_the_rows_in_foreign_key_order():
    # decisions.session_id -> sessions.id, allow_cache.decision_id -> decisions.id.
    repos = OrderRecordingRepos()
    await PostgresDecisionWriter(repos, repos, 86400).write(
        decision(state=session_state(), cache_key="k" * 64)
    )
    assert repos.order == ["session", "decision", "cache"]


async def test_postgres_writer_skips_the_cache_row_when_the_insert_was_skipped(caplog):
    # `insert` returns False when a row with this idempotency key already
    # existed -- a concurrent repeat must not then write a cache row whose
    # decision_id references a row that was never inserted.
    sessions, decisions = FakeSessionRecords(), FakeDecisionRepo(insert_returns=False)
    with caplog.at_level(logging.WARNING):
        await PostgresDecisionWriter(decisions, sessions, 86400).write(
            decision(state=session_state(), cache_key="k" * 64)
        )
    assert sessions.cache_puts == []
    assert "01J0" in caplog.text
