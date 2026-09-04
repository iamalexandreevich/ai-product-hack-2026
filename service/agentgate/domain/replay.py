"""Replay of a decision already taken, keyed by the caller's Idempotency-Key.

A harness that retries a call after a network timeout sends the same key
again. Answering from here means the retry moves no session counter,
writes no second row and asks no model. The store holds the flat
`DecisionRecord` because that is what both a live decision and a Postgres
row reduce to, so a replay after a restart is built the same way as one
from memory.
"""

from typing import Protocol

from agentgate.engine.decision import DecisionRecord


class ReplayStore(Protocol):
    async def get(self, key: str) -> DecisionRecord | None: ...

    async def put(self, key: str, record: DecisionRecord, ttl_seconds: int) -> None: ...


class RestorableReplayStore(ReplayStore, Protocol):
    """A replay store the composition root brings up to date before serving."""

    async def restore(self) -> None: ...
