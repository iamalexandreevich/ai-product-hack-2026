"""Pydantic v2 request/response models for the /v1/decide API.

These models are the shared contract between the service, the harness
adapters, and the benchmark work streams. Field names, types, enum values
*and the prose describing them* are exported as JSON Schema into
../../contracts by scripts/export_contracts.py and as OpenAPI by
scripts/export_openapi.py, so a field and its documentation are edited in
one place and cannot drift apart.
"""

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

USER_REQUEST_MAX_CHARS = 2048
RAW_MAX_BYTES = 32768
METADATA_MAX_BYTES = 16384
PROTOCOL = 1
HISTORY_MAX_TURNS = 200
HISTORY_MAX_BYTES = 131072
TURN_TOOL_MAX_CHARS = 64
TURN_CALL_ID_MAX_CHARS = 128


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
        description="The message text, the tool call as text, or the tool output."
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
    user_request: str = Field(
        description=(
            f"The user's last message, used as intent for the classifier. Not "
            f"rejected when long: the server truncates it to "
            f"{USER_REQUEST_MAX_CHARS} characters **keeping the tail**."
        )
    )
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
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            f"Arbitrary JSON, stored with the decision and echoed back by the "
            f"decision feed. It never reaches the decision logic or the stage-2 "
            f"prompt. At most {METADATA_MAX_BYTES} bytes when serialized as "
            f"UTF-8 JSON."
        ),
    )
    protocol: int = Field(
        default=PROTOCOL,
        description=(
            f"Protocol version the client speaks. This service speaks `{PROTOCOL}`; any "
            f"other value is refused fail-closed as `ask` with HTTP 200."
        ),
    )
    history: list[Turn] = Field(
        default_factory=list,
        description=(
            f"The dialogue that preceded this action, oldest turn first; the last "
            f"turn is the one immediately before the proposed action. At most "
            f"{HISTORY_MAX_TURNS} turns and {HISTORY_MAX_BYTES} bytes of UTF-8 text "
            f"summed over `content`, `tool` and `call_id`, "
            f"over which the request is refused fail-closed as `ask` with HTTP 200. "
            f"Rendered into the stage-2 prompt after per-role truncation; never seen "
            f"by stage 1. Empty for a v1 client, which changes nothing."
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
    def _truncate_user_request(cls, v: str) -> str:
        if len(v) > USER_REQUEST_MAX_CHARS:
            return v[-USER_REQUEST_MAX_CHARS:]
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(v, ensure_ascii=False).encode("utf-8"))
        if size > METADATA_MAX_BYTES:
            raise ValueError(f"metadata exceeds {METADATA_MAX_BYTES} bytes")
        return v

    @field_validator("protocol")
    @classmethod
    def _supported_protocol(cls, v: int) -> int:
        if v != PROTOCOL:
            raise ValueError(f"unsupported protocol {v}; this service speaks protocol {PROTOCOL}")
        return v

    @field_validator("history")
    @classmethod
    def _history_size(cls, v: list[Turn]) -> list[Turn]:
        if len(v) > HISTORY_MAX_TURNS:
            raise ValueError(f"history exceeds {HISTORY_MAX_TURNS} turns")
        size = sum(
            len(turn.content.encode("utf-8"))
            + len((turn.tool or "").encode("utf-8"))
            + len((turn.call_id or "").encode("utf-8"))
            for turn in v
        )
        if size > HISTORY_MAX_BYTES:
            raise ValueError(f"history exceeds {HISTORY_MAX_BYTES} bytes")
        return v

    @model_validator(mode="after")
    def _shell_requires_raw(self) -> "DecideRequest":
        if self.tool is Tool.shell and not self.raw.strip():
            raise ValueError("raw is required for tool=shell")
        return self


class LatencyMs(BaseModel):
    """Per-stage timing of one decision. A stage that did not run is `null`."""

    stage1: int | None = Field(
        default=None, description="Milliseconds spent in stage 1, or `null` if it did not run."
    )
    stage2: int | None = Field(
        default=None, description="Milliseconds spent in stage 2, or `null` if it did not run."
    )
    total: int = Field(description="Total milliseconds the service spent on this decision.")


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
