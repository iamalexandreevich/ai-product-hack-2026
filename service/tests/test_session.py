from agentgate.api.schemas import DecisionKind
from agentgate.profiles.schema import DenyWindow, Escalation
from agentgate.session.cache_key import allow_cache_key
from agentgate.session.escalation import should_escalate
from agentgate.session.memory import InMemorySessionStateStore
from agentgate.session.state import SessionState


def state():
    return SessionState(session_id="s", harness="h", profile_id="p", workspace="/w")


def test_record_counters():
    s = state()
    s.record(DecisionKind.deny); s.record(DecisionKind.deny)
    assert s.deny_consecutive == 2 and s.deny_total == 2 and s.decisions_total == 2
    s.record(DecisionKind.ask)
    assert s.deny_consecutive == 2
    s.record(DecisionKind.allow)
    assert s.deny_consecutive == 0 and s.deny_total == 2 and s.decisions_total == 4
    assert list(s.recent) == ["deny", "deny", "ask", "allow"]


def test_escalate_on_consecutive():
    s = state()
    cfg = Escalation(deny_consecutive=3, deny_window=DenyWindow(count=10, of_last=50))
    for _ in range(2):
        s.record(DecisionKind.deny)
    assert not should_escalate(s, cfg)
    s.record(DecisionKind.deny)
    assert should_escalate(s, cfg)


def test_escalate_on_window():
    s = state()
    cfg = Escalation(deny_consecutive=99, deny_window=DenyWindow(count=3, of_last=5))
    for d in ["deny", "allow", "deny", "allow", "deny"]:
        s.record(DecisionKind(d))
    assert should_escalate(s, cfg)
    for _ in range(5):
        s.record(DecisionKind.allow)
    assert not should_escalate(s, cfg)


async def test_memory_store_roundtrip_and_cache_ttl(monkeypatch):
    store = InMemorySessionStateStore()
    s = await store.get_or_create("s1", "h", "p", "/w")
    s.record(DecisionKind.deny)
    await store.save(s)
    again = await store.get_or_create("s1", "h", "p", "/w")
    assert again.deny_total == 1
    await store.cache_put("s1", "k", "dec1", ttl_seconds=10)
    assert await store.cache_get("s1", "k") == "dec1"
    assert await store.cache_get("s1", "other") is None
    import agentgate.session.memory as mem
    now = mem.time.monotonic()
    monkeypatch.setattr(mem.time, "monotonic", lambda: now + 11)
    assert await store.cache_get("s1", "k") is None


def test_cache_key_depends_on_all_parts():
    a = allow_cache_key("ph", "ah", "task")
    assert a != allow_cache_key("ph2", "ah", "task")
    assert a != allow_cache_key("ph", "ah2", "task")
    assert a != allow_cache_key("ph", "ah", "task2")
    assert len(a) == 64
