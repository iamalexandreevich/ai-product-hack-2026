"""Session state, and the store protocol the engine keeps it behind.

`SessionState` is one session's counters and its recent-decision window.
`SessionStateStore` is all the engine knows about where they live, so an
in-memory store, a write-through one and a Redis one are interchangeable
to it.
"""

from collections import deque
from collections.abc import Callable
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

    def reset_after_escalation(self) -> None:
        """Start counting afresh once a human has been asked.

        Without this the very next call would escalate again immediately.
        """
        self.deny_consecutive = 0
        self.recent.clear()


class SessionStateStore(Protocol):
    async def get_or_create(
        self, session_id: str, harness: str, profile_id: str, workspace: Callable[[], str]
    ) -> SessionState:
        """The session's state, created with ``workspace()`` if it did not exist.

        The workspace arrives unevaluated because producing it walks the
        filesystem, while a session's workspace is fixed by its first
        request: on every later call the value would be computed only to be
        discarded. An implementation calls it on the creating path only.
        """
        ...

    async def save(self, state: SessionState) -> None: ...

    async def cache_get(self, session_id: str, key: str) -> str | None: ...

    async def cache_put(
        self, session_id: str, key: str, decision_id: str, ttl_seconds: int
    ) -> None: ...


class RestorableSessionStateStore(SessionStateStore, Protocol):
    """A store the composition root brings up to date before serving traffic.

    Separate from `SessionStateStore` because the engine has no business
    restoring anything -- only whoever assembles the service does.
    """

    async def restore(self) -> None: ...
