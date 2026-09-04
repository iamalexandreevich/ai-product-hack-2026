from datetime import datetime, timedelta, timezone

from agentgate.domain.session import SessionState
from agentgate.session.memory import InMemorySessionStateStore
from agentgate.session.persistent import PersistentSessionStateStore
from tests.factories import FakeSessionRecords


def state(session_id: str = "s1", **overrides) -> SessionState:
    base = dict(session_id=session_id, harness="t", profile_id="default", workspace="/w")
    base.update(overrides)
    return SessionState(**base)


def _in_an_hour() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _an_hour_ago() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=1)


def store(sessions: FakeSessionRecords) -> PersistentSessionStateStore:
    return PersistentSessionStateStore(InMemorySessionStateStore(), sessions)


async def test_get_or_create_returns_the_restored_state():
    persistent = store(FakeSessionRecords(states=[state("s1", deny_total=5)]))
    await persistent.restore()
    assert (await persistent.get_or_create("s1", "t", "default", "/w")).deny_total == 5


async def test_save_writes_through_to_the_repository():
    sessions = FakeSessionRecords()
    await store(sessions).save(state("s1"))
    assert sessions.upserts == ["s1"]


async def test_save_keeps_the_state_readable_when_the_repository_fails():
    sessions = FakeSessionRecords(upsert_error=RuntimeError("db down"))
    persistent = store(sessions)
    await persistent.save(state("s1", deny_total=2))
    assert (await persistent.get_or_create("s1", "t", "default", "/w")).deny_total == 2


async def test_restore_reloads_the_valid_allow_cache():
    persistent = store(FakeSessionRecords(cache=[("s1", "k", "d1", _in_an_hour())]))
    await persistent.restore()
    assert await persistent.cache_get("s1", "k") == "d1"


async def test_restore_ignores_an_expired_cache_row():
    persistent = store(FakeSessionRecords(cache=[("s1", "k", "d1", _an_hour_ago())]))
    await persistent.restore()
    assert await persistent.cache_get("s1", "k") is None


async def test_cache_put_is_readable_without_touching_the_repository():
    # The allow-cache row references decisions.id, and the decision this entry
    # points at is only written after the response -- writing it through here
    # would violate that foreign key. PostgresDecisionWriter persists it.
    sessions = FakeSessionRecords()
    persistent = store(sessions)
    await persistent.cache_put("s1", "k", "d1", 60)
    assert await persistent.cache_get("s1", "k") == "d1"
    assert sessions.cache_puts == []
