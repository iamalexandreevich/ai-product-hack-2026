"""Benchmark case schema.

The benchmark boundary is ``human_req | assistant_tool_call``. Both map directly onto
``POST /v1/decide`` of the AgentGate service (see
``docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`` section 4.2):

    human_req                    -> user_request
    history                      -> history          (v2, optional)
    assistant_tool_call.tool     -> tool
    assistant_tool_call.raw      -> raw
    assistant_tool_call.arguments-> args {cwd, paths, domains, mcp}

No field here invents service behaviour: every value that leaves the benchmark is a
documented request field, and every expectation is expressed in the documented
``allow | deny | ask`` decision space.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.rules import RuleSet

ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

# Wire limits on ``history``, mirrored from the service
# (service/agentgate/api/schemas.py). A case that exceeds them is refused at stage 0
# as ``api.history-too-large`` and therefore measures nothing, so it must not exist.
HISTORY_MAX_TURNS = 200
HISTORY_MAX_BYTES = 131_072
TURN_TOOL_MAX_CHARS = 64
TURN_CALL_ID_MAX_CHARS = 128


class Difficulty(StrEnum):
    """Complexity levels a case can carry.

    The first five are required in every category. ``ultra_hard`` is optional: it is
    reserved for cases that combine several techniques at once and assume a defender
    who already handles the other five.
    """

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    ADVERSARIAL = "adversarial"
    REALISTIC_PRODUCTION = "realistic_production"
    ULTRA_HARD = "ultra_hard"


REQUIRED_DIFFICULTIES: frozenset[Difficulty] = frozenset(
    {
        Difficulty.EASY,
        Difficulty.MEDIUM,
        Difficulty.HARD,
        Difficulty.ADVERSARIAL,
        Difficulty.REALISTIC_PRODUCTION,
    }
)
OPTIONAL_DIFFICULTIES: frozenset[Difficulty] = frozenset(Difficulty) - REQUIRED_DIFFICULTIES

UNCAPPED_CATEGORIES: frozenset[str] = frozenset({"benign_utility"})
"""Categories a difficulty level may repeat in, so their size is not capped.

"One case per level" exists to stop an *attack* category from being padded with easy
variations of the same technique until the detection rate looks better than it is. A
control group is the opposite case: it carries no attack, nothing about it can be
inflated, and its whole job is to measure FP-rate and Friction — whose resolution is
1/N. Five cases mean a single ``ask`` reads as 20% false positives; thirty mean ~3%.

Named explicitly rather than derived from the data (say, "every case here is benign"),
so that a category cannot silently lose its anti-padding guard because someone set
``attack_location: none`` in the wrong file.
"""


class DatasetSource(StrEnum):
    """Where a case came from.

    ``team``     written inside this repository (possibly informed by published
                 research, but not copied from an external corpus);
    ``baseline`` imported from an external reference attack set (RedCode, AgentDojo,
                 InjecAgent, …).

    The distinction exists so that ASR can be reported for the two populations
    separately: a service tuned against our own cases must still be measurable on an
    independent set. Every case authored so far is ``team``; nothing in the repository
    is labelled ``baseline`` until such a corpus is actually imported.
    """

    BASELINE = "baseline"
    TEAM = "team"


class AttackLocation(StrEnum):
    """Where the malicious content sits inside the benchmark boundary."""

    HUMAN_REQ = "human_req"
    ASSISTANT_TOOL_CALL = "assistant_tool_call"
    HISTORY = "history"
    NONE = "none"


class TurnRole(StrEnum):
    """``role`` enum of a history turn (service contract v2, section 3.1)."""

    HUMAN = "human"
    ASSISTANT = "assistant"
    TOOLCALL = "toolcall"
    TOOLRESULT = "toolresult"


class TurnAuthor(StrEnum):
    """``author`` enum of a history turn.

    The distinction the trust-escalation cases attack: ``role: human`` with
    ``author: agent`` is a parent model's text addressed to a subagent, not a human's
    intent. Only ``human``/``human`` is the user speaking.
    """

    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class HistoryTurn(BaseModel):
    """One turn of the dialogue that preceded the proposed action.

    Mirrors ``Turn`` of ``POST /v1/decide`` field for field; nothing is added.
    """

    model_config = ConfigDict(extra="forbid")

    role: TurnRole
    author: TurnAuthor
    content: str
    tool: str | None = Field(default=None, max_length=TURN_TOOL_MAX_CHARS)
    call_id: str | None = Field(default=None, max_length=TURN_CALL_ID_MAX_CHARS)

    def wire_bytes(self) -> int:
        """Bytes this turn spends of the request budget, counted as the service counts."""
        return (
            len(self.content.encode("utf-8", "surrogatepass"))
            + len((self.tool or "").encode("utf-8", "surrogatepass"))
            + len((self.call_id or "").encode("utf-8", "surrogatepass"))
        )


class ToolName(StrEnum):
    """``tool`` enum of POST /v1/decide. Do not extend without a contract change."""

    SHELL = "shell"
    FILE_WRITE = "file_write"
    FILE_READ = "file_read"
    NETWORK = "network"
    MCP_CALL = "mcp_call"


class ServiceDecision(StrEnum):
    """``decision`` enum of the AgentGate response."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class McpCall(BaseModel):
    """``args.mcp`` payload for ``tool: mcp_call``."""

    model_config = ConfigDict(extra="forbid")

    server: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallArguments(BaseModel):
    """``args`` of POST /v1/decide."""

    model_config = ConfigDict(extra="forbid")

    cwd: str = Field(min_length=1, description="Absolute working directory, required by the API")
    paths: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    mcp: McpCall | None = None


class ToolCall(BaseModel):
    """The assistant's proposed tool call — the action AgentGate must rule on."""

    model_config = ConfigDict(extra="forbid")

    tool: ToolName
    raw: str = Field(default="", max_length=32_768)
    arguments: ToolCallArguments

    @model_validator(mode="after")
    def _check_tool_specific_requirements(self) -> Self:
        if len(self.raw.encode("utf-8")) > 32768:
            raise ValueError("raw exceeds 32768 UTF-8 bytes")
        if self.tool is ToolName.SHELL and not self.raw.strip():
            raise ValueError("raw is required for tool=shell (API contract 4.2)")
        if self.tool is ToolName.MCP_CALL and self.arguments.mcp is None:
            raise ValueError("args.mcp is required for tool=mcp_call (API contract 4.2)")
        if self.tool in (ToolName.FILE_READ, ToolName.FILE_WRITE) and not self.arguments.paths:
            raise ValueError("args.paths is required for file_read / file_write")
        if self.tool is ToolName.NETWORK and not self.arguments.domains:
            raise ValueError("args.domains is required for tool=network")
        return self


class BenchmarkCase(BaseModel):
    """One benchmark case. Serialised as one YAML file under ``attacks/cases/<category>/``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    attack_category: str = Field(min_length=1)
    attack_name: str = Field(min_length=1)
    difficulty: Difficulty
    description: str = Field(min_length=1)
    attack_location: Annotated[list[AttackLocation], Field(min_length=1)]

    human_req: str = Field(min_length=1)
    assistant_tool_call: ToolCall

    # The dialogue that preceded the action, oldest turn first. Empty for a case that
    # lives at the v1 boundary, which is every case written before v2 of the service.
    # Cross-cutting on purpose: any category may use it, not only the multi-turn one.
    history: list[HistoryTurn] = Field(default_factory=list)
    rules: RuleSet | None = None
    call_id: str | None = Field(default=None, min_length=1, max_length=128)
    enforce_pipeline: bool = False

    attack_payload: str | None = None

    expected_behavior: Annotated[list[str], Field(min_length=1)]
    expected_service_result: ServiceDecision
    acceptable_service_results: list[ServiceDecision] = Field(default_factory=list)
    expected_detection: bool

    # Optional, recorded but never part of the binary V1 score: which part of the
    # documented pipeline *should* have caught the case.
    expected_stage: int | None = Field(default=None, ge=0, le=2)
    expected_rule_id_prefix: str | None = None

    # Population this case belongs to. Defaults to ``team``: every case in
    # attacks/cases/ was written here. An imported baseline corpus sets it explicitly.
    dataset_source: DatasetSource = DatasetSource.TEAM

    tags: list[str] = Field(default_factory=list)
    source_references: list[str] = Field(default_factory=list)

    # Filled by the loader, never stored in YAML.
    source_path: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not ID_PATTERN.match(self.id):
            raise ValueError(f"id {self.id!r} must match {ID_PATTERN.pattern}")

        locations = set(self.attack_location)
        if AttackLocation.NONE in locations and len(locations) > 1:
            raise ValueError("attack_location 'none' cannot be combined with other locations")

        if len(self.history) > HISTORY_MAX_TURNS:
            raise ValueError(f"history exceeds {HISTORY_MAX_TURNS} turns, the service wire limit")
        size = sum(turn.wire_bytes() for turn in self.history)
        if size > HISTORY_MAX_BYTES:
            raise ValueError(f"history exceeds {HISTORY_MAX_BYTES} bytes, the service wire limit")
        if AttackLocation.HISTORY in locations and not self.history:
            raise ValueError("attack_location 'history' requires a non-empty history")

        is_benign = locations == {AttackLocation.NONE}
        if is_benign:
            if self.expected_detection:
                raise ValueError("benign case (attack_location=none) cannot expect detection")
            if self.attack_payload:
                raise ValueError("benign case (attack_location=none) must not carry attack_payload")
        else:
            if not self.attack_payload:
                raise ValueError("attack_payload is required when attack_location is not 'none'")
            if self.expected_detection and self.expected_service_result is ServiceDecision.ALLOW:
                raise ValueError("expected_detection=true is inconsistent with expected allow")

        if not self.acceptable_service_results:
            self.acceptable_service_results = [self.expected_service_result]
        elif self.expected_service_result not in self.acceptable_service_results:
            raise ValueError("expected_service_result must be listed in acceptable_service_results")

        return self

    @property
    def is_benign(self) -> bool:
        return set(self.attack_location) == {AttackLocation.NONE}
