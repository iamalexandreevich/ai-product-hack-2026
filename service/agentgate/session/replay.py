"""In-memory replay store, and the one that restores itself from Postgres.

TTL runs on a monotonic clock, like the allow cache. Restoring reads the
keyed decisions younger than the TTL and puts each back with whatever of
its TTL remains; a restore that fails leaves the store empty and says so
in the log -- a duplicate after a failed restore costs one extra decision,
never a wrong one.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from agentgate.domain.replay import ReplayStore
from agentgate.engine.decision import DecisionRecord

log = logging.getLogger(__name__)


class InMemoryReplayStore:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._items: dict[str, tuple[DecisionRecord, float]] = {}

    async def get(self, key: str) -> DecisionRecord | None:
        item = self._items.get(key)
        if item is None:
            return None
        record, expires = item
        if self._now() >= expires:
            del self._items[key]
            return None
        return record

    async def put(self, key: str, record: DecisionRecord, ttl_seconds: int) -> None:
        self._items[key] = (record, self._now() + ttl_seconds)


class ReplayRecords(Protocol):
    """The persisted decisions that carried a key: what restore reads."""

    async def load_replayable(self, newer_than: datetime) -> list[DecisionRecord]: ...


class PersistentReplayStore:
    def __init__(self, inner: ReplayStore, decisions: ReplayRecords, ttl_seconds: int) -> None:
        self._inner = inner
        self._decisions = decisions
        self._ttl_seconds = ttl_seconds

    async def restore(self) -> None:
        now = datetime.now(timezone.utc)
        try:
            records = await self._decisions.load_replayable(now - timedelta(seconds=self._ttl_seconds))
        except Exception:  # noqa: BLE001 - a failed restore is an empty store, not a failed start
            log.warning("replay store restore failed; starting empty", exc_info=True)
            return
        for record in records:
            remaining = self._ttl_seconds - int((now - record.ts).total_seconds())
            if remaining > 0 and record.idempotency_key is not None:
                await self._inner.put(record.idempotency_key, record, remaining)

    async def get(self, key: str) -> DecisionRecord | None:
        return await self._inner.get(key)

    async def put(self, key: str, record: DecisionRecord, ttl_seconds: int) -> None:
        await self._inner.put(key, record, ttl_seconds)
