import logging
from datetime import datetime, timezone

from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Latency
from agentgate.store.writer import CompositeDecisionWriter, JsonlDecisionWriter, PostgresDecisionWriter
from tests.factories import FailingDecisionWriter, RecordingDecisionWriter, decide_request


def decision(verdict: Verdict | None = None, **overrides) -> Decision:
    data = dict(
        id="01J0", ts=datetime.now(timezone.utc), request=decide_request("ls -la"),
        verdict=verdict or Verdict.allow("allowlist.readonly"),
        latency=Latency(total_ms=1), profile_id="default", profile_hash="h" * 64,
    )
    data.update(overrides)
    return Decision(**data)


class FakeSessionRepo:
    def __init__(self) -> None:
        self.upserts: list[str] = []
        self.cache_puts: list[tuple[str, str, str]] = []

    async def upsert(self, state) -> None:
        self.upserts.append(state.session_id)

    async def cache_put(self, session_id, action_hash, decision_id, expires_at) -> None:
        self.cache_puts.append((session_id, action_hash, decision_id))


class FakeDecisionRepo:
    def __init__(self) -> None:
        self.inserted: list[Decision] = []

    async def insert(self, decision) -> None:
        self.inserted.append(decision)


class CollectingLogger:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, record: dict) -> None:
        self.lines.append(record)


def state(session_id: str = "s1"):
    from agentgate.session.state import SessionState

    return SessionState(session_id=session_id, harness="t", profile_id="default", workspace="/w")


async def test_jsonl_writer_writes_the_view_shape():
    logger = CollectingLogger()
    await JsonlDecisionWriter(logger).write(decision())
    assert logger.lines[0]["decision_id"] == "01J0"
    assert logger.lines[0]["id"] == "01J0"


async def test_postgres_writer_caches_an_allow():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(state=state(), cache_key="k" * 64)
    )
    assert sessions.cache_puts == [("s1", "k" * 64, "01J0")]


async def test_postgres_writer_never_caches_a_deny():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(verdict=Verdict.deny("profile.path", "outside"), state=state(), cache_key="k" * 64)
    )
    assert sessions.cache_puts == []


async def test_postgres_writer_never_recaches_a_cache_hit():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(state=state(), cache_key="k" * 64, cached=True)
    )
    assert sessions.cache_puts == []


async def test_postgres_writer_skips_the_session_row_for_a_sessionless_call():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(decision(state=None))
    assert sessions.upserts == [] and len(decisions.inserted) == 1


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
    """Both repos sharing one order log, so 'session row before decision row'
    (the FK requirement) is asserted as an observable fact, not as a call count.
    """

    def __init__(self) -> None:
        self.order: list[str] = []

    async def upsert(self, state) -> None:
        self.order.append("session")

    async def cache_put(self, session_id, action_hash, decision_id, expires_at) -> None:
        self.order.append("cache")

    async def insert(self, decision) -> None:
        self.order.append("decision")


async def test_postgres_writer_writes_session_then_decision_then_cache():
    repos = OrderRecordingRepos()
    await PostgresDecisionWriter(repos, repos, 86400).write(decision(state=state(), cache_key="k" * 64))
    assert repos.order == ["session", "decision", "cache"]
