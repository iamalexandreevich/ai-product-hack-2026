"""Pydantic v2 request/response models for the /v1/decide API.

These models are the shared contract between the service, the harness
adapters, and the benchmark work streams. Field names, types, enum values
*and the prose describing them* are exported as JSON Schema into
../../contracts by scripts/export_contracts.py and as OpenAPI by
scripts/export_openapi.py, so a field and its documentation are edited in
one place and cannot drift apart.
"""

import hashlib
import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from agentgate.domain.usage import Usage, cost_amount
from agentgate.profiles.schema import ModelConfig

USER_REQUEST_MAX_CHARS = 2048
RAW_MAX_BYTES = 32768
METADATA_MAX_BYTES = 16384
PROTOCOL = 1
HISTORY_MAX_TURNS = 200
HISTORY_MAX_BYTES = 131072
TURN_TOOL_MAX_CHARS = 64
TURN_CALL_ID_MAX_CHARS = 128
IDEMPOTENCY_KEY_MAX_CHARS = 128
RULES_VERSION = 1
RULES_MAX_PATTERNS = 500
RULES_MAX_BYTES = 16384
RULE_PATTERN_MAX_CHARS = 200
CALL_ID_MAX_CHARS = 128
OUTPUT_MAX_BYTES = 262144


class HistoryTooLarge(ValueError):
    """The wire limit on `history` was exceeded; refused as `api.history-too-large`."""


class UnsupportedProtocol(ValueError):
    """`protocol` is not one this service speaks; refused as `api.unsupported-protocol`."""


class UnsupportedRules(ValueError):
    """`rules.version` is not one this service reads; refused as `api.unsupported-rules`."""


class RulesTooLarge(ValueError):
    """The wire limit on `rules` was exceeded; refused as `api.rules-too-large`."""


class OutputTooLarge(ValueError):
    """The wire limit on `output` was exceeded; the inspect call is refused as `drop`."""


class Tool(str, Enum):
    """Kind of action a harness can ask the gate about."""

    shell = "shell"
    file_write = "file_write"
    file_read = "file_read"
    network = "network"
    mcp_call = "mcp_call"


class DecisionKind(str, Enum):
    """The three possible answers. All of them are delivered with HTTP 200: `deny` and `ask` are decisions, not errors."""

    allow = "allow"
    deny = "deny"
    ask = "ask"


class InspectVerdict(str, Enum):
    """`pass` reaches the model untouched; `mask` — the caller substitutes `output`; `drop` — the result is withheld and `reason` shown instead."""

    pass_ = "pass"
    mask = "mask"
    drop = "drop"


class TurnRole(str, Enum):
    """What kind of dialogue turn this is."""

    human = "human"
    assistant = "assistant"
    toolcall = "toolcall"
    toolresult = "toolresult"


class Author(str, Enum):
    """Who produced a turn. A `human`-role turn authored by an `agent` is a
    parent model's message to a subagent, not the user's intent."""

    human = "human"
    agent = "agent"
    system = "system"


class Turn(BaseModel):
    """One turn of the dialogue that preceded the proposed action."""

    model_config = ConfigDict(frozen=True)

    role: TurnRole = Field(description="Kind of turn: `human`, `assistant`, `toolcall` or `toolresult`.")
    author: Author = Field(
        description=(
            "Who produced the turn. Only `human` marks the user's own words; a "
            "`human`-role turn with `author: agent` is text a parent model wrote "
            "for a subagent and is not treated as the user's intent."
        )
    )
    content: str = Field(
        description=(
            "The message text, the tool call as text, or the tool output. Visible turn "
            "text only: the agent's hidden reasoning (thinking blocks, scratchpads) must "
            "not be sent — the classifier is told it never sees it."
        )
    )
    tool: str | None = Field(
        default=None,
        max_length=TURN_TOOL_MAX_CHARS,
        description="Harness-native tool name for `toolcall` / `toolresult` turns.",
    )
    call_id: str | None = Field(
        default=None,
        max_length=TURN_CALL_ID_MAX_CHARS,
        description=(
            "Ties a `toolcall` to its `toolresult`. The same identifier the "
            "harness puts into its `Idempotency-Key`."
        ),
    )


USER_REQUEST_DESCRIPTION = (
    f"The user's last message, used as intent for the classifier. Not "
    f"rejected when long: the server truncates it to "
    f"{USER_REQUEST_MAX_CHARS} characters **keeping the tail**."
)
METADATA_DESCRIPTION = (
    f"Arbitrary JSON, stored with the decision and echoed back by the "
    f"decision feed. It never reaches the decision logic or the stage-2 "
    f"prompt. At most {METADATA_MAX_BYTES} bytes when serialized as "
    f"UTF-8 JSON."
)
PROTOCOL_DESCRIPTION = (
    f"Protocol version the client speaks. This service speaks `{PROTOCOL}`; any "
    f"other value is refused fail-closed as `ask` with HTTP 200."
)
HISTORY_DESCRIPTION = (
    f"The dialogue that preceded this action, oldest turn first; the last "
    f"turn is the one immediately before the proposed action. At most "
    f"{HISTORY_MAX_TURNS} turns and {HISTORY_MAX_BYTES} bytes of UTF-8 text "
    f"summed over `content`, `tool` and `call_id`, "
    f"over which the request is refused fail-closed as `ask` with HTTP 200. "
    f"Rendered into the stage-2 prompt after per-role truncation; never seen "
    f"by stage 1. Empty for a v1 client, which changes nothing."
)


def _truncate_user_request(v: str) -> str:
    if len(v) > USER_REQUEST_MAX_CHARS:
        return v[-USER_REQUEST_MAX_CHARS:]
    return v


def _check_metadata_size(v: dict[str, Any]) -> dict[str, Any]:
    size = len(json.dumps(v, ensure_ascii=False).encode("utf-8"))
    if size > METADATA_MAX_BYTES:
        raise ValueError(f"metadata exceeds {METADATA_MAX_BYTES} bytes")
    return v


def _check_protocol(v: int) -> int:
    if v != PROTOCOL:
        raise UnsupportedProtocol(f"unsupported protocol {v}; this service speaks protocol {PROTOCOL}")
    return v


def _check_history_size(v: list[Turn]) -> list[Turn]:
    if len(v) > HISTORY_MAX_TURNS:
        raise HistoryTooLarge(f"history exceeds {HISTORY_MAX_TURNS} turns")
    size = sum(
        len(turn.content.encode("utf-8", "surrogatepass"))
        + len((turn.tool or "").encode("utf-8", "surrogatepass"))
        + len((turn.call_id or "").encode("utf-8", "surrogatepass"))
        for turn in v
    )
    if size > HISTORY_MAX_BYTES:
        raise HistoryTooLarge(f"history exceeds {HISTORY_MAX_BYTES} bytes")
    return v


class McpArgs(BaseModel):
    server: str = Field(description="Name of the MCP server.")
    tool: str = Field(description="Name of the tool being called on that server.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Arguments passed to the MCP tool."
    )


class ActionArgs(BaseModel):
    cwd: str = Field(
        min_length=1,
        description="Absolute path of the agent's working directory. Must be non-empty.",
    )
    paths: list[str] = Field(
        default_factory=list,
        description=(
            "Paths the action touches, for `file_read` and `file_write`. "
            "**Ignored for `shell`** — the service extracts paths itself from the "
            "command's syntax tree. Sending them for `shell` is harmless."
        ),
    )
    domains: list[str] = Field(
        default_factory=list,
        description=(
            "Domains the action contacts, for `network`. **Ignored for `shell`** "
            "— the service extracts domains itself from the command."
        ),
    )
    mcp: McpArgs | None = Field(
        default=None,
        description="Server, tool and arguments of an MCP call. Used for `tool: mcp_call`.",
    )


class RuleSet(BaseModel):
    """The user's own deterministic policy, chosen at install time and edited by hand.

    Patterns are matched against the canonical form of the normalized action
    (argv joined by spaces, pipelines joined by ` | `) or against normalized
    paths -- never against the raw command line. `allow` can never override
    hard-deny or the server profile's denials.
    """

    model_config = ConfigDict(frozen=True)

    version: int = Field(
        description=f"Shape version. This service reads `{RULES_VERSION}`; any other value is refused as `ask`."
    )
    level: str = Field(
        default="custom",
        max_length=32,
        description="`low`, `medium`, `high`, or `custom` once edited. Recorded with the decision, not interpreted.",
    )
    allow: list[str] = Field(
        default_factory=list, description="Runs without asking, unless hard-deny or the profile forbids it."
    )
    ask: list[str] = Field(default_factory=list, description="Goes to the human.")
    deny: list[str] = Field(default_factory=list, description="Never runs.")

    @field_validator("version")
    @classmethod
    def _supported_version(cls, v: int) -> int:
        if v != RULES_VERSION:
            raise UnsupportedRules(f"unsupported rules version {v}; this service reads version {RULES_VERSION}")
        return v

    @model_validator(mode="after")
    def _rules_size(self) -> "RuleSet":
        patterns = [*self.allow, *self.ask, *self.deny]
        if len(patterns) > RULES_MAX_PATTERNS:
            raise RulesTooLarge(f"rules exceed {RULES_MAX_PATTERNS} patterns")
        if any(len(p) > RULE_PATTERN_MAX_CHARS for p in patterns):
            raise RulesTooLarge(f"a rule pattern exceeds {RULE_PATTERN_MAX_CHARS} chars")
        if sum(len(p.encode("utf-8", "surrogatepass")) for p in patterns) > RULES_MAX_BYTES:
            raise RulesTooLarge(f"rules exceed {RULES_MAX_BYTES} bytes")
        return self


class DecideRequest(BaseModel):
    """One proposed agent action, plus the intent behind it."""

    session_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Harness session identifier, at most 128 characters. Optional, but "
            "without it the service keeps no per-session counters and no allow "
            "cache, and the decision is stored with a null session."
        ),
    )
    harness: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Free-form name of the calling harness: `opencode`, `claude-code`, "
            "`codex`, `kilo`, `bench`, … 1-64 characters."
        ),
    )
    tool: Tool = Field(description="Kind of action being gated.")
    raw: str = Field(
        default="",
        description=(
            f"Raw payload as text. **Required and non-blank for `tool: shell`**, "
            f"where it is the command line stage 1 parses; for the other tools it "
            f"is the raw payload. At most {RAW_MAX_BYTES} **bytes** of UTF-8, not "
            f"characters — Cyrillic costs 2 bytes per character. Over the limit "
            f"the request is refused fail-closed as `ask` with HTTP 200."
        ),
    )
    args: ActionArgs = Field(description="Structured arguments of the action.")
    user_request: str = Field(description=USER_REQUEST_DESCRIPTION)
    profile_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Server-side policy profile to apply; defaults to `default`. The "
            "harness never sees the profile contents."
        ),
    )
    model: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Name of a model configuration inside the profile. Overrides the "
            "profile's `models.default`."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description=METADATA_DESCRIPTION)
    protocol: int = Field(default=PROTOCOL, description=PROTOCOL_DESCRIPTION)
    history: list[Turn] = Field(default_factory=list, description=HISTORY_DESCRIPTION)
    rules: RuleSet | None = Field(
        default=None,
        description=(
            "The user's deterministic policy for stage 1 (see `RuleSet`). Optional; "
            "absent means the server profile alone decides. `deny` beats the server "
            "allowlist, `allow` never beats hard-deny or the profile."
        ),
    )
    call_id: str | None = Field(
        default=None,
        max_length=CALL_ID_MAX_CHARS,
        description=(
            "Harness identifier of this tool invocation. Pairs the decision with the "
            "`POST /v1/inspect` of the same call in the feed."
        ),
    )

    @field_validator("raw")
    @classmethod
    def _raw_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > RAW_MAX_BYTES:
            raise ValueError(f"raw exceeds {RAW_MAX_BYTES} bytes")
        return v

    @field_validator("user_request")
    @classmethod
    def _validate_user_request(cls, v: str) -> str:
        return _truncate_user_request(v)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _check_metadata_size(v)

    @field_validator("protocol")
    @classmethod
    def _validate_protocol(cls, v: int) -> int:
        return _check_protocol(v)

    @field_validator("history")
    @classmethod
    def _validate_history(cls, v: list[Turn]) -> list[Turn]:
        return _check_history_size(v)

    @model_validator(mode="after")
    def _shell_requires_raw(self) -> "DecideRequest":
        if self.tool is Tool.shell and not self.raw.strip():
            raise ValueError("raw is required for tool=shell")
        return self

    def identity_digest(self) -> str:
        """sha256 of everything a decision depends on: the whole request minus
        `metadata`, which by contract never reaches the decision logic."""
        payload = self.model_dump_json(exclude={"metadata"})
        return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


class LatencyMs(BaseModel):
    """Per-stage timing of one decision. A stage that did not run is `null`."""

    stage1: int | None = Field(
        default=None, description="Milliseconds spent in stage 1, or `null` if it did not run."
    )
    stage2: int | None = Field(
        default=None, description="Milliseconds spent in stage 2, or `null` if it did not run."
    )
    total: int = Field(description="Total milliseconds the service spent on this decision.")


COST_CURRENCY = "USD"


class Cost(BaseModel):
    """Token usage of the stage-2 call, and its money cost when the operator
    priced the model in `profiles/schema.py::ModelConfig`.

    `amount` and `currency` are both absent from the wire form when the
    operator did not configure a price -- the tokens are still worth
    reporting, the money is not (see
    docs/superpowers/service/specs/response-cost-reporting.md). This whole
    field is absent from the response, not merely `null`, whenever stage 2
    was not called: `allow` from stage 1 and `allow` from the cache cost
    nothing to compute, which is a different fact than "we didn't count."
    """

    input_tokens: int = Field(description="Prompt tokens the provider billed for the stage-2 call.")
    output_tokens: int = Field(description="Completion tokens the provider billed for the stage-2 call.")
    reasoning_tokens: int = Field(
        default=0,
        description=(
            "Hidden reasoning tokens the provider billed, or `0` when the provider "
            "does not report them. A nonzero value while the model is configured "
            "with reasoning off means the setting did not take effect."
        ),
    )
    currency: str | None = Field(
        default=None, description=f"`{COST_CURRENCY}`, present only when `amount` is."
    )
    amount: float | None = Field(
        default=None,
        description="Money cost of the call at the operator's configured price; absent when no price is configured for this model.",
    )

    @model_serializer(mode="wrap")
    def _drop_amount_when_unpriced(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.amount is None:
            data.pop("amount", None)
            data.pop("currency", None)
        return data

    @classmethod
    def of(cls, usage: Usage, price_per_1m_input: float | None, price_per_1m_output: float | None) -> "Cost":
        amount = cost_amount(usage, price_per_1m_input, price_per_1m_output)
        return cls(
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            currency=COST_CURRENCY if amount is not None else None, amount=amount,
        )

    @classmethod
    def for_model(cls, usage: Usage | None, model_config: ModelConfig) -> "Cost | None":
        """The one place both classifiers turn a call's `Usage` into a `Cost`.

        `usage` is `None` when the provider's response carried no usage
        object at all -- there is nothing to report, not a free call.
        """
        if usage is None:
            return None
        return cls.of(usage, model_config.price_per_1m_input, model_config.price_per_1m_output)


class DecideResponse(BaseModel):
    """The gate's answer to one proposed action."""

    decision: DecisionKind = Field(
        description="The gate's answer. Always delivered with HTTP 200."
    )
    reason: str = Field(
        default="",
        description=(
            "Text handed to the agent verbatim as the tool result. Always present "
            "and non-empty for `deny` and `ask`; an empty string for `allow`."
        ),
    )
    suggest: str = Field(
        default="",
        description=(
            "A safe alternative to show the user. Empty string when there is "
            "nothing to suggest."
        ),
    )
    stage: int = Field(
        description=(
            "Who decided: `1` deterministic stage 1, `2` the LLM classifier, `0` "
            "an allow-cache hit or an API-level refusal such as an invalid body."
        )
    )
    rule_id: str | None = Field(
        default=None,
        description=(
            "Identifier of the stage-1 rule that fired (`hard-deny.exfil`, "
            "`profile.path`, `allowlist.readonly`, `escalation`, …), or `null` "
            "when stage 2 decided."
        ),
    )
    model: str | None = Field(
        default=None,
        description="Name of the model configuration used, if stage 2 ran; `null` otherwise.",
    )
    latency_ms: LatencyMs = Field(description="Latency per stage and in total.")
    cached: bool = Field(
        default=False,
        description=(
            "True when this answer came from the per-session `allow` cache. Only "
            "`allow` is ever cached; `deny` and `ask` never are."
        ),
    )
    decision_id: str = Field(
        description=(
            "ULID of the stored decision. Stable identifier for the record in the "
            "database and in the JSONL log."
        )
    )
    protocol: int = Field(
        default=PROTOCOL,
        description=f"Protocol version of this response. Always `{PROTOCOL}` in this release.",
    )
    cost: Cost | None = Field(
        default=None,
        description="Token usage and money cost of the stage-2 call. Absent when stage 2 did not run.",
    )

    @model_serializer(mode="wrap")
    def _drop_cost_when_stage2_did_not_run(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.cost is None:
            data.pop("cost", None)
        return data


class FileProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["file"]
    path: str


class ShellProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["shell"]
    command: str


class WebProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["web"]
    url: str


class McpProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["mcp"]
    server: str
    tool: str


class SubagentProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["subagent"]
    session_id: str


class UnknownProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["unknown"]


Provenance = Annotated[
    FileProvenance | ShellProvenance | WebProvenance | McpProvenance | SubagentProvenance | UnknownProvenance,
    Field(
        discriminator="kind",
        description=(
            "Where the text came from. An injection in a workspace file and one "
            "in a fetched page are different risks."
        ),
    ),
]


class InspectStatus(str, Enum):
    completed = "completed"
    error = "error"


class InspectRequest(BaseModel):
    """One tool result, held back from the model, plus where it came from."""

    session_id: str | None = Field(
        default=None, max_length=128, description="Same session as the `/v1/decide` call this result answers."
    )
    harness: str = Field(min_length=1, max_length=64)
    call_id: str = Field(
        min_length=1, max_length=CALL_ID_MAX_CHARS, description="Ties this result to the `/v1/decide` of the same invocation."
    )
    tool: Tool
    tool_name: str = Field(min_length=1, max_length=64, description="The harness's own name for the tool.")
    status: InspectStatus = Field(description="Error text is untrusted content too.")
    output: str = Field(
        description=(
            f"The result text as the model would see it. At most {OUTPUT_MAX_BYTES} bytes "
            f"of UTF-8; over the limit the result is refused as `drop`."
        )
    )
    provenance: Provenance
    args: ActionArgs
    user_request: str = Field(
        description=USER_REQUEST_DESCRIPTION + " Empty falls back to the last human-authored turn of `history`."
    )
    profile_id: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict, description=METADATA_DESCRIPTION)
    protocol: int = Field(default=PROTOCOL, description=PROTOCOL_DESCRIPTION)
    history: list[Turn] = Field(default_factory=list, description=HISTORY_DESCRIPTION)

    @field_validator("user_request")
    @classmethod
    def _validate_user_request(cls, v: str) -> str:
        return _truncate_user_request(v)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _check_metadata_size(v)

    @field_validator("protocol")
    @classmethod
    def _validate_protocol(cls, v: int) -> int:
        return _check_protocol(v)

    @field_validator("history")
    @classmethod
    def _validate_history(cls, v: list[Turn]) -> list[Turn]:
        return _check_history_size(v)

    @field_validator("output")
    @classmethod
    def _output_size(cls, v: str) -> str:
        if len(v.encode("utf-8", "surrogatepass")) > OUTPUT_MAX_BYTES:
            raise OutputTooLarge(f"output exceeds {OUTPUT_MAX_BYTES} bytes")
        return v

    def identity_digest(self) -> str:
        payload = self.model_dump_json(exclude={"metadata"})
        return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


SPAN_KINDS = ("instruction", "pipe-exec", "encoded", "invisible", "secret")
SpanKind = Literal["instruction", "pipe-exec", "encoded", "invisible", "secret"]
SpanSource = Literal["detector", "model"]


class Span(BaseModel):
    """One range of lines the verdict rewrote, by coordinates only -- never
    the text. Lines are 0-based indexes into `output.split("\\n")`,
    `line_end` inclusive."""

    line_start: int = Field(ge=0)
    line_end: int = Field(ge=0)
    kind: SpanKind
    source: SpanSource
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="Present only for `source: model`.")

    @model_serializer(mode="wrap")
    def _drop_absent_confidence(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.confidence is None:
            data.pop("confidence", None)
        return data


class InspectResponse(BaseModel):
    """The verdict on one tool result. Always HTTP 200."""

    verdict: InspectVerdict
    output: str | None = Field(
        default=None, description="Replacement text. Required and authoritative for `mask`; absent otherwise."
    )
    reason: str = Field(
        default="", description="Shown to the model on `drop`, recorded on `mask`, empty for `pass`."
    )
    suggest: str = Field(
        default="",
        description="A safe alternative to show the user. Empty string when there is nothing to suggest.",
    )
    stage: int
    rule_id: str | None = None
    model: str | None = None
    latency_ms: LatencyMs
    cached: bool = False
    decision_id: str
    protocol: int = Field(default=PROTOCOL)
    cost: Cost | None = Field(
        default=None,
        description="Token usage and money cost of the stage-2 call. Absent when stage 2 did not run.",
    )
    spans: list[Span] = Field(
        default_factory=list,
        description="Ranges the verdict masked or redacted, by line coordinates; never the text itself.",
    )
    redacted: int = Field(default=0, ge=0, description="How many secret values were redacted.")

    @model_validator(mode="after")
    def _mask_has_output(self) -> "InspectResponse":
        if self.verdict is InspectVerdict.mask and self.output is None:
            raise ValueError("mask requires output")
        return self

    @model_serializer(mode="wrap")
    def _drop_cost_when_stage2_did_not_run(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.cost is None:
            data.pop("cost", None)
        return data
