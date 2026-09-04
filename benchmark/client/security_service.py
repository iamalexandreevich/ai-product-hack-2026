"""Client for ``POST /v1/decide`` of the AgentGate service.

The request shape follows the design spec, section 4.2, exactly; the response is
normalised from section 4.3. Nothing outside those two sections is assumed:

* token usage and cost are not part of the contract, so they are probed through
  configurable JSON paths and otherwise reported as unavailable with a reason;
* component activation is not part of the contract either, so it is *derived* from the
  documented ``stage`` / ``rule_id`` / ``cached`` fields and flagged as ``derived``;
* ``provider`` and ``model_version`` are resolved, optionally, from
  ``GET /v1/profiles/{id}`` — the only documented place where a model configuration name
  maps to a concrete endpoint and model id.
"""

from __future__ import annotations

import logging
from typing import Any, Self

import httpx

from config import ServiceConfig, json_path
from schemas.case import ServiceDecision, ToolCall
from schemas.result import (
    ComponentsSource,
    CostSource,
    ModelSource,
    ServiceResponse,
    ServiceResultType,
    Usage,
)

logger = logging.getLogger(__name__)

# Mapping from the documented rule_id prefixes to stage-1 component names.
_RULE_PREFIX_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("hard-deny.", "stage1_hard_deny"),
    ("profile.", "stage1_profile"),
    ("allowlist.", "stage1_allowlist"),
    ("packages.", "stage1_packages"),
    ("escalation", "escalation"),
)

# Keys a future contract version might use to report components directly.
_SERVICE_COMPONENT_KEYS: tuple[str, ...] = ("components_activated", "components")


def build_decide_request(
    *,
    human_req: str,
    assistant_tool_call: ToolCall,
    harness: str,
    session_id: str | None = None,
    profile_id: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialise the benchmark boundary into a ``/v1/decide`` request body."""
    args: dict[str, Any] = {"cwd": assistant_tool_call.arguments.cwd}
    if assistant_tool_call.arguments.paths:
        args["paths"] = list(assistant_tool_call.arguments.paths)
    if assistant_tool_call.arguments.domains:
        args["domains"] = list(assistant_tool_call.arguments.domains)
    if assistant_tool_call.arguments.mcp is not None:
        args["mcp"] = assistant_tool_call.arguments.mcp.model_dump()

    body: dict[str, Any] = {
        "harness": harness,
        "tool": assistant_tool_call.tool.value,
        "raw": assistant_tool_call.raw,
        "args": args,
        "user_request": human_req,
    }
    if session_id:
        body["session_id"] = session_id
    if profile_id:
        body["profile_id"] = profile_id
    if model:
        body["model"] = model
    if metadata:
        body["metadata"] = metadata
    return body


def derive_components(payload: dict[str, Any]) -> tuple[list[str], ComponentsSource]:
    """Which parts of the service handled the request.

    If the service ever reports components itself, that value wins. Otherwise the set is
    derived from the documented pipeline (spec section 5): every request passes
    normalisation and stage 1; ``stage: 2`` means the LLM classifier was reached;
    ``cached: true`` / ``stage: 0`` mean the cascade was short-circuited.
    """
    for key in _SERVICE_COMPONENT_KEYS:
        reported = payload.get(key)
        if isinstance(reported, list) and reported:
            return [str(item) for item in reported], ComponentsSource.SERVICE_REPORTED

    stage = payload.get("stage")
    if not isinstance(stage, int):
        return [], ComponentsSource.UNAVAILABLE

    components: list[str] = []
    if payload.get("cached"):
        components.append("decision_cache")
    elif stage == 0:
        components.append("api_validation")

    if stage >= 1:
        components.extend(("normalizer", "stage1_rules"))
    if stage >= 2:
        components.append("stage2_llm")

    rule_id = payload.get("rule_id")
    if isinstance(rule_id, str):
        for prefix, component in _RULE_PREFIX_COMPONENTS:
            if rule_id.startswith(prefix) and component not in components:
                components.append(component)

    return components, ComponentsSource.DERIVED


# Stages that never reach a model, so their price is a real zero (spec section 5:
# 0 = allow-cache hit or API-level refusal, 1 = deterministic rules).
STAGES_WITHOUT_MODEL: frozenset[int] = frozenset({0, 1})


def extract_usage_and_cost(
    payload: dict[str, Any],
    config: ServiceConfig,
    *,
    model_names: tuple[str | None, ...] = (),
    stage: int | None = None,
) -> tuple[Usage, float | None, CostSource, str | None]:
    """Cost of one request: a number, or ``None`` plus the reason it is unknown.

    A price the service reported always wins. Failing that, a decision taken without
    calling a model costs ``0.0`` — derived from the ``stage`` the service itself
    reports, not guessed. Only a decision that did reach the classifier and whose price
    we cannot establish is ``None``.
    """
    usage = Usage(
        input_tokens=_first_int(payload, config.input_token_paths),
        output_tokens=_first_int(payload, config.output_token_paths),
        total_tokens=_first_int(payload, config.total_token_paths),
    )
    if usage.total_tokens is None and None not in (usage.input_tokens, usage.output_tokens):
        usage.total_tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)

    reported = _first_float(payload, config.cost_paths)
    if reported is not None:
        return usage, reported, CostSource.SERVICE_REPORTED, None

    if stage in STAGES_WITHOUT_MODEL:
        return usage, 0.0, CostSource.NO_MODEL_CALL, None

    if usage.input_tokens is None or usage.output_tokens is None:
        return (
            usage,
            None,
            CostSource.UNAVAILABLE,
            (
                "AgentGate /v1/decide does not report token usage (design spec 4.3), "
                "so cost cannot be computed"
            ),
        )

    price = config.pricing.lookup(*model_names)
    if price is None:
        which = ", ".join(name for name in model_names if name) or "unknown model"
        hint = "no pricing table configured" if config.pricing.is_empty else "model not in table"
        return usage, None, CostSource.UNAVAILABLE, f"{hint} for {which}"

    cost = (
        usage.input_tokens * price.input_per_1m + usage.output_tokens * price.output_per_1m
    ) / 1e6
    return usage, cost, CostSource.COMPUTED_FROM_TOKENS, None


def extract_currency(payload: dict[str, Any], config: ServiceConfig) -> str | None:
    """Currency of a service-reported price, when the service names one.

    Kept separate from :func:`extract_usage_and_cost` so that the price and its unit are
    never merged into one guessed value: a price with no currency stays a price with no
    currency rather than being assumed to be USD.
    """
    for path in config.currency_paths:
        value = json_path(payload, path)
        if isinstance(value, str) and value:
            return value
    return None


def normalize_response(
    payload: Any,
    *,
    http_status: int,
    config: ServiceConfig,
) -> ServiceResponse:
    """Turn a raw HTTP response into a :class:`ServiceResponse`."""
    if http_status == 401:
        return ServiceResponse(
            result_type=ServiceResultType.ERROR,
            http_status=http_status,
            raw_response=payload if isinstance(payload, dict) else {},
            error="401 Unauthorized: missing or invalid bearer token",
        )

    if not isinstance(payload, dict):
        return ServiceResponse(
            result_type=ServiceResultType.ERROR,
            http_status=http_status,
            raw_response={},
            error=f"response body is not a JSON object (HTTP {http_status})",
        )

    contract_violation: str | None = None
    if http_status != 200:
        contract_violation = (
            f"HTTP {http_status}: the contract (spec 4.4) requires HTTP 200 with a decision "
            "for every failure except authentication"
        )

    raw_decision = payload.get("decision")
    try:
        decision = ServiceDecision(raw_decision)
    except ValueError:
        return ServiceResponse(
            result_type=ServiceResultType.ERROR,
            http_status=http_status,
            raw_response=payload,
            error=f"unknown decision value {raw_decision!r}",
            contract_violation=contract_violation
            or f"decision {raw_decision!r} outside allow|deny|ask",
        )

    latency = payload.get("latency_ms")
    latency = latency if isinstance(latency, dict) else {}

    components, components_source = derive_components(payload)
    model = payload.get("model")
    model = model if isinstance(model, str) and model else None
    stage = payload.get("stage") if isinstance(payload.get("stage"), int) else None
    usage, cost, cost_source, cost_reason = extract_usage_and_cost(
        payload, config, model_names=(model,), stage=stage
    )
    currency: str | None = None
    if cost is not None and cost_source is not CostSource.NO_MODEL_CALL:
        currency = extract_currency(payload, config) or (
            config.pricing.currency if cost_source is CostSource.COMPUTED_FROM_TOKENS else None
        )

    if model is not None:
        model_source = ModelSource.SERVICE_REPORTED
    elif payload.get("stage") in (0, 1):
        # Stage 1 and cache hits legitimately never touch a model.
        model_source = ModelSource.NOT_APPLICABLE
    else:
        model_source = ModelSource.UNAVAILABLE

    return ServiceResponse(
        result_type=ServiceResultType(decision.value),
        decision=decision,
        reason=_as_str(payload.get("reason")),
        suggest=_as_str(payload.get("suggest")),
        stage=stage,
        rule_id=_as_str(payload.get("rule_id")),
        cached=payload.get("cached") if isinstance(payload.get("cached"), bool) else None,
        decision_id=_as_str(payload.get("decision_id")),
        model=model,
        model_source=model_source,
        latency_stage1_ms=_as_float(latency.get("stage1")),
        latency_stage2_ms=_as_float(latency.get("stage2")),
        latency_total_ms=_as_float(latency.get("total")),
        usage=usage,
        cost=cost,
        cost_currency=currency,
        cost_source=cost_source,
        cost_unavailable_reason=cost_reason,
        components_activated=components,
        components_source=components_source,
        http_status=http_status,
        raw_response=payload,
        contract_violation=contract_violation,
    )


class SecurityServiceClient:
    """Async client for the AgentGate API.

    A transport failure never raises out of :meth:`evaluate`: it is normalised into a
    ``ServiceResultType.ERROR`` response so that one broken case cannot abort a run.
    """

    def __init__(self, config: ServiceConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client
        self._owns_client = client is None
        self._profile_models: dict[str, dict[str, Any]] | None = None
        self._profile_lookup_failed = False

    async def __aenter__(self) -> Self:
        if self._client is None:
            headers = {"content-type": "application/json"}
            if self.config.token:
                headers["authorization"] = f"Bearer {self.config.token}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_s),
                headers=headers,
            )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SecurityServiceClient must be used as an async context manager")
        return self._client

    async def evaluate(
        self,
        human_req: str,
        assistant_tool_call: ToolCall,
        *,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ServiceResponse:
        """Send one benchmark case to ``POST /v1/decide``."""
        body = build_decide_request(
            human_req=human_req,
            assistant_tool_call=assistant_tool_call,
            harness=self.config.harness,
            session_id=session_id,
            profile_id=self.config.profile_id,
            model=self.config.model,
            metadata=metadata,
        )

        try:
            http_response = await self.client.post(self.config.decide_url, json=body)
        except httpx.TimeoutException as exc:
            return ServiceResponse(
                result_type=ServiceResultType.ERROR,
                error=f"timeout after {self.config.timeout_s}s: {exc!r}",
            )
        except httpx.HTTPError as exc:
            return ServiceResponse(
                result_type=ServiceResultType.ERROR,
                error=f"transport error: {exc!r}",
            )

        try:
            payload: Any = http_response.json()
        except ValueError:
            payload = None

        response = normalize_response(
            payload,
            http_status=http_response.status_code,
            config=self.config,
        )
        if self.config.resolve_model_metadata and response.model:
            await self._enrich_model_metadata(response)
        return response

    async def healthz(self) -> tuple[bool, dict[str, Any] | None]:
        """Liveness probe; used by the CLI before a run."""
        try:
            response = await self.client.get(self.config.health_url)
        except httpx.HTTPError as exc:
            logger.debug("health check failed: %r", exc)
            return False, None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response.status_code == 200, payload if isinstance(payload, dict) else None

    async def _enrich_model_metadata(self, response: ServiceResponse) -> None:
        """Resolve provider and concrete model id from ``GET /v1/profiles/{id}``.

        ``model`` in a decision is the *configuration name* from the profile, not the
        model id. The profile maps it to ``base_url`` and ``model``; the host of
        ``base_url`` is the closest thing to a provider the contract exposes. When the
        endpoint is unavailable both stay ``None`` — they are never guessed.
        """
        configs = await self._load_profile_models()
        entry = configs.get(response.model or "")
        if not isinstance(entry, dict):
            return
        base_url = entry.get("base_url")
        model_id = entry.get("model")
        if isinstance(base_url, str) and base_url:
            response.provider = httpx.URL(base_url).host or None
        if isinstance(model_id, str) and model_id:
            response.model_version = model_id
        if response.provider or response.model_version:
            response.model_source = ModelSource.PROFILE_LOOKUP

    async def _load_profile_models(self) -> dict[str, dict[str, Any]]:
        if self._profile_models is not None:
            return self._profile_models
        if self._profile_lookup_failed:
            return {}

        profile_id = self.config.profile_id or "default"
        try:
            response = await self.client.get(self.config.profile_url(profile_id))
            payload = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("profile lookup failed: %r", exc)
            payload = None

        if not isinstance(payload, dict):
            self._profile_lookup_failed = True
            return {}

        configs = json_path(payload, "models.configs")
        self._profile_models = configs if isinstance(configs, dict) else {}
        return self._profile_models


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value != "" else None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _first_int(payload: dict[str, Any], paths: tuple[str, ...]) -> int | None:
    for path in paths:
        value = json_path(payload, path)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _first_float(payload: dict[str, Any], paths: tuple[str, ...]) -> float | None:
    for path in paths:
        value = json_path(payload, path)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None
