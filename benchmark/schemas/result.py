"""Result schema: one record per benchmark execution.

Carries the six required result dimensions:

1. execution time              -> execution_time_ms (+ service-reported latency_*)
2. request cost                -> cost / cost_source / cost_unavailable_reason
3. activated service parts     -> components_activated / components_source
4. service response type       -> service_result_type / service_raw_response
5. binary correctness score    -> score (deterministic, no LLM judge)
6. underlying model            -> model / provider / model_version / model_source

Fields the AgentGate v1 contract does not expose (token usage, cost, provider) are
represented as ``None`` together with an explicit ``*_source`` marker, so a later
contract extension fills them in without a schema change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.case import Difficulty, ServiceDecision


class ServiceResultType(StrEnum):
    """Normalised outcome of one benchmark execution.

    ``allow`` / ``deny`` / ``ask`` are the documented service decisions. ``error`` is a
    benchmark-side value only: the AgentGate contract (section 4.4) requires the service
    to answer HTTP 200 with ``ask`` even on internal failure, so ``error`` marks a
    transport failure, a timeout, a non-200 status, or an unparseable body — i.e. the
    service failing to meet its own contract or being unreachable.
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    ERROR = "error"


class CostSource(StrEnum):
    SERVICE_REPORTED = "service_reported"
    COMPUTED_FROM_TOKENS = "computed_from_tokens"
    UNAVAILABLE = "unavailable"


class ComponentsSource(StrEnum):
    SERVICE_REPORTED = "service_reported"
    DERIVED = "derived"
    UNAVAILABLE = "unavailable"


class ModelSource(StrEnum):
    SERVICE_REPORTED = "service_reported"
    PROFILE_LOOKUP = "profile_lookup"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"


class Usage(BaseModel):
    """Token usage, when the service exposes it. All fields are ``None`` in v1."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ServiceResponse(BaseModel):
    """Normalised view of one ``POST /v1/decide`` response."""

    model_config = ConfigDict(extra="forbid")

    result_type: ServiceResultType
    decision: ServiceDecision | None = None
    reason: str | None = None
    suggest: str | None = None
    stage: int | None = None
    rule_id: str | None = None
    cached: bool | None = None
    decision_id: str | None = None

    model: str | None = None
    provider: str | None = None
    model_version: str | None = None
    model_source: ModelSource = ModelSource.UNAVAILABLE

    latency_stage1_ms: float | None = None
    latency_stage2_ms: float | None = None
    latency_total_ms: float | None = None

    usage: Usage = Field(default_factory=Usage)
    cost: float | None = None
    cost_source: CostSource = CostSource.UNAVAILABLE
    cost_unavailable_reason: str | None = None

    components_activated: list[str] = Field(default_factory=list)
    components_source: ComponentsSource = ComponentsSource.UNAVAILABLE

    http_status: int | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    contract_violation: str | None = None


class RunConfig(BaseModel):
    """Configuration of one benchmark run; stored verbatim with the run."""

    model_config = ConfigDict(extra="forbid")

    service_url: str
    profile_id: str | None = None
    model: str | None = None
    harness: str = "bench"
    concurrency: int = 1
    timeout_s: float = 30.0
    strict_scoring: bool = False
    category_filter: list[str] = Field(default_factory=list)
    difficulty_filter: list[str] = Field(default_factory=list)
    case_filter: list[str] = Field(default_factory=list)
    dataset_path: str = "attacks/cases"
    pricing_table_path: str | None = None
    session_mode: str = "per_case"


class BenchmarkResult(BaseModel):
    """One executed case."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    case_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))

    attack_category: str
    attack_name: str
    difficulty: Difficulty
    is_benign: bool = False
    tags: list[str] = Field(default_factory=list)

    human_req: str
    assistant_tool_call: dict[str, Any]

    execution_time_ms: float
    service_latency_total_ms: float | None = None
    service_latency_stage1_ms: float | None = None
    service_latency_stage2_ms: float | None = None

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None
    cost_source: CostSource = CostSource.UNAVAILABLE
    cost_unavailable_reason: str | None = None

    components_activated: list[str] = Field(default_factory=list)
    components_source: ComponentsSource = ComponentsSource.UNAVAILABLE

    service_result_type: ServiceResultType
    service_raw_response: dict[str, Any] = Field(default_factory=dict)
    stage: int | None = None
    rule_id: str | None = None
    cached: bool | None = None
    decision_id: str | None = None

    expected_result_type: ServiceDecision
    acceptable_result_types: list[ServiceDecision] = Field(default_factory=list)
    score: int = Field(ge=0, le=1)
    score_explanation: str = ""

    expected_detection: bool = False
    detected: bool | None = None
    detection_correct: bool | None = None

    model: str | None = None
    provider: str | None = None
    model_version: str | None = None
    model_source: ModelSource = ModelSource.UNAVAILABLE

    error: str | None = None
    contract_violation: str | None = None
