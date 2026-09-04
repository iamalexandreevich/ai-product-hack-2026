"""Where a decision goes after the answer has already been sent.

One protocol, three implementations. `write` never raises: a decision the
caller already has must not be undone by a storage failure, and one sink
failing must not stop the others.

The session row is not written here -- the session state store writes it
during the decision, which is what satisfies the `decisions.session_id`
foreign key by the time this runs. What is left of the FK ordering lives
here: `allow_cache.decision_id` references `decisions.id`, so the cache row
follows the decision row.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol

from agentgate.api.schemas import DecisionKind
from agentgate.engine.decision import Decision

log = logging.getLogger(__name__)


class DecisionWriter(Protocol):
    async def write(self, decision: Decision) -> None: ...


class JsonlDecisionWriter:
    def __init__(self, logger) -> None:
        self._logger = logger

    async def write(self, decision: Decision) -> None:
        self._logger.write(decision.to_view().model_dump(mode="json"))


class PostgresDecisionWriter:
    def __init__(self, decisions, sessions, cache_ttl_seconds: int) -> None:
        self._decisions = decisions
        self._sessions = sessions
        self._cache_ttl_seconds = cache_ttl_seconds

    async def write(self, decision: Decision) -> None:
        await self._decisions.insert(decision)
        if self._should_cache(decision):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._cache_ttl_seconds)
            await self._sessions.cache_put(
                decision.state.session_id, decision.cache_key, decision.id, expires_at
            )

    def _should_cache(self, decision: Decision) -> bool:
        return (
            decision.state is not None
            and decision.cache_key is not None
            and decision.verdict.decision is DecisionKind.allow
            and not decision.cached
        )


class CompositeDecisionWriter:
    def __init__(self, writers: Sequence[DecisionWriter]) -> None:
        self._writers = tuple(writers)

    async def write(self, decision: Decision) -> None:
        for writer in self._writers:
            try:
                await writer.write(decision)
            except Exception:  # noqa: BLE001 - one sink's failure must not stop the others
                log.exception(
                    "%s failed to write decision %s", type(writer).__name__, decision.id
                )
