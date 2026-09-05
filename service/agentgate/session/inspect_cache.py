"""In-memory cache of inspect verdicts, keyed by content.

TTL runs on a monotonic clock, like the allow cache and the replay store.
Two bounds keep the store from growing for a whole TTL of traffic: every
`SWEEP_EVERY` puts drop everything past its expiry, and `max_entries` caps
what is left, evicting in insertion order.
"""

import time
from collections import OrderedDict
from collections.abc import Callable

from agentgate.engine.inspection import Inspection

SWEEP_EVERY = 256


class InMemoryInspectCache:
    def __init__(self, now: Callable[[], float] = time.monotonic, max_entries: int = 100_000) -> None:
        self._now = now
        self._max_entries = max_entries
        self._puts = 0
        self._items: OrderedDict[str, tuple[Inspection, float]] = OrderedDict()

    async def get(self, key: str) -> Inspection | None:
        item = self._items.get(key)
        if item is None:
            return None
        inspection, expires = item
        if self._now() >= expires:
            del self._items[key]
            return None
        return inspection

    async def put(self, key: str, inspection: Inspection, ttl_seconds: int) -> None:
        self._puts += 1
        if self._puts % SWEEP_EVERY == 0:
            self._sweep()
        self._items.pop(key, None)
        self._items[key] = (inspection, self._now() + ttl_seconds)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    def _sweep(self) -> None:
        now = self._now()
        for key in [k for k, (_, expires) in self._items.items() if now >= expires]:
            del self._items[key]
