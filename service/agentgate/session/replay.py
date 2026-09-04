"""In-memory replay store, and the one that restores itself from Postgres.

TTL runs on a monotonic clock, like the allow cache. Restoring reads the
keyed decisions younger than the TTL and puts each back with whatever of
its TTL remains; a restore that fails leaves the store empty and says so
in the log -- a duplicate after a failed restore costs one extra decision,
never a wrong one.

An idempotency key is read at most once, so nothing but a `put` ever
notices that an entry has expired. Two bounds keep the store from growing
for a whole TTL of traffic: every `SWEEP_EVERY` puts drop everything past
its expiry, and `max_entries` caps what is left, evicting in insertion
order.
"""

import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from agentgate.domain.replay import Replay, ReplayStore
from agentgate.engine.decision import DecisionRecord

log = logging.getLogger(__name__)

SWEEP_EVERY = 256


class InMemoryReplayStore:
    def __init__(self, now: Callable[[], float] = time.monotonic, max_entries: int = 100_000) -> None:
        self._now = now
        self._max_entries = max_entries
        self._puts = 0
        self._items: OrderedDict[str, tuple[Replay, float]] = OrderedDict()

    async def get(self, key: str) -> Replay | None:
        item = self._items.get(key)
        if item is None:
            return None
        replay, expires = item
        if self._now() >= expires:
            del self._items[key]
            return None
        return replay

    async def put(self, key: str, replay: Replay, ttl_seconds: int) -> None:
        self._puts += 1
        if self._puts % SWEEP_EVERY == 0:
            self._sweep()
        self._items.pop(key, None)
        self._items[key] = (replay, self._now() + ttl_seconds)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    def _sweep(self) -> None:
        now = self._now()
        for key in [k for k, (_, expires) in self._items.items() if now >= expires]:
            del self._items[key]


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
                await self._inner.put(record.idempotency_key, Replay.of(record), remaining)

    async def get(self, key: str) -> Replay | None:
        return await self._inner.get(key)

    async def put(self, key: str, replay: Replay, ttl_seconds: int) -> None:
        await self._inner.put(key, replay, ttl_seconds)
