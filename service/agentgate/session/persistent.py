"""In-memory session state, written through to Postgres and restored from it.

The spec keeps counters and the allow cache in memory behind
SessionStateStore, writes them through, and restores them at startup. That
was three pieces of glue in two modules; it is one implementation of the
protocol here, which is also what makes a Redis store a single new class.

Two boundaries this store deliberately does not cross:

- The allow-cache row is NOT written through. `allow_cache.decision_id`
  references `decisions.id`, and this store is called while the decision is
  still being made -- its row reaches Postgres only after the response is
  sent. Persisting the cache entry belongs with the decision it points at,
  and lives in agentgate.store.writer.PostgresDecisionWriter.
- A repository failure never reaches the caller. The session row is
  bookkeeping; an answer the engine has already reached must not turn into
  `ask` because Postgres is down, so `save` logs and continues, exactly as
  the post-response writers do.
"""

import logging
from datetime import datetime, timezone
from typing import Protocol

from agentgate.domain.session import SessionState, SessionStateStore

log = logging.getLogger(__name__)


class SessionRecords(Protocol):
    """The persisted half of a session: what this store reads and writes."""

    async def load_all(self) -> list[SessionState]: ...

    async def cache_load_valid(self) -> list[tuple[str, str, str, datetime]]: ...

    async def upsert(self, state: SessionState) -> None: ...


class PersistentSessionStateStore:
    def __init__(self, inner: SessionStateStore, sessions: SessionRecords) -> None:
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
        self, session_id: str, harness: str, profile_id: str, workspace: str
    ) -> SessionState:
        return await self._inner.get_or_create(session_id, harness, profile_id, workspace)

    async def save(self, state: SessionState) -> None:
        await self._inner.save(state)
        try:
            await self._sessions.upsert(state)
        except Exception:  # noqa: BLE001 - bookkeeping must not change the decision
            log.exception("failed to persist session %s", state.session_id)

    async def cache_get(self, session_id: str, key: str) -> str | None:
        return await self._inner.cache_get(session_id, key)

    async def cache_put(
        self, session_id: str, key: str, decision_id: str, ttl_seconds: int
    ) -> None:
        await self._inner.cache_put(session_id, key, decision_id, ttl_seconds)
