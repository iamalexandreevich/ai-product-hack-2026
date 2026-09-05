"""A verdict on tool output is a function of the output, the policy and
where the output came from -- so it is cached by those three, across
sessions. Unlike the allow cache, `mask` and `drop` are cached too.
"""

from typing import Protocol

from agentgate.engine.inspection import Inspection


class InspectCache(Protocol):
    async def get(self, key: str) -> Inspection | None: ...

    async def put(self, key: str, inspection: Inspection, ttl_seconds: int) -> None: ...


def inspect_cache_key(profile_hash: str, provenance_kind: str, output_digest: str) -> str:
    return f"{profile_hash}:{provenance_kind}:{output_digest}"
