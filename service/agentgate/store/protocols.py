"""What the writer needs from an outcome, whether it is a `Decision` or
an `Inspection`.

Lives here, not in `engine/decision.py` or `engine/inspection.py`, because
`store/repo.py`'s repositories type their own parameters against it and
`engine` must not depend on `store`.

`state` and `session_ref` are each optional in practice, and neither
outcome defines both: a `Decision` carries `state` -- its own session's
counters, upserted whenever it has one -- but no `session_ref`, since
`state` already guarantees the session row exists. An `Inspection` never
owns a session's counters, so it carries no `state`; instead it carries
`session_ref`, the bare `(session_id, workspace)` pair a
`PostgresDecisionWriter` uses to make sure the row `decisions.session_id`
references exists, without inventing counters for a session it never
decided anything for. The writer reads both defensively
(`getattr(..., None)`) rather than requiring every implementer to define
both.
"""

from typing import Protocol

from agentgate.engine.decision import DecisionRecord


class Stored(Protocol):
    id: str
    idempotency_key: str | None

    def to_record(self) -> DecisionRecord: ...
    def allow_cache_entry(self) -> tuple[str, str] | None: ...

    # Optional, read via getattr(stored, name, None) by the writer -- see
    # module docstring for which outcome defines which.
    # state: SessionState | None
    # def session_ref(self) -> tuple[str, str] | None: ...
