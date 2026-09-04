"""One decision, and the one flat shape it is stored and read in.

`Decision` is what the engine produces: the request, the verdict, what
was normalized, how long it took. `DecisionView` is the flat projection
every consumer outside the engine sees -- the JSONL line, the Postgres
row, and the items of GET /v1/decisions are the same shape, defined
once, so a field cannot exist in the log and be missing from the API.

The view carries both `id` and `decision_id`: `decision_id` is the name
the public contract uses everywhere else, `id` is what the log and the
row have always been keyed by. One field, two spellings, no second
source of truth.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, computed_field

from agentgate.api.schemas import DecideRequest, DecideResponse, DecisionKind
from agentgate.domain.session import SessionState
from agentgate.domain.verdict import Verdict
from agentgate.engine.timings import Latency
from agentgate.normalize.model import NormalizedAction


class DecisionView(BaseModel):
    id: str
    session_id: str | None
    ts: datetime
    harness: str
    tool: str
    raw: str
    normalized: dict[str, Any]
    user_request: str
    profile_id: str
    profile_hash: str
    decision: DecisionKind
    reason: str
    suggest: str
    stage: int
    rule_id: str | None
    model: str | None
    model_raw_response: dict[str, Any] | None
    latency_stage1_ms: int | None
    latency_stage2_ms: int | None
    latency_total_ms: int
    error: str | None
    cached: bool
    metadata: dict[str, Any]

    @computed_field
    @property
    def decision_id(self) -> str:
        return self.id


@dataclass(frozen=True)
class Decision:
    id: str
    ts: datetime
    request: DecideRequest
    verdict: Verdict
    latency: Latency
    profile_id: str
    profile_hash: str
    action: NormalizedAction | None = None
    state: SessionState | None = None
    cache_key: str | None = None
    cached: bool = False

    def to_response(self) -> DecideResponse:
        return DecideResponse(
            decision=self.verdict.decision,
            reason=self.verdict.reason,
            suggest=self.verdict.suggest,
            stage=self.verdict.stage,
            rule_id=self.verdict.rule_id,
            model=self.verdict.model,
            latency_ms=self.latency.to_schema(),
            cached=self.cached,
            decision_id=self.id,
        )

    def to_view(self) -> DecisionView:
        return DecisionView(
            id=self.id,
            session_id=self.request.session_id,
            ts=self.ts,
            harness=self.request.harness,
            tool=self.request.tool.value,
            raw=self.request.raw,
            normalized=self.action.to_dict() if self.action is not None else {},
            user_request=self.request.user_request,
            profile_id=self.profile_id,
            profile_hash=self.profile_hash,
            decision=self.verdict.decision,
            reason=self.verdict.reason,
            suggest=self.verdict.suggest,
            stage=self.verdict.stage,
            rule_id=self.verdict.rule_id,
            model=self.verdict.model,
            model_raw_response=self.verdict.raw_response,
            latency_stage1_ms=self.latency.stage1_ms,
            latency_stage2_ms=self.latency.stage2_ms,
            latency_total_ms=self.latency.total_ms,
            error=self.verdict.error,
            cached=self.cached,
            metadata=self.request.metadata,
        )
