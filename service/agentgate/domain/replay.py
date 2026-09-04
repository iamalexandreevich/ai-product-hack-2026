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
    the identity is what a retry must match before it is trusted with it. A
    key is caller-supplied and shared across sessions, so the key alone is not
    proof that two calls are the same call. Six fields make up that identity:
    `session_id`, `harness`, `tool` and `raw` name the call; `cwd` and
    `profile_id` name the context it was decided under. `cwd` matters because
    a sessionless call has no other anchor for `${WORKSPACE}` -- two calls
    with the same command but a different `cwd` may have been evaluated
    against different workspaces. `profile_id` matters because the same
    command can be allowed under one profile and denied under another; without
    it a replay could hand back the wrong profile's verdict.
    """

    session_id: str | None
    harness: str
    tool: str
    raw: str
    cwd: str | None
    profile_id: str
    response: DecideResponse

    @classmethod
    def of(cls, record: DecisionRecord) -> "Replay":
        return cls(
            session_id=record.session_id, harness=record.harness, tool=record.tool.value,
            raw=record.raw, cwd=record.normalized.get("cwd"), profile_id=record.profile_id,
            response=record.to_response(),
        )

    def answers(self, request: DecideRequest, default_profile: str) -> bool:
        return (
            self.session_id == request.session_id and self.harness == request.harness
            and self.tool == request.tool.value and self.raw == request.raw
            and self.cwd == request.args.cwd
            and self.profile_id == (request.profile_id or default_profile)
        )


class ReplayStore(Protocol):
    async def get(self, key: str) -> Replay | None: ...

    async def put(self, key: str, replay: Replay, ttl_seconds: int) -> None: ...


class RestorableReplayStore(ReplayStore, Protocol):
    """A replay store the composition root brings up to date before serving."""

    async def restore(self) -> None: ...
