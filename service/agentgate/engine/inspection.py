"""One inspect verdict, projected the same way `Decision` is.

`Inspection` mirrors `Decision`'s shape for the inspect route: the request,
the verdict, what it cost, and the two projections every consumer outside
the engine sees -- the wire `InspectResponse` and the `DecisionRecord` row
shared with `/v1/decisions`.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from agentgate.api.schemas import Cost, InspectRequest, InspectResponse, InspectVerdict, Span
from agentgate.domain.dialogue import Dialogue
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
    key_id: str | None = None
    cost: Cost | None = None
    # The workspace `detect_workspace(request.args.cwd)` resolved, kept so
    # `session_ref` can hand the writer a row to ensure without resolving it
    # a second time or importing the profile loader into `store`.
    workspace: str = ""
    spans: tuple[Span, ...] = ()
    redacted: int = 0
    spans_rejected: int = 0
    # The output with stage 1's redactions applied and the line count kept.
    # `None` when nothing was redacted. This -- never `request.output` --
    # is what the record stores as `raw`: the service must not become the
    # long-term store of the secrets it hides.
    #
    # It is built from *all* redact findings, before `unredact` is applied,
    # so a value the model released is visible to the agent in `replacement`
    # and still hidden in `raw`. Deliberate: the audit trail errs toward
    # hiding, and a model that releases a real secret must not thereby
    # write it into the database.
    redacted_output: str | None = None

    def session_state(self) -> None:
        """Inspect never owns a session's counters."""
        return None

    def session_ref(self) -> tuple[str, str] | None:
        """`(session_id, workspace)` the writer must make sure has a session
        row, without inventing counters for it -- inspect never touches
        session state or the allow cache, but `decisions.session_id` is
        still a foreign key to `sessions.id`."""
        if self.request.session_id is None:
            return None
        return self.request.session_id, self.workspace

    def as_cached(self, new_id: str, request: InspectRequest, latency: Latency, workspace: str) -> "Inspection":
        """Rebuild this cache hit as its own answer, at stage 0 (spec 5.5).

        Only the verdict-bearing fields of `self` survive: what content was
        judged and how. Everything specific to the call that produced it --
        `error`, `raw_response`, `idempotency_key`, `key_id` -- is dropped rather
        than copied, since the new call had none of those; carrying them forward
        would misreport it as having failed, produced a raw model response,
        or been submitted under someone else's idempotency key. `model` and
        `findings` stay: the verdict is deterministic on content, so they
        still describe why it was reached. `spans`, `redacted` and
        `redacted_output` are part of the verdict too and carry over the
        same way; `spans_rejected` describes validation of one call's model
        response, not the content, so it does not.
        """
        return Inspection(
            id=new_id, ts=datetime.now(timezone.utc), request=request, verdict=self.verdict,
            latency=latency, profile_id=self.profile_id, profile_hash=self.profile_hash,
            replacement=self.replacement, reason=self.reason, suggest=self.suggest, stage=0,
            rule_id=self.rule_id, model=self.model, cached=True, findings=self.findings, workspace=workspace,
            spans=self.spans, redacted=self.redacted, redacted_output=self.redacted_output,
        )

    def to_response(self) -> InspectResponse:
        return self.to_record().to_inspect_response()

    def to_record(self) -> DecisionRecord:
        provenance = self.request.provenance.model_dump()
        raw = self.request.output if self.redacted_output is None else self.redacted_output
        return DecisionRecord(
            id=self.id, session_id=self.request.session_id, ts=self.ts, harness=self.request.harness,
            tool=self.request.tool, raw=raw,
            normalized={"tool_name": self.request.tool_name, "status": self.request.status.value},
            user_request=self.request.user_request, profile_id=self.profile_id, profile_hash=self.profile_hash,
            decision=self.verdict, reason=self.reason, suggest=self.suggest, stage=self.stage, rule_id=self.rule_id,
            model=self.model, model_raw_response=self.raw_response,
            latency_stage1_ms=self.latency.stage1_ms, latency_stage2_ms=self.latency.stage2_ms,
            latency_total_ms=self.latency.total_ms, error=self.error, cached=self.cached,
            metadata=self.request.metadata, protocol=self.request.protocol,
            history=[], history_digest=Dialogue.of(self.request.history).digest(),
            idempotency_key=self.idempotency_key, request_digest=self.request.identity_digest(),
            kind="inspect", call_id=self.request.call_id, provenance=provenance, replacement=self.replacement,
            cost=self.cost, spans=list(self.spans), redacted=self.redacted, spans_rejected=self.spans_rejected,
            key_id=self.key_id,
        )

    def allow_cache_entry(self) -> None:
        return None
