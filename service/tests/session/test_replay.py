import logging
from datetime import datetime, timedelta, timezone

from agentgate.domain.replay import Replay
from agentgate.session.replay import SWEEP_EVERY, InMemoryReplayStore, PersistentReplayStore
from tests.factories import FakeClock, FakeReplayRecords, decide_request, decision


def record(key: str = "k", age_seconds: int = 0):
    return decision(
        id="01J" + key[-3:].upper().ljust(3, "0"), idempotency_key=key,
        ts=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    ).to_record()


def replay(key: str = "k", age_seconds: int = 0) -> Replay:
    return Replay.of(record(key, age_seconds))


async def test_put_then_get_returns_the_same_entry():
    store = InMemoryReplayStore()
    stored = replay()
    await store.put("k", stored, 60)
    assert await store.get("k") == stored


async def test_get_of_an_unknown_key_is_none():
    assert await InMemoryReplayStore().get("nope") is None


async def test_entries_expire_on_the_monotonic_clock():
    clock = FakeClock()
    store = InMemoryReplayStore(now=clock)
    await store.put("k", replay(), 10)
    clock.advance(9)
    assert await store.get("k") is not None
    clock.advance(2)
    assert await store.get("k") is None


async def test_an_expired_entry_is_swept_without_ever_being_read():
    # Keys are read at most once, so `get` is not a reliable evictor: the sweep
    # on `put` is what keeps a day of unread keys from staying resident.
    clock = FakeClock()
    store = InMemoryReplayStore(now=clock)
    await store.put("stale", replay(), 10)
    clock.advance(11)
    for i in range(SWEEP_EVERY):
        await store.put(f"k{i}", replay(), 60)
    # "stale" is gone without ever being `get` -- if the sweep had not run,
    # the store would hold SWEEP_EVERY + 1 entries instead.
    assert len(store._store) == SWEEP_EVERY


async def test_the_oldest_entry_is_evicted_when_the_cap_is_reached():
    store = InMemoryReplayStore(max_entries=2)
    for key in ("a", "b", "c"):
        await store.put(key, replay(key), 60)
    assert await store.get("a") is None
    assert await store.get("b") is not None and await store.get("c") is not None


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


async def test_restore_keeps_the_identity_the_row_was_decided_for():
    stored = record("fresh", age_seconds=100)
    store = PersistentReplayStore(InMemoryReplayStore(), FakeReplayRecords([stored]), ttl_seconds=86400)
    await store.restore()
    restored = await store.get("fresh")
    assert restored.request_digest == decide_request("ls -la").identity_digest()
    assert restored.request_digest == stored.request_digest


async def test_restore_failure_starts_empty_and_warns(caplog):
    store = PersistentReplayStore(InMemoryReplayStore(), FakeReplayRecords(error=RuntimeError("db down")), 86400)
    with caplog.at_level(logging.WARNING):
        await store.restore()
    assert await store.get("k") is None
    assert "replay" in caplog.text.lower()


async def test_persistent_store_delegates_put_and_get():
    store = PersistentReplayStore(InMemoryReplayStore(), FakeReplayRecords(), 86400)
    stored = replay()
    await store.put("k", stored, 60)
    assert await store.get("k") == stored
