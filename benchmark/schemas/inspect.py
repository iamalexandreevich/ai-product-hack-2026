"""Inspection has its own boundary and verdicts; it is not a pre-action decision."""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.case import HistoryTurn, ToolCall, ToolCallArguments, ToolName
from schemas.result import CostSource, Usage


class FileProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["file"]
    path: str


class ShellProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["shell"]
    command: str


class WebProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["web"]
    url: str


class McpProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["mcp"]
    server: str
    tool: str


class SubagentProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["subagent"]
    session_id: str


class UnknownProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["unknown"]


Provenance = Annotated[
    FileProvenance
    | ShellProvenance
    | WebProvenance
    | McpProvenance
    | SubagentProvenance
    | UnknownProvenance,
    Field(discriminator="kind"),
]
Verdict = Literal["pass", "mask", "drop"]

# Where the case is meant to be settled. This axis replaces the main dataset's
# easy..ultra_hard, which does not transfer: at this boundary the interesting question
# is not how hard the text is to read but which layer of the cascade is answerable for
# it — and stage 2 only ever runs on a line a detector already flagged.
#
#   detector       stage 1 alone must produce the verdict, deterministically and with
#                  no model call. A regression here is a regression in the detectors.
#   classifier     a detector fires, so stage 2 runs and has the last word — the cases
#                  where the deterministic layer is knowingly too blunt on its own.
#   semantic_gap   v3 cannot reach it: no detector fires, so nothing escalates and the
#                  text reaches the model. `expected_verdict` states what a correct
#                  guard would do, so these cases fail today **on purpose** — they are
#                  the measured edge of v3 and are reported apart from the headline
#                  rate, never mixed into it. This is `v1_limitation` for inspect.
Tier = Literal["detector", "classifier", "semantic_gap"]


class InspectCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tier: Tier
    tags: list[str] = Field(default_factory=list)
    benign: bool = False
    tool: ToolName
    tool_name: str = Field(min_length=1, max_length=64)
    status: Literal["completed", "error"] = "completed"
    output: str
    provenance: Provenance
    args: ToolCallArguments
    user_request: str = ""
    history: list[HistoryTurn] = Field(default_factory=list)
    expected_verdict: Verdict
    acceptable_verdicts: list[Verdict] = Field(default_factory=list)
    expected_output: str | None = None
    must_preserve: list[str] = Field(default_factory=list)
    must_remove: list[str] = Field(default_factory=list)
    expected_rule_id_prefix: str | None = None
    # Explicit malformed wire overrides belong only in the API refusal suite.
    request_overrides: dict[str, Any] = Field(default_factory=dict)
    api_refusal: bool = False
    pre_action: ToolCall | None = None

    @model_validator(mode="after")
    def check_case(self) -> Self:
        if not self.acceptable_verdicts:
            self.acceptable_verdicts = [self.expected_verdict]
        if self.expected_verdict not in self.acceptable_verdicts:
            raise ValueError("expected_verdict must be acceptable")
        if self.request_overrides and not self.api_refusal:
            raise ValueError("wire overrides require api_refusal=true")
        if self.api_refusal and self.acceptable_verdicts != ["drop"]:
            raise ValueError("API refusal cases must require drop")
        if not self.api_refusal:
            if len(self.output.encode("utf-8", "surrogatepass")) > 262144:
                raise ValueError("output exceeds 262144 UTF-8 bytes")
            if len(self.history) > 200 or sum(t.wire_bytes() for t in self.history) > 131072:
                raise ValueError("history exceeds service limits")
        # Only when a mask is what the case *expects*. A case that merely tolerates one
        # is checked by `must_preserve`: what matters there is what survived, not what
        # the replacement line says.
        if self.expected_verdict == "mask" and not (
            self.expected_output is not None or self.must_remove
        ):
            raise ValueError("a case expecting mask must check the replacement text")
        if self.pre_action and (
            self.pre_action.tool != self.tool or self.pre_action.arguments != self.args
        ):
            raise ValueError("pre_action must match inspection tool and args")
        if self.tier == "semantic_gap":
            # The point of the tier is that v3 lets the text through. A gap case that
            # accepts `pass` would pass today and stop marking the edge it exists to mark.
            if "pass" in self.acceptable_verdicts:
                raise ValueError("a semantic_gap case must not accept pass; it states the gap")
            if self.benign:
                raise ValueError("semantic_gap describes an attack v3 misses, never a benign case")
        if self.api_refusal and self.tier != "detector":
            raise ValueError("API refusals are settled before any detector runs")
        if self.benign and self.expected_verdict == "drop":
            raise ValueError("dropping a benign result is never the expected outcome")
        return self

    @property
    def counts_toward_rates(self) -> bool:
        """Whether this case belongs in the published rates at all.

        Benign controls do (they are the preservation and intervention rates). Two
        kinds do not: an API refusal measures fail-closed plumbing rather than
        detection, and a `semantic_gap` case is a known v3 edge that would drag the
        containment rate down for a reason already documented. Both are counted and
        reported on their own.
        """
        return not self.api_refusal and self.tier != "semantic_gap"


class InspectResponse(BaseModel):
    """Validated wire response. Unknown additive fields remain in raw_response."""

    verdict: Verdict
    output: str | None = None
    reason: str = ""
    suggest: str = ""
    stage: int = Field(ge=0, le=2)
    rule_id: str | None = None
    model: str | None = None
    latency_ms: dict[str, int | None]
    cached: bool
    decision_id: str = Field(min_length=1)
    protocol: Literal[1]

    @model_validator(mode="after")
    def check_response(self) -> Self:
        if self.verdict == "mask" and self.output is None:
            raise ValueError("mask requires authoritative output")
        if self.verdict == "drop" and not self.reason:
            raise ValueError("drop requires reason")
        if self.latency_ms.get("total") is None:
            raise ValueError("latency_ms.total is required")
        return self


class InspectResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    case_id: str
    case: InspectCase
    request: dict[str, Any]
    response: InspectResponse | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)
    pre_action_response: dict[str, Any] | None = None
    warmup_response: dict[str, Any] | None = None
    execution_time_ms: float = 0
    usage: Usage = Field(default_factory=Usage)
    cost: float | None = None
    cost_currency: str | None = None
    cost_source: CostSource = CostSource.UNAVAILABLE
    cost_unavailable_reason: str | None = "No valid inspection response"
    components: list[str] = Field(default_factory=list)
    score: int = Field(default=0, ge=0, le=1)
    text_checks_passed: bool | None = None
    error: str | None = None
    cache_valid: bool = True
    # API refusals and invalid measurements never count as security detections.
    security_eligible: bool = False
