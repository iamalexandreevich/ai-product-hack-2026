### Task 8: Сессия — счётчики, эскалация, кэш allow

**Files:**
- Create: `service/agentgate/session/__init__.py`, `service/agentgate/session/state.py`, `service/agentgate/session/memory.py`, `service/agentgate/session/escalation.py`, `service/agentgate/session/cache_key.py`
- Test: `service/tests/test_session.py`

**Interfaces:**
- Produces (`agentgate.session.state`): `@dataclass class SessionState`: `session_id: str`, `harness: str`, `profile_id: str`, `workspace: str`, `deny_consecutive: int = 0`, `deny_total: int = 0`, `decisions_total: int = 0`, `recent: deque[str]` (maxlen 50, значения `allow|deny|ask`); метод `record(decision: DecisionKind) -> None` (обновляет счётчики: `allow` сбрасывает `deny_consecutive`, `deny` инкрементит оба, `ask` не трогает `deny_consecutive`). `class SessionStateStore(Protocol)`: `async get_or_create(session_id, harness, profile_id, workspace) -> SessionState`; `async save(state) -> None`; `async cache_get(session_id, key) -> str | None` (возвращает `decision_id`); `async cache_put(session_id, key, decision_id, ttl_seconds) -> None`.
- Produces (`agentgate.session.memory`): `class InMemorySessionStateStore(SessionStateStore)` с `dict` и TTL по `time.monotonic()`.
- Produces (`agentgate.session.escalation`): `should_escalate(state: SessionState, cfg: Escalation) -> bool` (оценивается ДО записи текущего решения: `deny_consecutive >= cfg.deny_consecutive` или число `deny` среди последних `cfg.deny_window.of_last` ≥ `cfg.deny_window.count`).
- Produces (`agentgate.session.cache_key`): `allow_cache_key(profile_hash: str, action_hash: str, user_request: str) -> str` (sha256).

- [ ] **Step 1: Failing tests**

`service/tests/test_session.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_session.py -v`
Expected: FAIL, `ModuleNotFoundError: agentgate.session`.

- [ ] **Step 3: Реализация**

`service/agentgate/session/__init__.py`: пустой.

`service/agentgate/session/state.py`:

```python
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from agentgate.api.schemas import DecisionKind

RECENT_MAXLEN = 50


@dataclass
class SessionState:
    session_id: str
    harness: str
    profile_id: str
    workspace: str
    deny_consecutive: int = 0
    deny_total: int = 0
    decisions_total: int = 0
    recent: deque[str] = field(default_factory=lambda: deque(maxlen=RECENT_MAXLEN))

    def record(self, decision: DecisionKind) -> None:
        self.decisions_total += 1
        if decision is DecisionKind.deny:
            self.deny_consecutive += 1
            self.deny_total += 1
        elif decision is DecisionKind.allow:
            self.deny_consecutive = 0
        self.recent.append(decision.value)


class SessionStateStore(Protocol):
    async def get_or_create(self, session_id: str, harness: str, profile_id: str, workspace: str) -> SessionState: ...
    async def save(self, state: SessionState) -> None: ...
    async def cache_get(self, session_id: str, key: str) -> str | None: ...
    async def cache_put(self, session_id: str, key: str, decision_id: str, ttl_seconds: int) -> None: ...
```

`service/agentgate/session/memory.py`:

```python
import time

from agentgate.session.state import SessionState


class InMemorySessionStateStore:
    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}

    async def get_or_create(self, session_id: str, harness: str, profile_id: str, workspace: str) -> SessionState:
        state = self._states.get(session_id)
        if state is None:
            state = SessionState(session_id=session_id, harness=harness, profile_id=profile_id, workspace=workspace)
            self._states[session_id] = state
        return state

    async def save(self, state: SessionState) -> None:
        self._states[state.session_id] = state

    async def cache_get(self, session_id: str, key: str) -> str | None:
        item = self._cache.get((session_id, key))
        if item is None:
            return None
        decision_id, expires = item
        if time.monotonic() >= expires:
            del self._cache[(session_id, key)]
            return None
        return decision_id

    async def cache_put(self, session_id: str, key: str, decision_id: str, ttl_seconds: int) -> None:
        self._cache[(session_id, key)] = (decision_id, time.monotonic() + ttl_seconds)

    def preload(self, states: list[SessionState]) -> None:
        for s in states:
            self._states[s.session_id] = s
```

`service/agentgate/session/escalation.py`:

```python
from agentgate.profiles.schema import Escalation
from agentgate.session.state import SessionState


def should_escalate(state: SessionState, cfg: Escalation) -> bool:
    if state.deny_consecutive >= cfg.deny_consecutive:
        return True
    window = list(state.recent)[-cfg.deny_window.of_last:]
    return window.count("deny") >= cfg.deny_window.count
```

`service/agentgate/session/cache_key.py`:

```python
import hashlib


def allow_cache_key(profile_hash: str, action_hash: str, user_request: str) -> str:
    payload = f"{profile_hash}\n{action_hash}\n{user_request}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_session.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add service/agentgate/session service/tests/test_session.py
git commit -m "feat(service): session state, escalation and allow cache

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

