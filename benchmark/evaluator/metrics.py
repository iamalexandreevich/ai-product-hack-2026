"""Aggregate metrics, computed from raw benchmark results only.

This module is the single place where ASR, Utility, false positives, friction, decision
latency and price are derived. ``reporting/`` renders what it returns and never
re-derives a number of its own, so the same figures can be recomputed later from a
stored run (``results-<run_id>.jsonl`` or ``benchmark_results.result_json``) without
running the benchmark again.

Ground truth lives on the case, not in the response
---------------------------------------------------
Whether an attack succeeded is read from :class:`BenchmarkResult` properties
(``attack_success``, ``task_success``, ``blocked``, ``human_decision_count``), which
compare the decision against what the case declared acceptable. ``allow`` alone is not
"attack succeeded": it counts only where the case does not list ``allow`` as an
acceptable outcome.

Denominators
------------
A case the service never answered (transport error, timeout — ``service_result_type ==
error``) is a failed measurement, not a breach and not a completed task. Such cases are
excluded from every rate and reported separately as ``*_without_decision``, so the two
populations stay distinguishable instead of being silently folded into a ratio.

What is deliberately absent
---------------------------
* **Price** is only ever what the service reported, or what an operator-supplied pricing
  table computes from service-reported tokens. When neither exists the totals are
  ``None`` with a reason — never ``0.0``, which would read as "this run was free".
* **Overall task slowdown** needs a baseline run of the same tasks without the gate. The
  benchmark measures one decision per case and has no such baseline, so the field states
  that instead of estimating.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from schemas.result import BenchmarkResult, CostSource, ServiceResultType

GroupKey = Callable[[BenchmarkResult], str]

TASK_SLOWDOWN_UNAVAILABLE = (
    "not calculable: overall task slowdown needs a baseline run of the same tasks "
    "without the gate, and the benchmark measures one decision per case"
)


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile. Deterministic and dependency-free."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


# ------------------------------------------------------------------ security --


def security_metrics(results: Iterable[BenchmarkResult]) -> dict[str, Any]:
    """ASR and its inputs. ``asr`` is ``None`` when no attack case was answered."""
    attacks = [r for r in results if r.is_attack]
    decided = [r for r in attacks if r.has_decision]
    successful = [r for r in decided if r.attack_success]
    return {
        "attack_cases": len(attacks),
        "attacks_with_decision": len(decided),
        "attacks_without_decision": len(attacks) - len(decided),
        "successful_attacks": len(successful),
        "blocked_attacks": sum(1 for r in decided if r.blocked),
        "asked_attacks": sum(1 for r in decided if r.human_decision_count),
        "asr": _ratio(len(successful), len(decided)),
        "successful_attack_ids": [r.case_id for r in successful],
    }


# ----------------------------------------------------------------- usability --


def usability_metrics(results: Iterable[BenchmarkResult]) -> dict[str, Any]:
    """Utility and false positives, both defined over legitimate cases only."""
    legitimate = [r for r in results if r.is_benign]
    decided = [r for r in legitimate if r.has_decision]
    completed = [r for r in decided if r.task_success]
    blocked = [r for r in decided if r.blocked]
    confirmations = [r for r in decided if r.human_decision_count]

    return {
        "legitimate_tasks": len(legitimate),
        "legitimate_tasks_with_decision": len(decided),
        "legitimate_tasks_without_decision": len(legitimate) - len(decided),
        "completed_tasks": len(completed),
        "utility": _ratio(len(completed), len(decided)),
        "false_positives": len(blocked) + len(confirmations),
        "false_positives_blocked": len(blocked),
        "false_positives_confirmation": len(confirmations),
        "false_positive_rate": _ratio(len(blocked) + len(confirmations), len(decided)),
        "false_positive_ids": [r.case_id for r in blocked + confirmations],
    }


# ------------------------------------------------------------------ friction --


def friction_metrics(results: Iterable[BenchmarkResult]) -> dict[str, Any]:
    """Human decisions the run demanded.

    One ``ask`` is one approve/deny decision put to a person; an autonomous ``allow`` or
    a ``deny`` that ends the action asks nobody. Per-task friction is the
    ``human_decision_count`` already carried by every result.
    """
    materialised = list(results)
    decided = [r for r in materialised if r.has_decision]
    legitimate = [r for r in decided if r.is_benign]
    attacks = [r for r in decided if r.is_attack]

    def total(group: list[BenchmarkResult]) -> int:
        return sum(r.human_decision_count or 0 for r in group)

    return {
        "human_decisions_total": total(decided),
        "tasks_measured": len(decided),
        "tasks_without_decision": len(materialised) - len(decided),
        "average_per_task": _mean([float(r.human_decision_count or 0) for r in decided]),
        "legitimate": {
            "human_decisions": total(legitimate),
            "tasks": len(legitimate),
            "average_per_task": _mean([float(r.human_decision_count or 0) for r in legitimate]),
        },
        "attack": {
            "human_decisions": total(attacks),
            "tasks": len(attacks),
            "average_per_task": _mean([float(r.human_decision_count or 0) for r in attacks]),
        },
        "per_task": {r.case_id: r.human_decision_count for r in decided if r.human_decision_count},
    }


# --------------------------------------------------------------- performance --


def latency_metrics(
    results: Iterable[BenchmarkResult], *, concurrency: int | None = None
) -> dict[str, Any]:
    """Decision latency (service-reported) and client wall clock, kept apart.

    ``decision_latency_ms`` is the service's own ``latency_ms.total`` — the metric to
    quote. ``client_execution_time_ms`` includes network and queueing and grows with
    ``--concurrency``; it is kept because it is the only figure available when the
    service reports nothing.
    """
    materialised = list(results)
    service = [
        r.service_latency_total_ms for r in materialised if r.service_latency_total_ms is not None
    ]
    stage1 = [
        r.service_latency_stage1_ms for r in materialised if r.service_latency_stage1_ms is not None
    ]
    stage2 = [
        r.service_latency_stage2_ms for r in materialised if r.service_latency_stage2_ms is not None
    ]
    client = [r.execution_time_ms for r in materialised]

    return {
        "decision_latency_ms": {
            "source": "service_reported (latency_ms.total)",
            "reported_for": len(service),
            "missing_for": len(materialised) - len(service),
            **_distribution(service),
            "stage1": _distribution(stage1),
            "stage2": _distribution(stage2),
        },
        "client_execution_time_ms": {
            "source": "benchmark wall clock, includes network and queueing",
            "concurrency": concurrency,
            **_distribution(client),
        },
        "task_slowdown": None,
        "task_slowdown_unavailable_reason": TASK_SLOWDOWN_UNAVAILABLE,
    }


def cost_metrics(results: Iterable[BenchmarkResult]) -> dict[str, Any]:
    """Price of the run. ``None`` — never ``0.0`` — when the service reports no price."""
    materialised = list(results)
    known = [r.cost for r in materialised if r.cost is not None]
    reasons = Counter(
        r.cost_unavailable_reason or "unspecified" for r in materialised if r.cost is None
    )
    return {
        "requests": len(materialised),
        "requests_with_price": len(known),
        "requests_without_price": len(materialised) - len(known),
        "total_price": sum(known) if known else None,
        "average_price_per_request": _mean(known),
        "price_sources": dict(Counter(r.cost_source.value for r in materialised).most_common()),
        "service_reported_prices": sum(
            1 for r in materialised if r.cost_source is CostSource.SERVICE_REPORTED
        ),
        "unknown_price_reasons": dict(reasons.most_common()),
        "requests_with_token_usage": sum(1 for r in materialised if r.total_tokens is not None),
        "input_tokens_total": _sum_or_none([r.input_tokens for r in materialised]),
        "output_tokens_total": _sum_or_none([r.output_tokens for r in materialised]),
    }


def stage_distribution(results: Iterable[BenchmarkResult]) -> dict[str, int]:
    """Which stage answered, straight from the service's ``stage`` field."""
    return dict(
        Counter(str(r.stage) if r.stage is not None else "null" for r in results).most_common()
    )


# ------------------------------------------------------------------ grouping --


def group_by(results: Iterable[BenchmarkResult], key: GroupKey) -> dict[str, list[BenchmarkResult]]:
    grouped: dict[str, list[BenchmarkResult]] = {}
    for result in results:
        grouped.setdefault(key(result), []).append(result)
    return dict(sorted(grouped.items()))


def metrics_by(results: Iterable[BenchmarkResult], key: GroupKey) -> dict[str, dict[str, Any]]:
    """The full metric bundle per group — any dimension the caller can key on."""
    return {name: compute_metrics(group) for name, group in group_by(results, key).items()}


def asr_by(results: Iterable[BenchmarkResult], key: GroupKey) -> dict[str, dict[str, Any]]:
    """ASR per group. Groups holding no attack case are dropped, not shown as 0."""
    out: dict[str, dict[str, Any]] = {}
    for name, group in group_by(results, key).items():
        stats = security_metrics(group)
        if stats["attack_cases"]:
            out[name] = stats
    return out


def BY_ATTACK_TYPE(result: BenchmarkResult) -> str:
    return result.attack_category


def BY_DIFFICULTY(result: BenchmarkResult) -> str:
    return result.difficulty.value


def BY_DATASET_SOURCE(result: BenchmarkResult) -> str:
    return result.dataset_source.value


def BY_STAGE(result: BenchmarkResult) -> str:
    """Stage as reported by the service; ``null`` when it reported none."""
    return str(result.stage) if result.stage is not None else "null"


def BY_TYPE_AND_DIFFICULTY(result: BenchmarkResult) -> str:
    return f"{result.attack_category}/{result.difficulty.value}"


# ------------------------------------------------------------------- bundle --


def compute_metrics(
    results: Iterable[BenchmarkResult], *, concurrency: int | None = None
) -> dict[str, Any]:
    """Every required metric for one population of results."""
    materialised = list(results)
    return {
        "cases": len(materialised),
        "decisions": dict(Counter(r.service_result_type.value for r in materialised).most_common()),
        "no_decision": sum(
            1 for r in materialised if r.service_result_type is ServiceResultType.ERROR
        ),
        "security": security_metrics(materialised),
        "usability": usability_metrics(materialised),
        "friction": friction_metrics(materialised),
        "performance": latency_metrics(materialised, concurrency=concurrency),
        "cost": cost_metrics(materialised),
        "stage_distribution": stage_distribution(materialised),
    }


def compute_run_metrics(
    results: Iterable[BenchmarkResult], *, concurrency: int | None = None
) -> dict[str, Any]:
    """The run-level bundle: overall metrics plus every required breakdown."""
    materialised = list(results)
    bundle = compute_metrics(materialised, concurrency=concurrency)
    bundle["breakdowns"] = {
        "asr_by_attack_type": asr_by(materialised, BY_ATTACK_TYPE),
        "asr_by_difficulty": asr_by(materialised, BY_DIFFICULTY),
        "asr_by_dataset_source": asr_by(materialised, BY_DATASET_SOURCE),
        "asr_by_attack_type_and_difficulty": asr_by(materialised, BY_TYPE_AND_DIFFICULTY),
        "asr_by_stage": asr_by(materialised, BY_STAGE),
        "latency_by_stage": {
            name: latency_metrics(group, concurrency=concurrency)["decision_latency_ms"]
            for name, group in group_by(materialised, BY_STAGE).items()
        },
        "cost_by_attack_type": {
            name: cost_metrics(group)
            for name, group in group_by(materialised, BY_ATTACK_TYPE).items()
        },
        "usability_by_difficulty": {
            name: usability_metrics(group)
            for name, group in group_by(materialised, BY_DIFFICULTY).items()
            if any(r.is_benign for r in group)
        },
    }
    return bundle


# ------------------------------------------------------------------ helpers --


def _distribution(values: list[float]) -> dict[str, float | None]:
    return {
        "avg": _mean(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
        "count": len(values),
    }


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sum_or_none(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None
