"""What the writer needs from an outcome, whether it is a `Decision` or
an `Inspection`.

Lives here, not in `engine/decision.py` or `engine/inspection.py`, because
`store/repo.py`'s repositories type their own parameters against it and
`engine` must not depend on `store`.

Every outcome answers both session questions, and at most one of them
with something: a `Decision` has `session_state()` -- its own session's
counters, upserted whenever it has one -- and no `session_ref()`, since
the state already guarantees the session row exists. An `Inspection`
never owns a session's counters, so its `session_state()` is None; its
`session_ref()` is the bare `(session_id, workspace)` pair the writer uses
to make sure the row `decisions.session_id` references exists, without
inventing counters for a session it never decided anything for.
"""

from typing import Protocol

from agentgate.domain.session import SessionState
from agentgate.engine.decision import DecisionRecord


class Stored(Protocol):
    id: str
    idempotency_key: str | None
    key_id: str | None

    def to_record(self) -> DecisionRecord: ...
    def allow_cache_entry(self) -> tuple[str, str] | None: ...
    def session_state(self) -> SessionState | None: ...
    def session_ref(self) -> tuple[str, str] | None: ...
