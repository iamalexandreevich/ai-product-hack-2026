"""In-memory replay store, and the one that restores itself from Postgres.

TTL runs on a monotonic clock, like the allow cache; the sweep/cap/evict
mechanics live in `TtlStore` and are shared with the inspect cache.
Restoring reads the keyed decisions younger than the TTL and puts each
back with whatever of its TTL remains; a restore that fails leaves the
store empty and says so in the log -- a duplicate after a failed restore
costs one extra decision, never a wrong one.

An idempotency key is read at most once, so nothing but a `put` ever
notices that an entry has expired.
"""

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol

from agentgate.domain.replay import Replay, ReplayKey, ReplayStore
from agentgate.engine.decision import DecisionRecord
from agentgate.session.ttl_store import SWEEP_EVERY, TtlStore

log = logging.getLogger(__name__)

__all__ = ["SWEEP_EVERY", "InMemoryReplayStore", "PersistentReplayStore", "ReplayRecords"]


class InMemoryReplayStore:
    def __init__(self, now: Callable[[], float] = time.monotonic, max_entries: int = 100_000) -> None:
        self._store: TtlStore[Replay] = TtlStore(now=now, max_entries=max_entries)

    async def get(self, key: str) -> Replay | None:
        return await self._store.get(key)

    async def put(self, key: str, replay: Replay, ttl_seconds: int) -> None:
        await self._store.put(key, replay, ttl_seconds)


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
            if remaining <= 0 or record.idempotency_key is None:
                continue
            try:
                replay = Replay.of(record)
            except Exception:  # noqa: BLE001 - one unprojectable row must not fail the whole restore
                log.warning("replay store restore skipped unprojectable record id=%s", record.id, exc_info=True)
                continue
            await self._inner.put(
                ReplayKey.of(record.key_id, record.idempotency_key).storage_key(), replay, remaining
            )

    async def get(self, key: str) -> Replay | None:
        return await self._inner.get(key)

    async def put(self, key: str, replay: Replay, ttl_seconds: int) -> None:
        await self._inner.put(key, replay, ttl_seconds)
