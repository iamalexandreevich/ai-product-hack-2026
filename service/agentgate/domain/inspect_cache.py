"""A verdict on tool output is a function of the output, the policy and
where the output came from -- so it is cached by those three, across
sessions. Unlike the allow cache, `mask` and `drop` are cached too.

Generic over the stored value so this module needs no import from
`engine`: `domain/replay.py` importing `engine.decision` is the one
deliberate domain->engine edge the root CLAUDE.md documents, and this
protocol does not need to be a second one. `Inspector` binds the type
parameter to `Inspection` at the call site.

The key builder lives in `session/cache_key.py`, next to the allow-cache
key: one module is the home for every decide/inspect cache key, whether
or not the store behind it happens to live in `session`.
"""

from typing import Protocol, TypeVar

T = TypeVar("T")


class InspectCache(Protocol[T]):
    async def get(self, key: str) -> T | None: ...

    async def put(self, key: str, value: T, ttl_seconds: int) -> None: ...
