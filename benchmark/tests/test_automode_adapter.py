"""The automode seam.

Two things are proven here. First, ``ServerAutomodeAdapter`` is a faithful move of the
code that used to live in ``runner/executor.py``: the same request body, the same session
strategy, and a response the envelope neither wraps twice nor edits. Second — and this is
why the seam exists — a fake adapter that owns no HTTP client, no ``ServiceConfig`` and no
transport at all can be injected into ``execute_case`` and ``BenchmarkRunner`` and produce
a scored result. That fake lives in this module only: no second production adapter exists.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from automode.base import AutomodeAdapter, AutomodeExecutionResult
from automode.server import BENCHMARK_NAME, ServerAutomodeAdapter
from client.security_service import SecurityServiceClient
from evaluator.metrics import cost_metrics, latency_metrics
from runner.executor import BenchmarkRunner, execute_case
from schemas.case import BenchmarkCase, ServiceDecision
from schemas.result import (
    ComponentsSource,
    CostSource,
    ModelSource,
    RunConfig,
    ServiceResponse,
    ServiceResultType,
)
from tests.conftest import DECISION_DENY, HISTORY_CASE, VALID_CASE


def _cases(n: int) -> list[BenchmarkCase]:
    cases = []
    for index in range(n):
        payload = copy.deepcopy(VALID_CASE)
        payload["id"] = f"SAMPLE_{index + 1:03d}"
        payload["human_req"] = f"request {index}"
        cases.append(BenchmarkCase.model_validate(payload))
    return cases


def _with_adapter(handler, config, coro_factory, **adapter_kwargs):
    """Run ``coro_factory(adapter)`` against a mock-transport server adapter."""

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = SecurityServiceClient(config, client=http_client)
            adapter = ServerAutomodeAdapter(client, **adapter_kwargs)
            return await coro_factory(adapter)

    return asyncio.run(scenario())


# -- 1. the server adapter satisfies the contract ----------------------------


def test_server_adapter_satisfies_the_protocol(service_config):
    adapter = ServerAutomodeAdapter(SecurityServiceClient(service_config))
    assert isinstance(adapter, AutomodeAdapter)
    assert adapter.name == "server"


def test_the_envelope_carries_the_client_response_unchanged(service_config):
    """The adapter adds nothing to and changes nothing in the ``ServiceResponse``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_DENY)

    case = _cases(1)[0]

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = SecurityServiceClient(service_config, client=http_client)
            direct = await client.evaluate(
                case.human_req,
                case.assistant_tool_call,
                session_id=f"bench-run-1-{case.id}",
                metadata={"benchmark": BENCHMARK_NAME, "run_id": "run-1", "case_id": case.id},
            )
            adapter = ServerAutomodeAdapter(client)
            return direct, await adapter.execute(case, run_id="run-1")

    direct, outcome = asyncio.run(scenario())

    assert isinstance(outcome, AutomodeExecutionResult)
    assert isinstance(outcome.response, ServiceResponse)
    assert outcome.response.model_dump() == direct.model_dump()


def test_the_envelope_carries_the_outcome_and_what_was_put_in_front_of_it():
    """Whole-task fields are added by whoever writes the second adapter, not guessed.

    ``history_turns_sent`` is not a whole-task field: it says what the adapter presented
    for this one decision, which only the adapter knows.
    """
    assert list(AutomodeExecutionResult.model_fields) == ["response", "history_turns_sent"]


def test_the_adapter_sends_the_dialogue_history_the_case_carries(service_config):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=DECISION_DENY)

    case = BenchmarkCase.model_validate(copy.deepcopy(HISTORY_CASE))
    result = _with_adapter(
        handler, service_config, lambda adapter: adapter.execute(case, run_id="run-1")
    )

    assert len(seen[0]["history"]) == 5
    assert result.history_turns_sent == 5


def test_stripping_history_removes_it_from_the_request_and_changes_nothing_else(service_config):
    """The ablation must differ in the dialogue alone, or the comparison means nothing."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=DECISION_DENY)

    case = BenchmarkCase.model_validate(copy.deepcopy(HISTORY_CASE))
    full = _with_adapter(
        handler, service_config, lambda adapter: adapter.execute(case, run_id="run-1")
    )
    stripped = _with_adapter(
        handler,
        service_config,
        lambda adapter: adapter.execute(case, run_id="run-1"),
        send_history=False,
    )

    assert "history" not in seen[1]
    assert stripped.history_turns_sent == 0
    assert full.history_turns_sent == 5
    assert seen[0] == seen[1] | {"history": seen[0]["history"]}


def test_the_result_records_how_many_turns_were_put_in_front_of_the_automode(service_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DECISION_DENY)

    case = BenchmarkCase.model_validate(copy.deepcopy(HISTORY_CASE))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = SecurityServiceClient(service_config, client=http_client)
            adapter = ServerAutomodeAdapter(client)
            return await execute_case(case, adapter, run_id="run-1")

    assert asyncio.run(scenario()).history_turns_sent == 5


# -- 2. the request the client used to send ----------------------------------


def test_the_adapter_sends_the_request_execute_case_used_to_send(service_config):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=DECISION_DENY)

    case = _cases(1)[0]
    _with_adapter(handler, service_config, lambda adapter: adapter.execute(case, run_id="run-1"))

    body = seen[0]
    assert body["user_request"] == case.human_req
    assert body["tool"] == "shell"
    assert body["raw"] == case.assistant_tool_call.raw
    assert body["args"]["cwd"] == "/home/dev/repo"
    assert body["metadata"] == {
        "benchmark": "agentgate-benchmark-v1",
        "run_id": "run-1",
        "case_id": "SAMPLE_001",
    }


# -- 3. the session strategy, moved with the server semantics it belongs to ---


def _session_ids(handler_sink, service_config, cases, **adapter_kwargs) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        handler_sink.append(json.loads(request.content))
        return httpx.Response(200, json=DECISION_DENY)

    async def run_all(adapter):
        for case in cases:
            await adapter.execute(case, run_id="run-1")

    _with_adapter(handler, service_config, run_all, **adapter_kwargs)


def test_per_case_session_mode_gives_every_case_its_own_session(service_config):
    bodies: list[dict] = []
    _session_ids(bodies, service_config, _cases(3))
    seen = [body["session_id"] for body in bodies]
    assert len(set(seen)) == 3
    assert all(item.startswith("bench-run-1-") for item in seen)


def test_shared_session_mode_uses_one_session_for_the_whole_run(service_config):
    bodies: list[dict] = []
    _session_ids(bodies, service_config, _cases(3), session_mode="shared")
    seen = {body["session_id"] for body in bodies}
    assert seen == {"bench-run-1"}


def test_session_mode_none_omits_the_session_id(service_config):
    bodies: list[dict] = []
    _session_ids(bodies, service_config, _cases(2), session_mode="none")
    assert all("session_id" not in body for body in bodies)


def test_an_unknown_session_mode_raises(service_config):
    bodies: list[dict] = []
    with pytest.raises(ValueError, match="unknown session_mode"):
        _session_ids(bodies, service_config, _cases(1), session_mode="nonsense")


# -- 4. the runner depends on the protocol, not on our HTTP client -----------


class _FakeAdapter:
    """A non-server automode implementation: no httpx, no ServiceConfig, no transport.

    Test-only by design — the repository holds exactly one production adapter.
    """

    name = "fake-automode"

    def __init__(
        self,
        response: ServiceResponse | None = None,
        *,
        raises: BaseException | None = None,
    ) -> None:
        self.response = response or ServiceResponse(
            result_type=ServiceResultType.DENY,
            decision=ServiceDecision.DENY,
        )
        self.raises = raises
        self.seen: list[tuple[str, str]] = []

    async def execute(self, case: BenchmarkCase, *, run_id: str) -> AutomodeExecutionResult:
        self.seen.append((case.id, run_id))
        if self.raises is not None:
            raise self.raises
        return AutomodeExecutionResult(response=self.response)


def test_a_fake_adapter_satisfies_the_protocol():
    assert isinstance(_FakeAdapter(), AutomodeAdapter)


def test_execute_case_runs_any_adapter_and_stamps_its_name():
    adapter = _FakeAdapter()
    case = _cases(1)[0]
    result = asyncio.run(execute_case(case, adapter, run_id="run-1"))

    assert adapter.seen == [("SAMPLE_001", "run-1")]
    assert result.adapter_name == "fake-automode"
    assert result.service_result_type is ServiceResultType.DENY
    assert result.score == 1
    assert result.execution_time_ms > 0


def test_the_runner_drives_any_adapter():
    adapter = _FakeAdapter()
    config = RunConfig(service_url="unused://", concurrency=2)
    results = asyncio.run(BenchmarkRunner(adapter, config, run_id="run-1").run(_cases(4)))

    assert [r.case_id for r in results] == [f"SAMPLE_{i:03d}" for i in range(1, 5)]
    assert {r.adapter_name for r in results} == {"fake-automode"}


# -- 5. optional telemetry may be absent -------------------------------------


def test_absent_telemetry_stays_none_and_is_never_read_as_zero():
    """An adapter that reports no stage, latency or price yields ``None``, not ``0``."""
    adapter = _FakeAdapter(
        ServiceResponse(
            result_type=ServiceResultType.DENY,
            decision=ServiceDecision.DENY,
            stage=None,
            rule_id=None,
            cached=None,
            latency_stage1_ms=None,
            latency_stage2_ms=None,
            latency_total_ms=None,
            cost=None,
            cost_source=CostSource.UNAVAILABLE,
            cost_unavailable_reason="this automode reports no price",
        )
    )
    result = asyncio.run(execute_case(_cases(1)[0], adapter, run_id="run-1"))

    assert result.stage is None
    assert result.rule_id is None
    assert result.cached is None
    assert result.service_latency_total_ms is None
    assert result.service_latency_stage1_ms is None
    assert result.service_latency_stage2_ms is None
    assert result.cost is None
    assert result.cost_source is CostSource.UNAVAILABLE
    assert result.cost_unavailable_reason == "this automode reports no price"
    assert result.components_activated == []
    assert result.components_source is ComponentsSource.UNAVAILABLE
    assert result.model is None
    assert result.model_source is ModelSource.UNAVAILABLE

    # the aggregates read that as "unknown", never as "free"
    price = cost_metrics([result])
    assert price["priced_requests"] == 0
    assert price["total_price"] is None
    assert price["average_price_per_request"] is None
    assert price["free_requests_no_model_call"] == 0
    assert price["unknown_price_reasons"] == {"this automode reports no price": 1}
    assert latency_metrics([result])["decision_latency_ms"]["p50"] is None


# -- 6. a raising adapter is a failed measurement, not a crashed run ---------


def test_an_adapter_that_raises_becomes_an_error_result():
    adapter = _FakeAdapter(raises=RuntimeError("automode exploded"))
    result = asyncio.run(execute_case(_cases(1)[0], adapter, run_id="run-1"))

    assert result.service_result_type is ServiceResultType.ERROR
    assert result.score == 0
    assert "automode exploded" in (result.error or "")


def test_the_run_continues_after_an_adapter_failure():
    class _FlakyAdapter(_FakeAdapter):
        name = "flaky"

        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def execute(self, case, *, run_id):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("boom")
            return AutomodeExecutionResult(response=self.response)

    config = RunConfig(service_url="unused://", concurrency=1)
    results = asyncio.run(BenchmarkRunner(_FlakyAdapter(), config, run_id="run-1").run(_cases(3)))

    assert len(results) == 3
    assert sum(r.score for r in results) == 2
    assert sum(1 for r in results if r.service_result_type is ServiceResultType.ERROR) == 1


# -- the dependency direction cannot silently regress ------------------------


def test_the_runner_imports_nothing_from_the_http_client_or_the_service_config():
    """``runner/`` may know the protocol; it may not know how we reach our server."""
    source = Path(__file__).resolve().parent.parent / "runner" / "executor.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)

    forbidden = {"client", "config", "httpx"}
    assert not any(name.split(".")[0] in forbidden for name in imported), sorted(imported)
    assert "SecurityServiceClient" not in source.read_text(encoding="utf-8")
