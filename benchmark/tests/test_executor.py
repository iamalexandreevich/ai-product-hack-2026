"""Executor: latency measurement, error isolation, concurrency.

The executor is driven here through the production adapter over a mock transport, so
these assertions cover the whole server path. The seam itself — and the session strategy
that moved into ``ServerAutomodeAdapter`` with it — is covered by
``tests/test_automode_adapter.py``.
"""

from __future__ import annotations

import asyncio
import copy
import time

import httpx

from automode.server import ServerAutomodeAdapter
from client.security_service import SecurityServiceClient
from runner.executor import BenchmarkRunner, execute_case, measure_ms
from schemas.case import BenchmarkCase, DatasetSource
from schemas.result import ComponentsSource, CostSource, ModelSource, RunConfig, ServiceResultType
from tests.conftest import (
    BENIGN_CASE,
    DECISION_ALLOW_STAGE2,
    DECISION_ALLOW_STAGE2_WITH_COST,
    DECISION_DENY,
    VALID_CASE,
)


def _cases(n: int) -> list[BenchmarkCase]:
    cases = []
    for index in range(n):
        payload = copy.deepcopy(VALID_CASE)
        payload["id"] = f"SAMPLE_{index + 1:03d}"
        payload["human_req"] = f"request {index}"
        cases.append(BenchmarkCase.model_validate(payload))
    return cases


def _run(handler, cases, config, run_config=None, **kwargs):
    """Drive the executor through the production adapter over a mock transport."""

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = SecurityServiceClient(config, client=http_client)
            session_mode = run_config.session_mode if run_config else "per_case"
            adapter = ServerAutomodeAdapter(client, session_mode=session_mode)
            if run_config is None:
                return await execute_case(cases[0], adapter, run_id="run-1", **kwargs)
            runner = BenchmarkRunner(adapter, run_config, run_id="run-1")
            return await runner.run(cases)

    return asyncio.run(scenario())


def test_measure_ms_returns_positive_duration():
    with measure_ms() as elapsed:
        time.sleep(0.01)
    assert elapsed() >= 9.0


def test_execute_case_records_all_six_dimensions(service_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_DENY)

    result = _run(handler, _cases(1), service_config)

    # 1 execution time, 2 cost, 3 components, 4 result type, 5 score, 6 model
    assert result.execution_time_ms > 0
    assert result.service_latency_total_ms == 1.1
    assert result.cost == 0.0  # stage 1 never calls a model
    assert result.cost_source is CostSource.NO_MODEL_CALL
    assert result.cost_unavailable_reason is None
    assert result.components_activated == ["normalizer", "stage1_rules", "stage1_hard_deny"]
    assert result.components_source is ComponentsSource.DERIVED
    assert result.service_result_type is ServiceResultType.DENY
    assert result.score == 1
    assert result.model is None
    assert result.model_source is ModelSource.NOT_APPLICABLE


def test_execute_case_keeps_case_metadata(service_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_DENY)

    result = _run(handler, _cases(1), service_config)
    assert result.case_id == "SAMPLE_001"
    assert result.attack_category == "sample_category"
    assert result.human_req == "request 0"
    assert result.assistant_tool_call["tool"] == "shell"
    assert result.run_id == "run-1"


def test_execute_case_survives_service_failure(service_config):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no service", request=request)

    result = _run(handler, _cases(1), service_config)
    assert result.service_result_type is ServiceResultType.ERROR
    assert result.score == 0
    assert result.error
    assert result.execution_time_ms > 0


def test_execute_case_survives_timeout(service_config):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = _run(handler, _cases(1), service_config)
    assert result.service_result_type is ServiceResultType.ERROR
    assert "timeout" in (result.error or "")


def test_runner_preserves_case_order_under_concurrency(service_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_DENY)

    run_config = RunConfig(service_url=service_config.url, concurrency=4)
    results = _run(handler, _cases(6), service_config, run_config)
    assert [r.case_id for r in results] == [f"SAMPLE_{i:03d}" for i in range(1, 7)]


def test_runner_continues_after_a_failing_case(service_config):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=DECISION_DENY)

    run_config = RunConfig(service_url=service_config.url, concurrency=1)
    results = _run(handler, _cases(3), service_config, run_config)
    assert len(results) == 3
    assert sum(r.score for r in results) == 2
    assert sum(1 for r in results if r.service_result_type is ServiceResultType.ERROR) == 1


def test_strict_scoring_is_applied_by_the_runner(valid_case_dict, service_config):
    valid_case_dict["acceptable_service_results"] = ["deny", "ask"]
    case = BenchmarkCase.model_validate(valid_case_dict)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_DENY | {"decision": "ask"})

    lenient = RunConfig(service_url=service_config.url, strict_scoring=False)
    strict = RunConfig(service_url=service_config.url, strict_scoring=True)
    assert _run(handler, [case], service_config, lenient)[0].score == 1
    assert _run(handler, [case], service_config, strict)[0].score == 0


def test_stage_two_result_records_model(service_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_ALLOW_STAGE2)

    result = _run(handler, _cases(1), service_config)
    assert result.model == "sonnet"
    assert result.model_source is ModelSource.SERVICE_REPORTED
    assert result.service_latency_stage2_ms == 84.0
    assert result.score == 0  # the case expects deny


def test_metadata_carries_run_and_case_id(service_config):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content)["metadata"])
        return httpx.Response(200, json=DECISION_DENY)

    _run(handler, _cases(1), service_config)
    assert seen[0]["run_id"] == "run-1"
    assert seen[0]["case_id"] == "SAMPLE_001"


# -- what one executed case must carry ---------------------------------------


def test_server_price_response_time_and_stage_reach_the_result(service_config):
    """The three server-provided fields survive execution unchanged."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_ALLOW_STAGE2_WITH_COST)

    result = _run(handler, _cases(1), service_config)

    assert result.cost == 0.000147
    assert result.cost_currency == "USD"
    assert result.cost_source is CostSource.SERVICE_REPORTED
    assert result.service_latency_total_ms == 85.2
    assert result.service_latency_stage1_ms == 0.5
    assert result.service_latency_stage2_ms == 84.0
    assert result.stage == 2
    # client wall clock stays a separate concept, never a substitute
    assert result.execution_time_ms != result.service_latency_total_ms


def test_a_missing_server_price_stays_missing(service_config):
    """A classifier call whose price the service does not report is None, never 0."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_ALLOW_STAGE2)

    result = _run(handler, _cases(1), service_config)
    assert result.stage == 2
    assert result.cost is None
    assert result.cost_currency is None
    assert result.cost_unavailable_reason


def test_attack_type_difficulty_and_dataset_source_come_from_the_case(service_config):
    """Attack metadata is benchmark-side and is never inferred from the response."""
    payload = copy.deepcopy(VALID_CASE)
    payload["difficulty"] = "adversarial"
    payload["dataset_source"] = "baseline"
    case = BenchmarkCase.model_validate(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_ALLOW_STAGE2)

    result = _run(handler, [case], service_config)

    assert result.attack_category == "sample_category"
    assert result.difficulty.value == "adversarial"
    assert result.dataset_source is DatasetSource.BASELINE
    assert result.is_benign is False
    assert result.attack_success is True


def test_a_legitimate_case_is_recorded_as_a_legitimate_task(service_config):
    case = BenchmarkCase.model_validate(copy.deepcopy(BENIGN_CASE))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_ALLOW_STAGE2)

    result = _run(handler, [case], service_config)

    assert result.is_benign is True
    assert result.dataset_source is DatasetSource.TEAM
    assert result.task_success is True
    assert result.attack_success is None
    assert result.human_decision_count == 0


def test_the_result_records_which_adapter_produced_it(service_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_DENY)

    assert _run(handler, _cases(1), service_config).adapter_name == "server"
