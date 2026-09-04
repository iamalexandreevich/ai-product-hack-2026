import logging
from datetime import datetime, timedelta, timezone

from agentgate.session.replay import InMemoryReplayStore, PersistentReplayStore
from tests.factories import FakeClock, FakeReplayRecords, decision


def record(key: str = "k", age_seconds: int = 0):
    return decision(
        id="01J" + key[-3:].upper().ljust(3, "0"), idempotency_key=key,
        ts=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    ).to_record()


async def test_put_then_get_returns_the_same_record():
    store = InMemoryReplayStore()
    stored = record()
    await store.put("k", stored, 60)
    assert await store.get("k") == stored


async def test_get_of_an_unknown_key_is_none():
    assert await InMemoryReplayStore().get("nope") is None


async def test_entries_expire_on_the_monotonic_clock():
    clock = FakeClock()
    store = InMemoryReplayStore(now=clock)
    await store.put("k", record(), 10)
    clock.advance(9)
    assert await store.get("k") is not None
    clock.advance(2)
    assert await store.get("k") is None


async def test_restore_loads_keyed_rows_with_their_remaining_ttl():
    clock = FakeClock()
    inner = InMemoryReplayStore(now=clock)
    records = FakeReplayRecords([record("fresh", age_seconds=100), record("stale", age_seconds=90000)])
    store = PersistentReplayStore(inner, records, ttl_seconds=86400)
    await store.restore()
    assert await store.get("fresh") is not None
    assert await store.get("stale") is None
    assert records.cutoffs and records.cutoffs[0] < datetime.now(timezone.utc)
    clock.advance(86400 - 100 + 1)
    assert await store.get("fresh") is None


async def test_restore_failure_starts_empty_and_warns(caplog):
    store = PersistentReplayStore(InMemoryReplayStore(), FakeReplayRecords(error=RuntimeError("db down")), 86400)
    with caplog.at_level(logging.WARNING):
        await store.restore()
    assert await store.get("k") is None
    assert "replay" in caplog.text.lower()


async def test_persistent_store_delegates_put_and_get():
    store = PersistentReplayStore(InMemoryReplayStore(), FakeReplayRecords(), 86400)
    stored = record()
    await store.put("k", stored, 60)
    assert await store.get("k") == stored
