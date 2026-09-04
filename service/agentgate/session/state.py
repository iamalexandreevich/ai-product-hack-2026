"""Per-session in-memory state: decision counters and recent-decision window."""

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

    def reset_after_escalation(self) -> None:
        """Start counting afresh once a human has been asked.

        Without this the very next call would escalate again immediately.
        """
        self.deny_consecutive = 0
        self.recent.clear()


class SessionStateStore(Protocol):
    async def get_or_create(
        self, session_id: str, harness: str, profile_id: str, workspace: str
    ) -> SessionState: ...

    async def save(self, state: SessionState) -> None: ...

    async def cache_get(self, session_id: str, key: str) -> str | None: ...

    async def cache_put(
        self, session_id: str, key: str, decision_id: str, ttl_seconds: int
    ) -> None: ...
