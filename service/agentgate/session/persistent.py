"""In-memory session state that can restore itself from Postgres.

The spec keeps counters and the allow cache in memory behind
SessionStateStore and restores them at startup. That restore used to be
glue in the process entrypoint; it is a method on the store here, which is
what makes another backing store a single new class.

Nothing here writes to Postgres. Every persisted row a decision produces --
the session row, the decision row, the allow-cache row -- is written after
the response is sent, in one place, by
agentgate.store.writer.PostgresDecisionWriter. Two reasons it has to be
that way:

- The gate sits in front of every tool call an agent makes, and v1's
  constraints put database writes after the response for that reason. A
  round-trip awaited inside `save` would be paid by every decision.
- Ordering. `decisions.session_id` references `sessions.id` and
  `allow_cache.decision_id` references `decisions.id`, so the three writes
  have one correct order and it is cheaper to keep than to reconstruct
  across two modules.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from agentgate.domain.session import SessionState, SessionStateStore


class SessionRecords(Protocol):
    """The persisted half of a session: what this store reads."""

    async def load_all(self) -> list[SessionState]: ...

    async def cache_load_valid(self) -> list[tuple[str, str, str, datetime]]: ...


class PreloadableSessionStateStore(SessionStateStore, Protocol):
    """A store that can be seeded with state it did not create.

    Restoring means putting rows that already exist into a store that has
    not seen them, which is not something every store can do -- so it is a
    requirement of the inner store, stated here rather than assumed.
    """

    def preload(self, states: list[SessionState]) -> None: ...


class PersistentSessionStateStore:
    def __init__(self, inner: PreloadableSessionStateStore, sessions: SessionRecords) -> None:
        self._inner = inner
        self._sessions = sessions

    async def restore(self) -> None:
        """Load the persisted sessions and every still-live allow-cache entry."""
        self._inner.preload(await self._sessions.load_all())
        now = datetime.now(timezone.utc)
        for session_id, key, decision_id, expires_at in await self._sessions.cache_load_valid():
            ttl_seconds = int((expires_at - now).total_seconds())
            if ttl_seconds > 0:
                await self._inner.cache_put(session_id, key, decision_id, ttl_seconds)

    async def get_or_create(
        self, session_id: str, harness: str, profile_id: str, workspace: Callable[[], str]
    ) -> SessionState:
        return await self._inner.get_or_create(session_id, harness, profile_id, workspace)

    async def save(self, state: SessionState) -> None:
        await self._inner.save(state)

    async def cache_get(self, session_id: str, key: str) -> str | None:
        return await self._inner.cache_get(session_id, key)

    async def cache_put(
        self, session_id: str, key: str, decision_id: str, ttl_seconds: int
    ) -> None:
        await self._inner.cache_put(session_id, key, decision_id, ttl_seconds)
