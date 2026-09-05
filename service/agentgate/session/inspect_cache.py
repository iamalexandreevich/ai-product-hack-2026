"""In-memory cache of inspect verdicts, keyed by content.

TTL runs on a monotonic clock, like the allow cache and the replay store;
the sweep/cap/evict mechanics live in `TtlStore` and are shared with the
replay store.
"""

import time
from collections.abc import Callable

from agentgate.engine.inspection import Inspection
from agentgate.session.ttl_store import SWEEP_EVERY, TtlStore

__all__ = ["SWEEP_EVERY", "InMemoryInspectCache"]


class InMemoryInspectCache:
    def __init__(self, now: Callable[[], float] = time.monotonic, max_entries: int = 100_000) -> None:
        self._store: TtlStore[Inspection] = TtlStore(now=now, max_entries=max_entries)

    async def get(self, key: str) -> Inspection | None:
        return await self._store.get(key)

    async def put(self, key: str, inspection: Inspection, ttl_seconds: int) -> None:
        await self._store.put(key, inspection, ttl_seconds)
