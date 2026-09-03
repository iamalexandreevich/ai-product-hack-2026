"""Benchmark configuration: service endpoint, cost model, token extraction.

Everything deployment-specific comes from environment variables or CLI options; nothing
is hardcoded. The relevant variables are:

``SECURITY_SERVICE_URL``   base URL of the AgentGate service (also ``AGENTGATE_URL``)
``SECURITY_SERVICE_TOKEN`` bearer token (also ``AGENTGATE_TOKEN``)
``AGENTGATE_PROFILE_ID``   profile to evaluate against
``AGENTGATE_MODEL``        stage-2 model configuration name
``BENCHMARK_PRICING_TABLE`` path to a pricing YAML (see ``pricing.example.yaml``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SERVICE_URL = "http://127.0.0.1:8400"
DEFAULT_HARNESS = "bench"

# Candidate JSON paths for token usage inside the /v1/decide response.
#
# AgentGate v1 does NOT document any of these: section 4.3 of the design spec lists
# decision, reason, suggest, stage, rule_id, model, latency_ms, cached, decision_id and
# nothing else. They are probed anyway so that a later contract extension is picked up
# without a code change; when none of them resolves, cost stays ``None`` with an
# explicit reason. Cost is never guessed.
DEFAULT_INPUT_TOKEN_PATHS: tuple[str, ...] = (
    "usage.input_tokens",
    "usage.prompt_tokens",
    "model_usage.input_tokens",
    "model_raw_response.usage.input_tokens",
    "model_raw_response.usage.prompt_tokens",
)
DEFAULT_OUTPUT_TOKEN_PATHS: tuple[str, ...] = (
    "usage.output_tokens",
    "usage.completion_tokens",
    "model_usage.output_tokens",
    "model_raw_response.usage.output_tokens",
    "model_raw_response.usage.completion_tokens",
)
DEFAULT_TOTAL_TOKEN_PATHS: tuple[str, ...] = (
    "usage.total_tokens",
    "model_usage.total_tokens",
    "model_raw_response.usage.total_tokens",
)
DEFAULT_COST_PATHS: tuple[str, ...] = (
    "cost",
    "usage.cost",
    "model_usage.cost",
    "model_raw_response.usage.cost",
)


@dataclass(frozen=True)
class ModelPrice:
    input_per_1m: float
    output_per_1m: float


@dataclass(frozen=True)
class PricingTable:
    """Explicit, operator-supplied prices. Empty by default — no invented numbers."""

    currency: str = "USD"
    models: dict[str, ModelPrice] = field(default_factory=dict)
    source_path: str | None = None

    def lookup(self, *names: str | None) -> ModelPrice | None:
        for name in names:
            if name and name in self.models:
                return self.models[name]
        return None

    @property
    def is_empty(self) -> bool:
        return not self.models

    @classmethod
    def load(cls, path: Path) -> PricingTable:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: pricing table must be a YAML mapping")
        models: dict[str, ModelPrice] = {}
        for name, entry in (data.get("models") or {}).items():
            if not isinstance(entry, dict):
                raise ValueError(f"{path}: entry for {name!r} must be a mapping")
            try:
                models[str(name)] = ModelPrice(
                    input_per_1m=float(entry["input_per_1m"]),
                    output_per_1m=float(entry["output_per_1m"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path}: entry for {name!r} needs numeric input_per_1m and output_per_1m"
                ) from exc
        return cls(
            currency=str(data.get("currency", "USD")),
            models=models,
            source_path=str(path),
        )


@dataclass(frozen=True)
class ServiceConfig:
    """How to reach the AgentGate service under test."""

    url: str = DEFAULT_SERVICE_URL
    token: str | None = None
    timeout_s: float = 30.0
    harness: str = DEFAULT_HARNESS
    profile_id: str | None = None
    model: str | None = None
    resolve_model_metadata: bool = True
    input_token_paths: tuple[str, ...] = DEFAULT_INPUT_TOKEN_PATHS
    output_token_paths: tuple[str, ...] = DEFAULT_OUTPUT_TOKEN_PATHS
    total_token_paths: tuple[str, ...] = DEFAULT_TOTAL_TOKEN_PATHS
    cost_paths: tuple[str, ...] = DEFAULT_COST_PATHS
    pricing: PricingTable = field(default_factory=PricingTable)

    @property
    def decide_url(self) -> str:
        return f"{self.url.rstrip('/')}/v1/decide"

    def profile_url(self, profile_id: str) -> str:
        return f"{self.url.rstrip('/')}/v1/profiles/{profile_id}"

    @property
    def health_url(self) -> str:
        return f"{self.url.rstrip('/')}/healthz"


def service_config_from_env(**overrides: Any) -> ServiceConfig:
    """Build a :class:`ServiceConfig` from the environment, then apply CLI overrides."""
    url = (
        overrides.pop("url", None)
        or os.getenv("SECURITY_SERVICE_URL")
        or os.getenv("AGENTGATE_URL")
        or DEFAULT_SERVICE_URL
    )
    token = (
        overrides.pop("token", None)
        or os.getenv("SECURITY_SERVICE_TOKEN")
        or os.getenv("AGENTGATE_TOKEN")
    )
    profile_id = overrides.pop("profile_id", None) or os.getenv("AGENTGATE_PROFILE_ID")
    model = overrides.pop("model", None) or os.getenv("AGENTGATE_MODEL")

    pricing_path = overrides.pop("pricing_table_path", None) or os.getenv("BENCHMARK_PRICING_TABLE")
    pricing = PricingTable.load(Path(pricing_path)) if pricing_path else PricingTable()

    cleaned = {key: value for key, value in overrides.items() if value is not None}
    return ServiceConfig(
        url=url,
        token=token,
        profile_id=profile_id,
        model=model,
        pricing=pricing,
        **cleaned,
    )


def json_path(payload: Any, path: str) -> Any:
    """Resolve a dotted path inside a JSON-like structure; ``None`` when absent."""
    current = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current
