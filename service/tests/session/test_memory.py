from agentgate.api.schemas import DecisionKind
from agentgate.session.memory import InMemorySessionStateStore
from tests.factories import FakeClock, session_state


async def test_state_survives_a_save_and_reload():
    store = InMemorySessionStateStore()
    state = await store.get_or_create("s1", "h", "p", "/w")
    state.record(DecisionKind.deny)
    await store.save(state)
    assert (await store.get_or_create("s1", "h", "p", "/w")).deny_total == 1


async def test_cache_returns_what_was_put_under_that_key():
    store = InMemorySessionStateStore()
    await store.cache_put("s1", "k", "dec1", ttl_seconds=10)
    assert await store.cache_get("s1", "k") == "dec1"


async def test_cache_misses_an_unknown_key():
    store = InMemorySessionStateStore()
    await store.cache_put("s1", "k", "dec1", ttl_seconds=10)
    assert await store.cache_get("s1", "other") is None


async def test_cache_entry_expires_after_its_ttl():
    clock = FakeClock()
    store = InMemorySessionStateStore(now=clock)
    await store.cache_put("s1", "k", "dec1", ttl_seconds=10)
    clock.advance(11)
    assert await store.cache_get("s1", "k") is None


async def test_cache_entry_is_expired_exactly_at_its_ttl():
    # The comparison is `>=`, so exactly at expiry (t + ttl_seconds) the entry
    # must already be considered expired, not one tick past it.
    clock = FakeClock()
    store = InMemorySessionStateStore(now=clock)
    await store.cache_put("s1", "k", "dec1", ttl_seconds=10)
    clock.advance(10)
    assert await store.cache_get("s1", "k") is None


async def test_preload_makes_a_seeded_state_the_stores_own():
    store = InMemorySessionStateStore()
    seeded = session_state("s2", deny_total=5)
    store.preload([seeded])
    again = await store.get_or_create("s2", "h", "p", "/w")
    assert again is seeded and again.deny_total == 5
