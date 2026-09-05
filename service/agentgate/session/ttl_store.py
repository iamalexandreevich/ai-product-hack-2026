"""Generic in-memory TTL store, shared by the replay store and the inspect cache.

TTL runs on a monotonic clock so a wall-clock adjustment cannot resurrect
or kill an entry early. Two bounds keep the store from growing for a
whole TTL of traffic: every `SWEEP_EVERY` puts drop everything past its
expiry, and `max_entries` caps what is left, evicting in insertion order.

An entry is read at most once past its expiry: nothing but a `put` ever
notices that the store as a whole has stale entries in it, so `get`
alone is not a reliable evictor.
"""

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar

SWEEP_EVERY = 256

T = TypeVar("T")


class TtlStore(Generic[T]):
    def __init__(self, now: Callable[[], float] = time.monotonic, max_entries: int = 100_000) -> None:
        self._now = now
        self._max_entries = max_entries
        self._puts = 0
        self._items: OrderedDict[str, tuple[T, float]] = OrderedDict()

    async def get(self, key: str) -> T | None:
        item = self._items.get(key)
        if item is None:
            return None
        value, expires = item
        if self._now() >= expires:
            del self._items[key]
            return None
        return value

    async def put(self, key: str, value: T, ttl_seconds: int) -> None:
        self._puts += 1
        if self._puts % SWEEP_EVERY == 0:
            self._sweep()
        self._items.pop(key, None)
        self._items[key] = (value, self._now() + ttl_seconds)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    def _sweep(self) -> None:
        now = self._now()
        for key in [k for k, (_, expires) in self._items.items() if now >= expires]:
            del self._items[key]
