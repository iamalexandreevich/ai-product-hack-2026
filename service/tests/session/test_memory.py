from agentgate.api.schemas import DecisionKind
from agentgate.session.memory import InMemorySessionStateStore
from tests.factories import FakeClock, session_state


async def test_state_survives_a_save_and_reload():
    store = InMemorySessionStateStore()
    state = await store.get_or_create("s1", "h", "p", lambda: "/w")
    state.record(DecisionKind.deny)
    await store.save(state)
    assert (await store.get_or_create("s1", "h", "p", lambda: "/w")).deny_total == 1


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
    again = await store.get_or_create("s2", "h", "p", lambda: "/w")
    assert again is seeded and again.deny_total == 5


async def test_a_preloaded_unclaimed_state_does_not_pin_the_workspace():
    # `SessionRepo.ensure` (called for an inspect that carries a session_id)
    # writes a row with harness="" -- a session no decide has ever seen. A
    # restart preloads that row same as any other; the first decide for that
    # session must still get its own cwd's workspace, not the one inspect's
    # `ensure` happened to write.
    store = InMemorySessionStateStore()
    unclaimed = session_state("s3", harness="", profile_id="", workspace="/")
    store.preload([unclaimed])

    claimed = await store.get_or_create("s3", "claude-code", "default", lambda: "/home/u/repo")

    assert claimed.workspace == "/home/u/repo"
    assert claimed.harness == "claude-code" and claimed.profile_id == "default"


async def test_a_preloaded_claimed_state_keeps_its_pinned_workspace():
    # The mirror case: a state a decide already claimed (non-empty harness)
    # must not be re-detected on a later call, restart or not.
    store = InMemorySessionStateStore()
    claimed_before = session_state("s4", harness="claude-code", profile_id="default", workspace="/home/u/repo")
    store.preload([claimed_before])

    again = await store.get_or_create("s4", "claude-code", "default", lambda: "/should-not-be-used")

    assert again.workspace == "/home/u/repo"
