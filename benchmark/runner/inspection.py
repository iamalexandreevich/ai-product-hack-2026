"""Replay recorded tool results. Never executes the recorded tools."""

import asyncio
import time
import uuid
from collections.abc import Callable

from client.inspect import build_inspect_request, inspect
from client.security_service import (
    SecurityServiceClient,
    extract_currency,
    extract_usage_and_cost,
)
from evaluator.inspection import score_inspection
from schemas.inspect import InspectCase, InspectResult
from schemas.result import CostSource


async def execute_inspection(
    case: InspectCase,
    client: SecurityServiceClient,
    *,
    run_id: str,
    cache_mode: str = "cold",
    paired: bool = False,
    send_history: bool = True,
) -> InspectResult:
    # Bounded IDs remain valid even when the operator chooses a long run label.
    identity = uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}/{case.id}").hex
    session_id, call_id = f"inspect-{identity}", f"call-{identity}"
    body = build_inspect_request(
        case, client, session_id=session_id, call_id=call_id, send_history=send_history
    )
    result = InspectResult(run_id=run_id, case_id=case.id, case=case, request=body)
    if paired:
        if case.pre_action is None:
            result.error = "paired replay requires pre_action"
            return result
        decision = await client.evaluate(
            case.user_request,
            case.pre_action,
            session_id=session_id,
            call_id=call_id,
            history=case.history if send_history else [],
        )
        result.pre_action_response = decision.model_dump(mode="json")
        if decision.result_type.value != "allow" or decision.contract_violation:
            result.error = "pre-action gate did not allow this recorded tool invocation"
            return result
    if cache_mode == "warm" and not case.api_refusal:
        warm, raw, error = await inspect(client, body)
        result.warmup_response = raw
        if error or warm is None:
            result.error = f"warmup failed: {error}"
            return result
    started = time.perf_counter()
    response, raw, error = await inspect(client, body)
    result.execution_time_ms = (time.perf_counter() - started) * 1000
    result.response, result.raw_response, result.error = response, raw, error
    if response is None:
        return result
    if not case.api_refusal:
        result.cache_valid = cache_mode == "observe" or response.cached == (cache_mode == "warm")
        if not result.cache_valid:
            result.error = f"cache condition violated: mode={cache_mode}, cached={response.cached}"
    # Failed stage 2 can retain stage 1's verdict. Its latency proves a call was attempted.
    billed_stage = response.stage
    if not response.cached and response.latency_ms.get("stage2") is not None:
        billed_stage = 2
    result.usage, result.cost, result.cost_source, result.cost_unavailable_reason = (
        extract_usage_and_cost(
            raw,
            client.config,
            model_names=(response.model,),
            stage=billed_stage,
        )
    )
    if result.cost_source is CostSource.SERVICE_REPORTED:
        result.cost_currency = extract_currency(raw, client.config)
    elif result.cost_source is CostSource.COMPUTED_FROM_TOKENS:
        result.cost_currency = client.config.pricing.currency
    result.components = (
        ["inspect_cache"]
        if response.cached
        else ["api_validation"]
        if response.stage == 0
        else ["inspect_detectors"]
    )
    if response.stage >= 1 and response.verdict == "mask":
        result.components.append("inspect_mask")
    if not response.cached and response.redacted:
        result.components.append("inspect_secrets")
    if not response.cached and any(s.source == "model" for s in response.spans or []):
        result.components.append("inspect_semantic")
    if billed_stage == 2:
        result.components.append("inspect_stage2_llm")
    score_inspection(result)
    return result


async def run_inspections(
    cases: list[InspectCase],
    client: SecurityServiceClient,
    *,
    run_id: str,
    cache_mode: str,
    paired: bool = False,
    send_history: bool = True,
    on_result: Callable[[InspectResult], None] | None = None,
) -> list[InspectResult]:
    # Sequential to keep cache expectations and latency meaningful.
    results = []
    for case in cases:
        results.append(
            await execute_inspection(
                case,
                client,
                run_id=run_id,
                cache_mode=cache_mode,
                paired=paired,
                send_history=send_history,
            )
        )
        if on_result is not None:
            on_result(results[-1])
        await asyncio.sleep(0)
    return results
