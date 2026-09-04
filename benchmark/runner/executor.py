"""Benchmark executor.

Latency and concurrency
-----------------------
``execution_time_ms`` is wall-clock time measured around the HTTP call on the client
side, so it includes network and server queueing. With ``--concurrency > 1`` several
requests wait on the same service and this number grows with load: it stops being a
measurement of the service's own processing time and becomes a throughput-dependent
figure. For a latency claim use ``--concurrency 1``, or read the service-reported
``latency_ms`` fields (``service_latency_*``), which the service measures internally and
which the reports carry alongside. The report prints the concurrency of the run next to
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

from client.security_service import SecurityServiceClient
from evaluator.scorer import score_case
from schemas.case import BenchmarkCase
from schemas.result import BenchmarkResult, RunConfig, ServiceResponse, ServiceResultType

logger = logging.getLogger(__name__)

BENCHMARK_NAME = "agentgate-benchmark-v1"


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
    client: SecurityServiceClient,
    *,
    run_id: str,
    strict: bool = False,
    session_mode: str = "per_case",
    shared_session_id: str | None = None,
) -> BenchmarkResult:
    """Run one case end to end. Never raises: failures become an ``error`` result."""
    session_id = _session_id(case, run_id, session_mode, shared_session_id)
    metadata = {"benchmark": BENCHMARK_NAME, "run_id": run_id, "case_id": case.id}

    with measure_ms() as elapsed:
        try:
            response = await client.evaluate(
                case.human_req,
                case.assistant_tool_call,
                session_id=session_id,
                metadata=metadata,
            )
        except Exception as exc:  # defensive: a run must survive any single case
            logger.exception("case %s raised", case.id)
            response = ServiceResponse(
                result_type=ServiceResultType.ERROR,
                error=f"unhandled client error: {exc!r}",
            )
    execution_time_ms = elapsed()

    outcome = score_case(case, response, strict=strict)
    return BenchmarkResult(
        run_id=run_id,
        case_id=case.id,
        attack_category=case.attack_category,
        attack_name=case.attack_name,
        difficulty=case.difficulty,
        is_benign=case.is_benign,
        dataset_source=case.dataset_source,
        tags=list(case.tags),
        human_req=case.human_req,
        assistant_tool_call=case.assistant_tool_call.model_dump(mode="json"),
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
        client: SecurityServiceClient,
        config: RunConfig,
        *,
        run_id: str | None = None,
        on_result: Callable[[BenchmarkResult], None] | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.run_id = run_id or str(uuid.uuid4())
        self._on_result = on_result
        self._shared_session_id = f"bench-{self.run_id}"

    async def run(self, cases: list[BenchmarkCase]) -> list[BenchmarkResult]:
        semaphore = asyncio.Semaphore(max(1, self.config.concurrency))
        results: list[BenchmarkResult | None] = [None] * len(cases)

        async def worker(index: int, case: BenchmarkCase) -> None:
            async with semaphore:
                result = await execute_case(
                    case,
                    self.client,
                    run_id=self.run_id,
                    strict=self.config.strict_scoring,
                    session_mode=self.config.session_mode,
                    shared_session_id=self._shared_session_id,
                )
            results[index] = result
            if self._on_result is not None:
                self._on_result(result)

        await asyncio.gather(*(worker(i, case) for i, case in enumerate(cases)))
        return [result for result in results if result is not None]


def _session_id(
    case: BenchmarkCase,
    run_id: str,
    session_mode: str,
    shared_session_id: str | None,
) -> str | None:
    """Session strategy.

    ``per_case`` (default) gives every case a fresh session, so the service's allow cache
    and its escalation counters (three consecutive denies force an ``ask``, spec 5.4)
    cannot leak between cases and distort the measurement. ``shared`` deliberately keeps
    one session for the whole run to exercise that escalation logic; ``none`` omits
    ``session_id`` entirely, which the contract allows.
    """
    match session_mode:
        case "per_case":
            return f"bench-{run_id}-{case.id}"
        case "shared":
            return shared_session_id
        case "none":
            return None
        case _:
            raise ValueError(f"unknown session_mode {session_mode!r}")


def run_sync(coro: Awaitable[Any]) -> Any:
    """Small helper so the CLI stays synchronous."""
    return asyncio.run(coro)  # type: ignore[arg-type]
