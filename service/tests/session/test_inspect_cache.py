from agentgate.session.inspect_cache import SWEEP_EVERY, InMemoryInspectCache
from tests.factories import FakeClock, inspection


async def test_put_then_get_returns_the_same_entry():
    cache = InMemoryInspectCache()
    stored = inspection()
    await cache.put("k", stored, 60)
    assert await cache.get("k") == stored


async def test_get_of_an_unknown_key_is_none():
    assert await InMemoryInspectCache().get("nope") is None


async def test_entries_expire_on_the_monotonic_clock():
    clock = FakeClock()
    cache = InMemoryInspectCache(now=clock)
    await cache.put("k", inspection(), 10)
    clock.advance(9)
    assert await cache.get("k") is not None
    clock.advance(2)
    assert await cache.get("k") is None


async def test_an_expired_entry_is_swept_without_ever_being_read():
    clock = FakeClock()
    cache = InMemoryInspectCache(now=clock)
    await cache.put("stale", inspection(), 10)
    clock.advance(11)
    for i in range(SWEEP_EVERY):
        await cache.put(f"k{i}", inspection(), 60)
    assert await cache.get("stale") is None


async def test_the_oldest_entry_is_evicted_when_the_cap_is_reached():
    cache = InMemoryInspectCache(max_entries=2)
    for key in ("a", "b", "c"):
        await cache.put(key, inspection(), 60)
    assert await cache.get("a") is None
    assert await cache.get("b") is not None
    assert await cache.get("c") is not None
