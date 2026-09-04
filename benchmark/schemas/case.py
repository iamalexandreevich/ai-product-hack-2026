"""Benchmark case schema.

The benchmark boundary is ``human_req | assistant_tool_call``. Both map directly onto
``POST /v1/decide`` of the AgentGate service (see
``docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`` section 4.2):

    human_req                    -> user_request
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

ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


class Difficulty(StrEnum):
    """The five complexity levels every attack category must cover."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    ADVERSARIAL = "adversarial"
    REALISTIC_PRODUCTION = "realistic_production"


REQUIRED_DIFFICULTIES: frozenset[Difficulty] = frozenset(Difficulty)


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
    NONE = "none"


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
