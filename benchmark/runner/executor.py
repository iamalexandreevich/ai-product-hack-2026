"""Benchmark executor.

The runner knows one thing about the system under test: it satisfies
``automode.base.AutomodeAdapter``. Which implementation that is — our AgentGate server
today — is decided in ``cli.py`` and injected, so nothing here selects, discovers or
configures an adapter.

Latency and concurrency
-----------------------
``execution_time_ms`` is wall-clock time measured around the adapter call on the
benchmark side, so for the server adapter it includes network and server queueing. With
``--concurrency > 1`` several requests wait on the same service and this number grows
with load: it stops being a measurement of the service's own processing time and becomes
a throughput-dependent figure. For a latency claim use ``--concurrency 1``, or read the
service-reported ``latency_ms`` fields (``service_latency_*``), which the service
measures internally and which the reports carry alongside. The report prints the concurrency of the run next to
every latency statistic for exactly this reason.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from contextlib import contextmanager
from typing import Any

from automode.base import AutomodeAdapter
from evaluator.scorer import score_case
from schemas.case import BenchmarkCase
from schemas.result import BenchmarkResult, RunConfig, ServiceResponse, ServiceResultType

logger = logging.getLogger(__name__)


@contextmanager
def measure_ms() -> Iterable[Callable[[], float]]:
    """Measure the wall-clock duration of a block in milliseconds."""
    start = time.perf_counter()
    elapsed: float | None = None

    def read() -> float:
        return elapsed if elapsed is not None else (time.perf_counter() - start) * 1000.0

    try:
        yield read
    finally:
        elapsed = (time.perf_counter() - start) * 1000.0


async def execute_case(
    case: BenchmarkCase,
    adapter: AutomodeAdapter,
    *,
    run_id: str,
    strict: bool = False,
) -> BenchmarkResult:
    """Run one case end to end. Never raises: failures become an ``error`` result."""
    history_turns_sent = 0
    with measure_ms() as elapsed:
        try:
            execution = await adapter.execute(case, run_id=run_id)
            response = execution.response
            history_turns_sent = execution.history_turns_sent
        except Exception as exc:  # defensive: a run must survive any single case
            logger.exception("case %s raised", case.id)
            response = ServiceResponse(
                result_type=ServiceResultType.ERROR,
                error=f"unhandled adapter error: {exc!r}",
            )
    execution_time_ms = elapsed()

    outcome = score_case(case, response, strict=strict)
    return BenchmarkResult(
        run_id=run_id,
        case_id=case.id,
        adapter_name=adapter.name,
        attack_category=case.attack_category,
        attack_name=case.attack_name,
        difficulty=case.difficulty,
        is_benign=case.is_benign,
        dataset_source=case.dataset_source,
        tags=list(case.tags),
        human_req=case.human_req,
        assistant_tool_call=case.assistant_tool_call.model_dump(mode="json"),
        history_turns_sent=history_turns_sent,
        execution_time_ms=execution_time_ms,
        service_latency_total_ms=response.latency_total_ms,
        service_latency_stage1_ms=response.latency_stage1_ms,
        service_latency_stage2_ms=response.latency_stage2_ms,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        total_tokens=response.usage.total_tokens,
        cost=response.cost,
        cost_currency=response.cost_currency,
        cost_source=response.cost_source,
        cost_unavailable_reason=response.cost_unavailable_reason,
        components_activated=response.components_activated,
        components_source=response.components_source,
        service_result_type=response.result_type,
        service_raw_response=response.raw_response,
        stage=response.stage,
        rule_id=response.rule_id,
        cached=response.cached,
        decision_id=response.decision_id,
        expected_result_type=case.expected_service_result,
        acceptable_result_types=list(outcome.acceptable),
        score=outcome.score,
        score_explanation=outcome.explanation,
        expected_detection=case.expected_detection,
        detected=outcome.detected,
        detection_correct=outcome.detection_correct,
        model=response.model,
        provider=response.provider,
        model_version=response.model_version,
        model_source=response.model_source,
        error=response.error,
        contract_violation=response.contract_violation,
    )


class BenchmarkRunner:
    """Runs a list of cases with bounded concurrency and streams results to a sink."""

    def __init__(
        self,
        adapter: AutomodeAdapter,
        config: RunConfig,
        *,
        run_id: str | None = None,
        on_result: Callable[[BenchmarkResult], None] | None = None,
    ) -> None:
        self.adapter = adapter
        self.config = config
        self.run_id = run_id or str(uuid.uuid4())
        self._on_result = on_result

    async def run(self, cases: list[BenchmarkCase]) -> list[BenchmarkResult]:
        semaphore = asyncio.Semaphore(max(1, self.config.concurrency))
        results: list[BenchmarkResult | None] = [None] * len(cases)

        async def worker(index: int, case: BenchmarkCase) -> None:
            async with semaphore:
                result = await execute_case(
                    case,
                    self.adapter,
                    run_id=self.run_id,
                    strict=self.config.strict_scoring,
                )
            results[index] = result
            if self._on_result is not None:
                self._on_result(result)

        await asyncio.gather(*(worker(i, case) for i, case in enumerate(cases)))
        return [result for result in results if result is not None]


def run_sync(coro: Awaitable[Any]) -> Any:
    """Small helper so the CLI stays synchronous."""
    return asyncio.run(coro)  # type: ignore[arg-type]
