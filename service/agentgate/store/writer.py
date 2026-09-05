"""Where a decision goes after the answer has already been sent.

One protocol, three implementations. `write` never raises: a decision the
caller already has must not be undone by a storage failure, and one sink
failing must not stop the others.

All three rows a decision produces are written here, and the foreign keys
fix their order: `decisions.session_id` references `sessions.id` and
`allow_cache.decision_id` references `decisions.id`, so it is session row,
then decision row, then cache row. Keeping the order in one place is the
point -- split across modules it has to be reconstructed by whoever reads
it next.

The session state store deliberately does not write its own row. The gate
runs in front of every tool call an agent makes, so a database round-trip
awaited during the decision would be paid by all of them; v1's constraints
put persistence after the response for that reason.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol

from agentgate.store.protocols import Stored

log = logging.getLogger(__name__)


class DecisionWriter(Protocol):
    async def write(self, stored: Stored) -> None: ...


class JsonlDecisionWriter:
    def __init__(self, logger) -> None:
        self._logger = logger

    async def write(self, stored: Stored) -> None:
        self._logger.write(stored.to_record().model_dump(mode="json"))


class PostgresDecisionWriter:
    def __init__(self, decisions, sessions, cache_ttl_seconds: int) -> None:
        self._decisions = decisions
        self._sessions = sessions
        self._cache_ttl_seconds = cache_ttl_seconds

    async def write(self, stored: Stored) -> None:
        state = stored.session_state()
        if state is not None:
            await self._sessions.upsert(state)
        ref = stored.session_ref()
        if ref is not None:
            await self._sessions.ensure(*ref)
        inserted = await self._decisions.insert(stored)
        if inserted is False:
            # Only an explicit False means "skipped" -- a fake repo whose
            # insert has no return value is falsy (None) but did insert.
            log.warning(
                "decision %s not stored: idempotency key %s already has a row for this principal and session",
                stored.id, stored.idempotency_key,
            )
            return
        entry = stored.allow_cache_entry()
        if entry is not None:
            session_id, cache_key = entry
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._cache_ttl_seconds)
            await self._sessions.cache_put(session_id, cache_key, stored.id, expires_at)


class CompositeDecisionWriter:
    def __init__(self, writers: Sequence[DecisionWriter]) -> None:
        self._writers = tuple(writers)

    async def write(self, stored: Stored) -> None:
        for writer in self._writers:
            try:
                await writer.write(stored)
            except Exception:  # noqa: BLE001 - one sink's failure must not stop the others
                log.exception(
                    "%s failed to write decision %s", type(writer).__name__, stored.id
                )
