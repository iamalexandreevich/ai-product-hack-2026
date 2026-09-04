"""Replay of a decision already taken, keyed by the caller's Idempotency-Key.

A harness that retries a call after a network timeout sends the same key
again. Answering from here means the retry moves no session counter,
writes no second row and asks no model.

The key alone is not proof that two calls are the same call: it is chosen
by the caller and shared across sessions, so a colliding key from another
agent would otherwise be handed someone else's verdict -- and with it a
bypass of stage 1. `Replay` therefore stores the request identity beside
the answer, and the API honours an entry only when the identity matches.
"""

from dataclasses import dataclass
from typing import Protocol

from agentgate.api.schemas import DecideRequest, DecideResponse
from agentgate.engine.decision import DecisionRecord


@dataclass(frozen=True)
class Replay:
    """What a repeated call gets back, and who it was decided for.

    The answer is the wire response, built once by DecisionRecord.to_response;
    the identity is what a retry must match before it is trusted with it. A key
    is caller-supplied and shared across sessions, so the key alone is not
    proof that two calls are the same call.

    The identity is the whole request minus `metadata`, as one sha256. Listing
    the fields that make a decision differ proved incomplete twice -- once for
    `args.paths` (the only input a `file_write` is judged on, while `raw` is
    empty), once for `history` and `user_request` (what stage 2 reads) -- and
    each omission handed a retry a verdict taken for a different action.
    `metadata` is the one exclusion, and only because the contract already
    says it never reaches the decision logic.
    """

    request_digest: str
    response: DecideResponse

    @classmethod
    def of(cls, record: DecisionRecord) -> "Replay":
        return cls(request_digest=record.request_digest, response=record.to_response())

    def answers(self, request: DecideRequest) -> bool:
        return self.request_digest == request.identity_digest()


class ReplayStore(Protocol):
    async def get(self, key: str) -> Replay | None: ...

    async def put(self, key: str, replay: Replay, ttl_seconds: int) -> None: ...


class RestorableReplayStore(ReplayStore, Protocol):
    """A replay store the composition root brings up to date before serving."""

    async def restore(self) -> None: ...
