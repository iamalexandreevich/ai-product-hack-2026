"""Replay of a decision already taken, keyed by the caller's Idempotency-Key.

A harness that retries a call after a network timeout sends the same key
again. Answering from here means the retry moves no session counter,
writes no second row and asks no model.

The key alone is not proof that two calls are the same call: it is chosen
by the caller and shared across sessions, so a colliding key from another
agent would otherwise be handed someone else's verdict -- and with it a
bypass of stage 1. `Replay` therefore stores the request identity beside
the answer, and the API honours an entry only when the identity matches.

The key alone was not enough for a second reason too: it is global to the
service. Two integrators may pick the same string, so the entry is stored
under `ReplayKey` -- the key namespaced by the principal that supplied it
(the issued key's id, or "token" for the static one) -- and `answers`
checks the principal again, so a store that flattened the namespace still
could not hand one caller another's verdict.

A principal running two sessions under one key is a third way to collide:
without the session in the namespace, the two sessions' entries would evict
each other and neither would ever be replayed. `ReplayKey` therefore also
carries the session (JSON `null` for a call that named none), encoded
as a JSON array so that no character inside a session id or the caller's
key can be mistaken for a field separator between the three parts.
"""

import json
from dataclasses import dataclass
from typing import Protocol

from agentgate.api.schemas import DecideRequest, DecideResponse, InspectRequest, InspectResponse
from agentgate.engine.decision import DecisionRecord

from agentgate.domain.principal import STATIC_PRINCIPAL, principal_of  # noqa: F401  (re-exported)


@dataclass(frozen=True)
class ReplayKey:
    """The caller-supplied key, namespaced by whoever supplied it and where.

    The key is chosen by the caller and was global to the service until
    this type existed: two integrators picking the same string shared one
    entry, and one of them lost the audit row to the other's unique index.
    The session is the second half of that story -- one integrator running
    two sessions under one key had the two entries evict each other, so
    neither was ever replayed.
    """

    principal: str
    session: str | None
    key: str

    @classmethod
    def of(cls, key_id: str | None, session_id: str | None, key: str) -> "ReplayKey":
        return cls(principal_of(key_id), session_id, key)

    def storage_key(self) -> str:
        # A JSON array, not a "principal:session:key" join: either field could
        # itself contain ":" and alias a different triple onto the same slot.
        return json.dumps([self.principal, self.session, self.key], separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Replay:
    """What a repeated call gets back, and who it was decided for.

    The answer is the wire response, built once by DecisionRecord.to_response
    or to_inspect_response depending on the record's `kind`; the identity is
    what a retry must match before it is trusted with it. A key is
    caller-supplied and shared across sessions, so the key alone is not proof
    that two calls are the same call.

    The identity is the whole request minus `metadata`, as one sha256. Listing
    the fields that make a decision differ proved incomplete twice -- once for
    `args.paths` (the only input a `file_write` is judged on, while `raw` is
    empty), once for `history` and `user_request` (what stage 2 reads) -- and
    each omission handed a retry a verdict taken for a different action.
    `metadata` is the one exclusion, and only because the contract already
    says it never reaches the decision logic.
    """

    request_digest: str
    response: DecideResponse | InspectResponse
    principal: str

    @classmethod
    def of(cls, record: DecisionRecord) -> "Replay":
        response = record.to_inspect_response() if record.kind == "inspect" else record.to_response()
        return cls(
            request_digest=record.request_digest, response=response,
            principal=principal_of(record.key_id),
        )

    def answers(self, request: DecideRequest | InspectRequest, principal: str) -> bool:
        return self.principal == principal and self.request_digest == request.identity_digest()


class ReplayStore(Protocol):
    async def get(self, key: str) -> Replay | None: ...

    async def put(self, key: str, replay: Replay, ttl_seconds: int) -> None: ...


class RestorableReplayStore(ReplayStore, Protocol):
    """A replay store the composition root brings up to date before serving."""

    async def restore(self) -> None: ...
