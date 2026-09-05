"""One decision, and the one flat shape it is stored and read in.

`Decision` is what the engine produces: the request, the verdict, what
was normalized, how long it took. `DecisionRecord` is the flat projection
every consumer outside the engine sees -- the JSONL line, the Postgres
row, and the items of GET /v1/decisions are the same shape, defined
once, so a field cannot exist in the log and be missing from the API.
Its name is the one contracts/openapi.yaml publishes, because external
clients generate their type from that name.

The record carries both `id` and `decision_id`: `decision_id` is the name
the public contract uses everywhere else, `id` is what the log and the
row have always been keyed by. One field, two spellings, no second
source of truth.

`DecisionRecord.to_response` is the only place a `DecideResponse` is
built: a replayed record (repeated `Idempotency-Key`) and a live decision
must answer identically, so both go through it -- `Decision.to_response`
just stores itself and delegates.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from agentgate.api.schemas import (
    PROTOCOL,
    DecideRequest,
    DecideResponse,
    DecisionKind,
    InspectVerdict,
    LatencyMs,
    Tool,
    Turn,
)
from agentgate.domain.client_rules import ClientRules
from agentgate.domain.dialogue import Dialogue
from agentgate.domain.session import SessionState
from agentgate.domain.verdict import Verdict
from agentgate.engine.timings import Latency
from agentgate.normalize.model import NormalizedAction


class DecisionRecord(BaseModel):
    """One stored decision, as returned in the `/v1/decisions` items array.
    Everything a `DecideResponse` carries is here, plus the request it
    answered and the profile it was judged against. The item carries both
    `id` and `decision_id`; they are the same ULID (the feed injects
    `decision_id` for symmetry with `/v1/decide`)."""

    id: str = Field(description="ULID of the decision.")
    session_id: str | None
    ts: datetime
    harness: str
    tool: Tool
    raw: str
    normalized: dict[str, Any]
    user_request: str
    profile_id: str
    profile_hash: str = Field(
        description="sha256 of the normalized profile, so the benchmark can tell policies apart."
    )
    decision: DecisionKind | InspectVerdict
    reason: str
    suggest: str
    stage: int
    rule_id: str | None
    model: str | None
    model_raw_response: dict[str, Any] | None = Field(
        description="Structured output the stage-2 model returned, or `null` if it did not run."
    )
    latency_stage1_ms: int | None
    latency_stage2_ms: int | None
    latency_total_ms: int
    error: str | None = Field(
        description="Set when the decision was reached fail-closed after a failure."
    )
    cached: bool
    metadata: dict[str, Any]
    protocol: int = Field(default=PROTOCOL, description="Protocol version the request declared.")
    history: list[Turn] = Field(
        default_factory=list,
        description=(
            "Dialogue turns the stage-2 model saw, after truncation to the profile "
            "budget. Empty when stage 2 did not run."
        ),
    )
    history_omitted: int = Field(
        default=0,
        description=(
            "Turns dropped from the dialogue before it reached the model; part of "
            "the [HISTORY] header the model saw."
        ),
    )
    history_digest: str = Field(
        default="",
        description="sha256 of the full history the request carried, before truncation.",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="`Idempotency-Key` the request carried, if any; a repeat replays this record.",
    )
    request_digest: str = Field(
        default="",
        description=(
            "sha256 of the request minus `metadata`; a repeat under an "
            "`Idempotency-Key` is honoured only when it matches."
        ),
    )
    kind: Literal["decide", "inspect"] = Field(
        default="decide", description="`decide` for a pre-tool-use decision, `inspect` for a post-tool-use verdict on a result."
    )
    call_id: str | None = Field(
        default=None, description="Harness identifier pairing the decide and inspect records of one invocation."
    )
    rules_level: str | None = Field(
        default=None, description="`level` of the user's rules the request carried, if any."
    )
    rules_digest: str | None = Field(
        default=None,
        description="sha256 of the user's rule patterns, order-independent; the patterns themselves are not stored.",
    )
    provenance: dict[str, Any] | None = Field(
        default=None, description="Where an inspected result came from; `null` for decide records."
    )
    replacement: str | None = Field(
        default=None, description="The `output` a `mask` verdict returned; `null` otherwise."
    )

    @computed_field(description="Same ULID as `id`; mirrors the field name /v1/decide returns.")
    @property
    def decision_id(self) -> str:
        return self.id

    def to_response(self) -> DecideResponse:
        """The wire answer this record stands for -- the one built for a live
        decision and the one replayed for a repeated `Idempotency-Key` alike."""
        return DecideResponse(
            decision=self.decision, reason=self.reason, suggest=self.suggest, stage=self.stage,
            rule_id=self.rule_id, model=self.model,
            latency_ms=LatencyMs(
                stage1=self.latency_stage1_ms, stage2=self.latency_stage2_ms, total=self.latency_total_ms
            ),
            cached=self.cached, decision_id=self.id, protocol=self.protocol,
        )


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
    history_digest: str = ""
    dialogue: Dialogue | None = None
    idempotency_key: str | None = None

    def to_response(self) -> DecideResponse:
        return self.to_record().to_response()

    def to_record(self) -> DecisionRecord:
        return DecisionRecord(
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
            protocol=self.request.protocol,
            history=list(self.dialogue.turns) if self.dialogue is not None else [],
            history_omitted=self.dialogue.omitted if self.dialogue is not None else 0,
            history_digest=self.history_digest,
            idempotency_key=self.idempotency_key,
            request_digest=self.request.identity_digest(),
            call_id=self.request.call_id,
            rules_level=self.request.rules.level if self.request.rules else None,
            rules_digest=ClientRules.of(self.request.rules).digest() if self.request.rules else None,
        )
