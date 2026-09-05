"""One inspect verdict, projected the same way `Decision` is.

`Inspection` mirrors `Decision`'s shape for the inspect route: the request,
the verdict, what it cost, and the two projections every consumer outside
the engine sees -- the wire `InspectResponse` and the `DecisionRecord` row
shared with `/v1/decisions`.
"""

from dataclasses import dataclass
from datetime import datetime

from agentgate.api.schemas import InspectRequest, InspectResponse, InspectVerdict
from agentgate.domain.dialogue import Dialogue
from agentgate.domain.session import SessionState
from agentgate.engine.decision import DecisionRecord
from agentgate.engine.timings import Latency


@dataclass(frozen=True)
class Inspection:
    id: str
    ts: datetime
    request: InspectRequest
    verdict: InspectVerdict
    latency: Latency
    profile_id: str
    profile_hash: str
    replacement: str | None = None
    reason: str = ""
    suggest: str = ""
    stage: int = 1
    rule_id: str | None = None
    model: str | None = None
    raw_response: dict | None = None
    error: str | None = None
    cached: bool = False
    findings: tuple[str, ...] = ()
    idempotency_key: str | None = None
    # Always None: inspect never touches session state or the allow cache,
    # but the writer's `Stored` protocol reads this field on every outcome.
    state: SessionState | None = None

    def to_response(self) -> InspectResponse:
        return self.to_record().to_inspect_response()

    def to_record(self) -> DecisionRecord:
        provenance = self.request.provenance.model_dump()
        return DecisionRecord(
            id=self.id, session_id=self.request.session_id, ts=self.ts, harness=self.request.harness,
            tool=self.request.tool, raw=self.request.output,
            normalized={
                "tool_name": self.request.tool_name, "status": self.request.status.value, "provenance": provenance,
            },
            user_request=self.request.user_request, profile_id=self.profile_id, profile_hash=self.profile_hash,
            decision=self.verdict, reason=self.reason, suggest=self.suggest, stage=self.stage, rule_id=self.rule_id,
            model=self.model, model_raw_response=self.raw_response,
            latency_stage1_ms=self.latency.stage1_ms, latency_stage2_ms=self.latency.stage2_ms,
            latency_total_ms=self.latency.total_ms, error=self.error, cached=self.cached,
            metadata=self.request.metadata, protocol=self.request.protocol,
            history=[], history_digest=Dialogue.of(self.request.history).digest(),
            idempotency_key=self.idempotency_key, request_digest=self.request.identity_digest(),
            kind="inspect", call_id=self.request.call_id, provenance=provenance, replacement=self.replacement,
        )

    def allow_cache_entry(self) -> None:
        return None
