"""Request serialisation, response normalisation, cost and model metadata."""

from __future__ import annotations

import asyncio
import copy

import httpx

from client.security_service import (
    SecurityServiceClient,
    build_decide_request,
    derive_components,
    extract_usage_and_cost,
    normalize_response,
)
from config import ModelPrice, PricingTable, ServiceConfig
from schemas.case import BenchmarkCase, ToolCall
from schemas.result import ComponentsSource, CostSource, ModelSource, ServiceResultType
from tests.conftest import (
    DECISION_ALLOW_STAGE2,
    DECISION_ALLOW_STAGE2_WITH_COST,
    DECISION_DENY,
    VALID_CASE,
)

# -- request ----------------------------------------------------------------


def test_request_maps_benchmark_boundary_to_contract(sample_case: BenchmarkCase):
    body = build_decide_request(
        human_req=sample_case.human_req,
        assistant_tool_call=sample_case.assistant_tool_call,
        harness="bench",
        session_id="bench-1",
        profile_id="default-dev",
        model="sonnet",
        metadata={"run_id": "r1"},
    )
    assert body["user_request"] == sample_case.human_req
    assert body["tool"] == "shell"
    assert body["raw"] == sample_case.assistant_tool_call.raw
    assert body["args"] == {"cwd": "/home/dev/repo"}
    assert body["harness"] == "bench"
    assert body["session_id"] == "bench-1"
    assert body["profile_id"] == "default-dev"
    assert body["model"] == "sonnet"
    assert body["metadata"] == {"run_id": "r1"}


def test_request_omits_optional_fields_when_unset(sample_case: BenchmarkCase):
    body = build_decide_request(
        human_req=sample_case.human_req,
        assistant_tool_call=sample_case.assistant_tool_call,
        harness="bench",
    )
    assert "session_id" not in body
    assert "profile_id" not in body
    assert "model" not in body
    assert "metadata" not in body


def test_request_carries_mcp_and_paths():
    call = ToolCall.model_validate(
        {
            "tool": "mcp_call",
            "raw": "read_file",
            "arguments": {
                "cwd": "/home/dev/repo",
                "paths": ["/home/dev/repo/a.txt"],
                "domains": ["example.net"],
                "mcp": {"server": "fs", "tool": "read_file", "arguments": {"path": "/etc/passwd"}},
            },
        }
    )
    body = build_decide_request(human_req="x", assistant_tool_call=call, harness="bench")
    assert body["args"]["mcp"]["server"] == "fs"
    assert body["args"]["paths"] == ["/home/dev/repo/a.txt"]
    assert body["args"]["domains"] == ["example.net"]


def test_request_carries_the_dialogue_history_in_contract_shape(history_case: BenchmarkCase):
    body = build_decide_request(
        human_req=history_case.human_req,
        assistant_tool_call=history_case.assistant_tool_call,
        harness="bench",
        history=history_case.history,
    )
    assert len(body["history"]) == 5
    assert body["history"][0] == {
        "role": "human",
        "author": "human",
        "content": "help me with the release",
        "tool": None,
        "call_id": None,
    }
    # Exactly the five keys of the contract Turn, no benchmark invention.
    assert all(
        set(turn) == {"role", "author", "content", "tool", "call_id"} for turn in body["history"]
    )
    # ``protocol`` stays unsent: the service speaks 1 and that is the default.
    assert "protocol" not in body


def test_request_without_history_omits_the_key_entirely(sample_case: BenchmarkCase):
    """A v1-boundary case must produce the v1 request, byte for byte."""
    for history in (None, []):
        body = build_decide_request(
            human_req=sample_case.human_req,
            assistant_tool_call=sample_case.assistant_tool_call,
            harness="bench",
            history=history,
        )
        assert "history" not in body


# -- response normalisation --------------------------------------------------


def test_normalize_deny(service_config: ServiceConfig):
    response = normalize_response(DECISION_DENY, http_status=200, config=service_config)
    assert response.result_type is ServiceResultType.DENY
    assert response.rule_id == "hard-deny.exfil"
    assert response.stage == 1
    assert response.latency_total_ms == 1.1
    assert response.contract_violation is None


def test_normalize_allow_stage2(service_config: ServiceConfig):
    response = normalize_response(DECISION_ALLOW_STAGE2, http_status=200, config=service_config)
    assert response.result_type is ServiceResultType.ALLOW
    assert response.model == "sonnet"
    assert response.model_source is ModelSource.SERVICE_REPORTED
    assert response.latency_stage2_ms == 84.0


def test_normalize_unknown_decision_is_error(service_config: ServiceConfig):
    payload = copy.deepcopy(DECISION_DENY) | {"decision": "block"}
    response = normalize_response(payload, http_status=200, config=service_config)
    assert response.result_type is ServiceResultType.ERROR
    assert "block" in (response.error or "")
    assert response.contract_violation


def test_normalize_non_json_body_is_error(service_config: ServiceConfig):
    response = normalize_response(None, http_status=200, config=service_config)
    assert response.result_type is ServiceResultType.ERROR
    assert "not a JSON object" in (response.error or "")


def test_normalize_401_is_error(service_config: ServiceConfig):
    response = normalize_response({}, http_status=401, config=service_config)
    assert response.result_type is ServiceResultType.ERROR
    assert "401" in (response.error or "")


def test_non_200_with_decision_is_flagged_as_contract_violation(service_config: ServiceConfig):
    response = normalize_response(DECISION_DENY, http_status=500, config=service_config)
    assert response.result_type is ServiceResultType.DENY
    assert "HTTP 500" in (response.contract_violation or "")


# -- components --------------------------------------------------------------


def test_components_derived_from_stage_one():
    components, source = derive_components(DECISION_DENY)
    assert source is ComponentsSource.DERIVED
    assert components == ["normalizer", "stage1_rules", "stage1_hard_deny"]


def test_components_derived_for_stage_two():
    components, source = derive_components(DECISION_ALLOW_STAGE2)
    assert source is ComponentsSource.DERIVED
    assert "stage2_llm" in components


def test_components_for_cache_hit():
    components, _ = derive_components({"stage": 0, "cached": True, "decision": "allow"})
    assert components == ["decision_cache"]


def test_components_unavailable_without_stage():
    components, source = derive_components({"decision": "allow"})
    assert components == []
    assert source is ComponentsSource.UNAVAILABLE


def test_components_prefer_service_reported():
    components, source = derive_components(
        {"stage": 2, "components_activated": ["policy_engine", "llm"]}
    )
    assert source is ComponentsSource.SERVICE_REPORTED
    assert components == ["policy_engine", "llm"]


def test_escalation_component_is_recognised():
    components, _ = derive_components({"stage": 1, "rule_id": "escalation"})
    assert "escalation" in components


# -- cost --------------------------------------------------------------------


def test_cost_unavailable_when_service_reports_no_tokens(service_config: ServiceConfig):
    usage, cost, source, reason = extract_usage_and_cost(DECISION_ALLOW_STAGE2, service_config)
    assert usage.input_tokens is None
    assert cost is None
    assert source is CostSource.UNAVAILABLE
    assert "does not report token usage" in (reason or "")


def test_cost_computed_from_tokens_with_pricing_table():
    config = ServiceConfig(
        pricing=PricingTable(models={"sonnet": ModelPrice(input_per_1m=3.0, output_per_1m=15.0)})
    )
    payload = DECISION_ALLOW_STAGE2 | {"usage": {"input_tokens": 1_000, "output_tokens": 200}}
    usage, cost, source, reason = extract_usage_and_cost(payload, config, model_names=("sonnet",))
    assert usage.total_tokens == 1_200
    assert cost == (1_000 * 3.0 + 200 * 15.0) / 1e6
    assert source is CostSource.COMPUTED_FROM_TOKENS
    assert reason is None


def test_cost_unavailable_when_model_missing_from_pricing_table():
    config = ServiceConfig(
        pricing=PricingTable(models={"other": ModelPrice(input_per_1m=1.0, output_per_1m=1.0)})
    )
    payload = DECISION_ALLOW_STAGE2 | {"usage": {"input_tokens": 10, "output_tokens": 2}}
    _, cost, source, reason = extract_usage_and_cost(payload, config, model_names=("sonnet",))
    assert cost is None
    assert source is CostSource.UNAVAILABLE
    assert "model not in table" in (reason or "")


def test_service_reported_cost_wins(service_config: ServiceConfig):
    payload = DECISION_ALLOW_STAGE2 | {"cost": 0.0017}
    _, cost, source, reason = extract_usage_and_cost(payload, service_config)
    assert cost == 0.0017
    assert source is CostSource.SERVICE_REPORTED
    assert reason is None


def test_pricing_table_loads_from_yaml(tmp_path):
    path = tmp_path / "pricing.yaml"
    path.write_text(
        "currency: USD\nmodels:\n  sonnet:\n    input_per_1m: 3\n    output_per_1m: 15\n",
        encoding="utf-8",
    )
    table = PricingTable.load(path)
    assert table.lookup("sonnet") == ModelPrice(3.0, 15.0)
    assert table.lookup("missing") is None
    assert table.is_empty is False


# -- model metadata ----------------------------------------------------------


def test_model_not_applicable_for_stage_one(service_config: ServiceConfig):
    response = normalize_response(DECISION_DENY, http_status=200, config=service_config)
    assert response.model is None
    assert response.model_source is ModelSource.NOT_APPLICABLE
    assert response.provider is None
    assert response.model_version is None


def test_model_metadata_resolved_from_profile():
    config = ServiceConfig(url="http://127.0.0.1:8400", resolve_model_metadata=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/decide":
            return httpx.Response(200, json=DECISION_ALLOW_STAGE2)
        if request.url.path == "/v1/profiles/default":
            return httpx.Response(
                200,
                json={
                    "id": "default",
                    "models": {
                        "default": "sonnet",
                        "configs": {
                            "sonnet": {
                                "base_url": "https://openrouter.ai/api/v1",
                                "model": "anthropic/claude-sonnet-4-6",
                            }
                        },
                    },
                },
            )
        return httpx.Response(404, json={})

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = SecurityServiceClient(config, client=http_client)
            call = BenchmarkCase.model_validate(copy.deepcopy(VALID_CASE)).assistant_tool_call
            return await client.evaluate("do it", call)

    response = asyncio.run(scenario())
    assert response.provider == "openrouter.ai"
    assert response.model_version == "anthropic/claude-sonnet-4-6"
    assert response.model_source is ModelSource.PROFILE_LOOKUP


def test_model_metadata_absent_when_profile_lookup_fails():
    config = ServiceConfig(url="http://127.0.0.1:8400", resolve_model_metadata=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/decide":
            return httpx.Response(200, json=DECISION_ALLOW_STAGE2)
        return httpx.Response(503, text="unavailable")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = SecurityServiceClient(config, client=http_client)
            call = BenchmarkCase.model_validate(copy.deepcopy(VALID_CASE)).assistant_tool_call
            return await client.evaluate("do it", call)

    response = asyncio.run(scenario())
    assert response.model == "sonnet"
    assert response.provider is None
    assert response.model_version is None
    assert response.model_source is ModelSource.SERVICE_REPORTED


# -- transport failures ------------------------------------------------------


def test_timeout_becomes_error_result(service_config: ServiceConfig):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = SecurityServiceClient(service_config, client=http_client)
            call = BenchmarkCase.model_validate(copy.deepcopy(VALID_CASE)).assistant_tool_call
            return await client.evaluate("do it", call)

    response = asyncio.run(scenario())
    assert response.result_type is ServiceResultType.ERROR
    assert "timeout" in (response.error or "")


def test_connection_error_becomes_error_result(service_config: ServiceConfig):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = SecurityServiceClient(service_config, client=http_client)
            call = BenchmarkCase.model_validate(copy.deepcopy(VALID_CASE)).assistant_tool_call
            return await client.evaluate("do it", call)

    response = asyncio.run(scenario())
    assert response.result_type is ServiceResultType.ERROR
    assert "transport error" in (response.error or "")


def test_healthz_reports_status(service_config: ServiceConfig):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = SecurityServiceClient(service_config, client=http_client)
            return await client.healthz()

    healthy, payload = asyncio.run(scenario())
    assert healthy is True
    assert payload == {"status": "ok", "version": "0.1.0"}


# -- price, response time and stage, straight from the service ---------------


def test_price_is_read_from_the_service_cost_object(service_config: ServiceConfig):
    """The approved ``cost`` object wins over any local computation."""
    response = normalize_response(
        DECISION_ALLOW_STAGE2_WITH_COST, http_status=200, config=service_config
    )
    assert response.cost == 0.000147
    assert response.cost_source is CostSource.SERVICE_REPORTED
    assert response.cost_currency == "USD"
    assert response.cost_unavailable_reason is None
    assert response.usage.input_tokens == 812
    assert response.usage.output_tokens == 41
    assert response.usage.total_tokens == 853


def test_service_price_is_not_recomputed_from_a_pricing_table():
    """A pricing table never overrides a price the service itself reported."""
    config = ServiceConfig(
        resolve_model_metadata=False,
        pricing=PricingTable(models={"sonnet": ModelPrice(input_per_1m=3.0, output_per_1m=15.0)}),
    )
    response = normalize_response(DECISION_ALLOW_STAGE2_WITH_COST, http_status=200, config=config)
    assert response.cost == 0.000147
    assert response.cost_source is CostSource.SERVICE_REPORTED


def test_tokens_alone_still_produce_a_price_with_the_table_currency():
    config = ServiceConfig(
        resolve_model_metadata=False,
        pricing=PricingTable(
            currency="EUR", models={"sonnet": ModelPrice(input_per_1m=3.0, output_per_1m=15.0)}
        ),
    )
    payload = DECISION_ALLOW_STAGE2 | {"cost": {"input_tokens": 1_000, "output_tokens": 200}}
    response = normalize_response(payload, http_status=200, config=config)
    assert response.cost == (1_000 * 3.0 + 200 * 15.0) / 1e6
    assert response.cost_source is CostSource.COMPUTED_FROM_TOKENS
    assert response.cost_currency == "EUR"


def test_a_decision_without_a_model_call_is_priced_at_a_real_zero(
    service_config: ServiceConfig,
):
    """Stage 1 never reaches a model, so it costs nothing — derived from `stage`."""
    response = normalize_response(DECISION_DENY, http_status=200, config=service_config)
    assert response.stage == 1
    assert response.cost == 0.0
    assert response.cost_source is CostSource.NO_MODEL_CALL
    assert response.cost_unavailable_reason is None
    assert response.cost_currency is None


def test_an_unpriced_classifier_call_stays_unknown_not_zero(service_config: ServiceConfig):
    """Stage 2 without usage data is unknown; it must never be folded into zero."""
    response = normalize_response(DECISION_ALLOW_STAGE2, http_status=200, config=service_config)
    assert response.stage == 2
    assert response.cost is None
    assert response.cost_currency is None
    assert response.cost_source is CostSource.UNAVAILABLE
    assert response.cost_unavailable_reason


def test_a_response_with_no_stage_is_unknown_not_free(service_config: ServiceConfig):
    payload = {"decision": "ask", "reason": "r", "decision_id": "01J"}
    response = normalize_response(payload, http_status=200, config=service_config)
    assert response.cost is None
    assert response.cost_source is CostSource.UNAVAILABLE


def test_response_time_and_stage_are_taken_from_the_service(service_config: ServiceConfig):
    response = normalize_response(DECISION_ALLOW_STAGE2, http_status=200, config=service_config)
    assert response.latency_total_ms == 85.2
    assert response.latency_stage1_ms == 0.5
    assert response.latency_stage2_ms == 84.0
    assert response.stage == 2


def test_absent_response_time_and_stage_stay_none(service_config: ServiceConfig):
    payload = {"decision": "ask", "reason": "r", "decision_id": "01J"}
    response = normalize_response(payload, http_status=200, config=service_config)
    assert response.latency_total_ms is None
    assert response.stage is None
