"""Machine-readable and human-readable benchmark reports.

Every number here comes from :mod:`evaluator.metrics`; this module formats and never
derives. ``summary["metrics"]`` is the authoritative block (ASR, Utility, false
positives, friction, decision latency, price, and the breakdowns by attack type,
difficulty, dataset source and stage). The blocks around it — ``totals``,
``security_metrics``, ``latency``, ``cost`` — are the original v1 keys, kept so existing
consumers keep working; they are filled from the same metric functions.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluator.metrics import (
    BY_ATTACK_TYPE,
    BY_DATASET_SOURCE,
    BY_DIFFICULTY,
    BY_STAGE,
    compute_run_metrics,
    friction_metrics,
    percentile,
    security_metrics,
    usability_metrics,
)
from schemas.result import BenchmarkResult, RunConfig, ServiceResultType

MAX_FAILURE_DETAIL = 4000

__all__ = [
    "build_summary",
    "percentile",
    "render_failures",
    "render_text",
    "write_reports",
]


def build_summary(
    results: list[BenchmarkResult],
    *,
    run_id: str,
    config: RunConfig | None = None,
) -> dict[str, Any]:
    """Aggregate results into the JSON summary document.

    ``metrics`` carries the required aggregates and their breakdowns, straight from
    :mod:`evaluator.metrics`. The other blocks are the v1 keys, filled from the same
    numbers so that both views of a run always agree.
    """
    concurrency = config.concurrency if config else None
    metrics = compute_run_metrics(results, concurrency=concurrency)
    security = metrics["security"]
    usability = metrics["usability"]
    friction = metrics["friction"]
    decision_latency = metrics["performance"]["decision_latency_ms"]
    client_latency = metrics["performance"]["client_execution_time_ms"]
    price = metrics["cost"]

    total = len(results)
    passed = sum(r.score for r in results)

    summary: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "configuration": config.model_dump(mode="json") if config else None,
        "totals": {
            "total_cases": total,
            "passed": passed,
            "failed": total - passed,
            "accuracy": _ratio(passed, total),
            "errors": sum(1 for r in results if r.service_result_type is ServiceResultType.ERROR),
            "contract_violations": sum(1 for r in results if r.contract_violation),
        },
        "metrics": metrics,
        "by_attack_category": _group_stats(results, key=BY_ATTACK_TYPE),
        "by_difficulty": _group_stats(results, key=BY_DIFFICULTY),
        "by_dataset_source": _group_stats(results, key=BY_DATASET_SOURCE),
        "by_stage": _group_stats(results, key=BY_STAGE),
        "security_metrics": {
            # v1 key names. ``attacks_not_blocked`` is now the ground-truth count of
            # attacks that succeeded (the service permitted an action the case forbids),
            # and ``attack_pass_through_rate`` is the ASR over answered attacks.
            "attack_cases": security["attack_cases"],
            "attack_cases_with_decision": security["attacks_with_decision"],
            "attacks_not_blocked": security["successful_attacks"],
            "attack_pass_through_rate": security["asr"],
            "asr": security["asr"],
            "benign_cases": usability["legitimate_tasks"],
            "benign_allowed": usability["completed_tasks"],
            "benign_asked_friction": usability["false_positives_confirmation"],
            "benign_denied_false_positive": usability["false_positives_blocked"],
            "false_positive_rate": usability["false_positive_rate"],
            "false_positives": usability["false_positives"],
            "utility": usability["utility"],
            "human_decisions_total": friction["human_decisions_total"],
        },
        "latency": {
            "note": (
                "execution_time_ms is client-side wall clock and includes queueing; with "
                f"concurrency={concurrency if concurrency is not None else 'unknown'} it is not "
                "a pure service-latency measurement. service_latency_* comes from the service "
                "itself and is the decision-latency metric."
            ),
            "concurrency": concurrency,
            "client_avg_ms": client_latency["avg"],
            "client_p50_ms": client_latency["p50"],
            "client_p95_ms": client_latency["p95"],
            "client_max_ms": client_latency["max"],
            "service_reported_available": decision_latency["reported_for"],
            "service_avg_ms": decision_latency["avg"],
            "service_p50_ms": decision_latency["p50"],
            "service_p95_ms": decision_latency["p95"],
            "task_slowdown": metrics["performance"]["task_slowdown"],
            "task_slowdown_unavailable_reason": metrics["performance"][
                "task_slowdown_unavailable_reason"
            ],
        },
        "cost": {
            # ``None``, not 0.0, when the service reported no price: a run whose price is
            # unknown must not read as a run that was free.
            "total_known_cost": price["total_price"],
            "requests_with_known_cost": price["requests_with_price"],
            "average_known_cost_per_request": price["average_price_per_request"],
            "requests_with_unknown_cost": price["requests_without_price"],
            "unknown_cost_reasons": price["unknown_price_reasons"],
            "cost_sources": price["price_sources"],
            "tokens_reported": price["requests_with_token_usage"],
            "currencies_observed": dict(
                Counter(r.cost_currency for r in results if r.cost_currency).most_common()
            ),
        },
        "models_observed": _models_observed(results),
        "components_observed": dict(
            Counter(
                component for r in results for component in r.components_activated
            ).most_common()
        ),
        "components_sources": dict(Counter(r.components_source.value for r in results)),
        "decision_distribution": dict(
            Counter(r.service_result_type.value for r in results).most_common()
        ),
        "stage_distribution": metrics["stage_distribution"],
        "rule_ids_observed": dict(Counter(r.rule_id for r in results if r.rule_id).most_common()),
        "tag_failures": _tag_failures(results),
        "failed_cases": [_failure_record(r) for r in results if not r.score],
    }
    return summary


def render_text(summary: dict[str, Any]) -> str:
    """Human-readable summary."""
    totals = summary["totals"]
    latency = summary["latency"]
    cost = summary["cost"]
    security = summary["security_metrics"]
    lines: list[str] = []

    lines.append("=" * 78)
    lines.append("AgentGate Benchmark V1 - summary")
    lines.append("=" * 78)
    lines.append(f"run_id:       {summary['run_id']}")
    lines.append(f"generated at: {summary['generated_at']}")
    config = summary.get("configuration") or {}
    if config:
        lines.append(
            f"service:      {config.get('service_url')} "
            f"profile={config.get('profile_id') or '-'} model={config.get('model') or '-'} "
            f"concurrency={config.get('concurrency')} "
            f"scoring={'strict' if config.get('strict_scoring') else 'default'}"
        )
    lines.append("")

    lines.append(
        f"cases: {totals['total_cases']}   passed: {totals['passed']}   "
        f"failed: {totals['failed']}   accuracy: {_pct(totals['accuracy'])}"
    )
    if totals["errors"]:
        lines.append(f"service errors / no decision: {totals['errors']}")
    if totals["contract_violations"]:
        lines.append(f"contract violations: {totals['contract_violations']}")
    lines.append("")

    metrics = summary.get("metrics") or {}
    sec = metrics.get("security", {})
    usab = metrics.get("usability", {})
    fric = metrics.get("friction", {})

    lines.append("-- security --------------------------------------------------------------")
    lines.append(
        f"ASR: {_pct(sec.get('asr'))}   "
        f"({sec.get('successful_attacks', 0)} of {sec.get('attacks_with_decision', 0)} answered "
        f"attacks succeeded; {sec.get('attack_cases', 0)} attack cases, "
        f"{sec.get('attacks_without_decision', 0)} without a decision)"
    )
    lines.append(
        f"attack cases: {security['attack_cases']}   "
        f"not blocked (allow): {security['attacks_not_blocked']}   "
        f"pass-through rate: {_pct(security['attack_pass_through_rate'])}"
    )
    lines.append("")

    lines.append("-- usability -------------------------------------------------------------")
    lines.append(
        f"Utility: {_pct(usab.get('utility'))}   "
        f"({usab.get('completed_tasks', 0)} of {usab.get('legitimate_tasks_with_decision', 0)} "
        "legitimate tasks completed without intervention)"
    )
    lines.append(
        f"FP: {usab.get('false_positives', 0)}   "
        f"(blocked {usab.get('false_positives_blocked', 0)}, "
        f"confirmation {usab.get('false_positives_confirmation', 0)})   "
        f"rate: {_pct(usab.get('false_positive_rate'))}"
    )
    lines.append(
        f"Friction: {fric.get('human_decisions_total', 0)} human decision(s)   "
        f"avg per task: {_num(fric.get('average_per_task'))}   "
        f"legitimate: {fric.get('legitimate', {}).get('human_decisions', 0)}   "
        f"attack: {fric.get('attack', {}).get('human_decisions', 0)}"
    )
    lines.append(
        f"benign cases: {security['benign_cases']}   allowed: {security['benign_allowed']}   "
        f"ask (friction): {security['benign_asked_friction']}   "
        f"deny (false positive): {security['benign_denied_false_positive']}"
    )
    lines.append("")

    lines.append("-- ASR breakdowns --------------------------------------------------------")
    breakdowns = metrics.get("breakdowns", {})
    for title, key in (
        ("by attack type", "asr_by_attack_type"),
        ("by difficulty", "asr_by_difficulty"),
        ("by dataset source", "asr_by_dataset_source"),
        ("by stage", "asr_by_stage"),
    ):
        entries = breakdowns.get(key, {})
        if not entries:
            lines.append(f"  {title}: no attack case in this run")
            continue
        rendered = ", ".join(
            f"{name} {_pct(entry['asr'])} ({entry['successful_attacks']}/"
            f"{entry['attacks_with_decision']})"
            for name, entry in entries.items()
        )
        lines.append(f"  {title}: {rendered}")
    lines.append("")

    lines.append("-- by attack category ----------------------------------------------------")
    lines.extend(_render_group_table(summary["by_attack_category"], "category"))
    lines.append("")
    lines.append("-- by difficulty ---------------------------------------------------------")
    lines.extend(_render_group_table(summary["by_difficulty"], "difficulty"))
    lines.append("")
    if summary.get("by_dataset_source"):
        lines.append("-- by dataset source -----------------------------------------------------")
        lines.extend(_render_group_table(summary["by_dataset_source"], "source"))
        lines.append("")
    if summary.get("by_stage"):
        lines.append("-- by stage --------------------------------------------------------------")
        lines.extend(_render_group_table(summary["by_stage"], "stage"))
        lines.append("")

    lines.append("-- latency ---------------------------------------------------------------")
    lines.append(f"note: {latency['note']}")
    lines.append(
        f"client   avg {_ms(latency['client_avg_ms'])}  p50 {_ms(latency['client_p50_ms'])}  "
        f"p95 {_ms(latency['client_p95_ms'])}  max {_ms(latency['client_max_ms'])}"
    )
    if latency["service_reported_available"]:
        lines.append(
            f"service  avg {_ms(latency['service_avg_ms'])}  p50 {_ms(latency['service_p50_ms'])}  "
            f"p95 {_ms(latency['service_p95_ms'])}  "
            f"(reported for {latency['service_reported_available']} requests)"
        )
    else:
        lines.append("service  not reported")
    lines.append(f"task slowdown: {latency.get('task_slowdown_unavailable_reason', 'n/a')}")
    lines.append("")

    lines.append("-- cost ------------------------------------------------------------------")
    lines.append(
        f"total known price: {_num(cost['total_known_cost'])}   "
        f"known for {cost['requests_with_known_cost']} request(s)   "
        f"average: {_num(cost['average_known_cost_per_request'])}"
    )
    lines.append(f"unknown cost: {cost['requests_with_unknown_cost']} request(s)")
    for reason, count in cost["unknown_cost_reasons"].items():
        lines.append(f"  {count:>4}x {reason}")
    lines.append("")

    lines.append("-- service metadata ------------------------------------------------------")
    if summary["models_observed"]:
        for entry in summary["models_observed"]:
            lines.append(
                f"  model={entry['model'] or 'null'} provider={entry['provider'] or 'null'} "
                f"version={entry['model_version'] or 'null'} "
                f"source={entry['model_source']} count={entry['count']}"
            )
    else:
        lines.append("  no model metadata reported")
    lines.append(
        "  components: "
        + (
            ", ".join(f"{name} x{count}" for name, count in summary["components_observed"].items())
            or "none reported"
        )
    )
    lines.append(f"  components source: {summary['components_sources']}")
    lines.append(f"  decisions: {summary['decision_distribution']}")
    lines.append(f"  stages: {summary['stage_distribution']}")
    if summary["rule_ids_observed"]:
        lines.append(f"  rule ids: {summary['rule_ids_observed']}")
    if summary["tag_failures"]:
        top_tags = list(summary["tag_failures"].items())[:12]
        rendered = ", ".join(f"{tag} x{count}" for tag, count in top_tags)
        more = len(summary["tag_failures"]) - len(top_tags)
        lines.append(f"  failures by tag: {rendered}" + (f", (+{more} more)" if more else ""))
    lines.append("")

    failed = summary["failed_cases"]
    lines.append("-- failed cases ----------------------------------------------------------")
    if not failed:
        lines.append("  none")
    for record in failed:
        lines.append(
            f"  {record['case_id']}  [{record['attack_category']}/{record['difficulty']}]  "
            f"expected={record['expected_result_type']} actual={record['service_result_type']}"
        )
    lines.append("")
    return "\n".join(lines)


def render_failures(results: list[BenchmarkResult], *, limit: int | None = None) -> str:
    """Detailed dump of failing cases for inspection."""
    failures = [r for r in results if not r.score]
    if limit is not None:
        failures = failures[:limit]
    if not failures:
        return "No failed cases.\n"

    blocks: list[str] = []
    for result in failures:
        raw = json.dumps(result.service_raw_response, ensure_ascii=False, indent=2)
        if len(raw) > MAX_FAILURE_DETAIL:
            raw = raw[:MAX_FAILURE_DETAIL] + "\n… truncated …"
        blocks.append(
            "\n".join(
                [
                    "=" * 78,
                    f"case:        {result.case_id}",
                    f"category:    {result.attack_category} / {result.attack_name}",
                    f"difficulty:  {result.difficulty.value}",
                    (
                        f"expected:    {result.expected_result_type.value} (accepted: "
                        f"{', '.join(d.value for d in result.acceptable_result_types)})"
                    ),
                    f"actual:      {result.service_result_type.value}   score={result.score}",
                    f"score note:  {result.score_explanation}",
                    f"stage:       {result.stage}   rule_id: {result.rule_id}",
                    f"error:       {result.error or '-'}",
                    "",
                    "human_req:",
                    _indent(result.human_req),
                    "",
                    "assistant_tool_call:",
                    _indent(json.dumps(result.assistant_tool_call, ensure_ascii=False, indent=2)),
                    "",
                    "raw service response:",
                    _indent(raw),
                ]
            )
        )
    return "\n".join(blocks) + "\n"


def write_reports(
    results: list[BenchmarkResult],
    summary: dict[str, Any],
    out_dir: Path,
    *,
    run_id: str,
) -> dict[str, Path]:
    """Write JSON summary, JSONL results and the text report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary_json": out_dir / f"summary-{run_id}.json",
        "results_jsonl": out_dir / f"results-{run_id}.jsonl",
        "summary_txt": out_dir / f"summary-{run_id}.txt",
        "failures_txt": out_dir / f"failures-{run_id}.txt",
    }
    paths["summary_json"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with paths["results_jsonl"].open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(result.model_dump_json() + "\n")
    paths["summary_txt"].write_text(render_text(summary), encoding="utf-8")
    paths["failures_txt"].write_text(render_failures(results), encoding="utf-8")
    return paths


def _group_stats(results: list[BenchmarkResult], *, key: Any) -> dict[str, dict[str, Any]]:
    """Per-group view: correctness, ASR, Utility, friction and both latencies.

    ``asr`` and ``utility`` are ``None`` where the group holds no attack case / no
    legitimate case, rather than 0, which would read as a measured result.
    """
    grouped: dict[str, list[BenchmarkResult]] = defaultdict(list)
    for result in results:
        grouped[key(result)].append(result)

    stats: dict[str, dict[str, Any]] = {}
    for name, group in sorted(grouped.items()):
        passed = sum(r.score for r in group)
        security = security_metrics(group)
        usability = usability_metrics(group)
        friction = friction_metrics(group)
        service_latency = [
            r.service_latency_total_ms for r in group if r.service_latency_total_ms is not None
        ]
        stats[name] = {
            "total": len(group),
            "passed": passed,
            "failed": len(group) - passed,
            "accuracy": _ratio(passed, len(group)),
            "asr": security["asr"],
            "attack_cases": security["attack_cases"],
            "successful_attacks": security["successful_attacks"],
            "utility": usability["utility"],
            "legitimate_tasks": usability["legitimate_tasks"],
            "false_positives": usability["false_positives"],
            "human_decisions": friction["human_decisions_total"],
            "avg_execution_time_ms": _mean([r.execution_time_ms for r in group]),
            "avg_service_latency_ms": _mean(service_latency),
            "service_latency_reported_for": len(service_latency),
            "decisions": dict(Counter(r.service_result_type.value for r in group).most_common()),
        }
    return stats


def _models_observed(results: list[BenchmarkResult]) -> list[dict[str, Any]]:
    counter = Counter((r.model, r.provider, r.model_version, r.model_source.value) for r in results)
    return [
        {
            "model": model,
            "provider": provider,
            "model_version": version,
            "model_source": source,
            "count": count,
        }
        for (model, provider, version, source), count in counter.most_common()
    ]


def _tag_failures(results: list[BenchmarkResult]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for result in results:
        if not result.score:
            counter.update(result.tags)
    return dict(counter.most_common())


def _failure_record(result: BenchmarkResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "attack_category": result.attack_category,
        "attack_name": result.attack_name,
        "difficulty": result.difficulty.value,
        "human_req": result.human_req,
        "assistant_tool_call": result.assistant_tool_call,
        "expected_result_type": result.expected_result_type.value,
        "acceptable_result_types": [d.value for d in result.acceptable_result_types],
        "service_result_type": result.service_result_type.value,
        "service_raw_response": result.service_raw_response,
        "stage": result.stage,
        "rule_id": result.rule_id,
        "score": result.score,
        "score_explanation": result.score_explanation,
        "error": result.error,
        "tags": result.tags,
    }


def _render_group_table(stats: dict[str, dict[str, Any]], header: str) -> list[str]:
    """One row per group: correctness, ASR, Utility, friction and both latencies."""
    width = max([len(header), *(len(name) for name in stats)]) if stats else len(header)
    head = (
        f"  {header:<{width}}  passed  total  accuracy       ASR   utility  FP  human  "
        "client ms  svc ms"
    )
    lines = [head]
    for name, entry in stats.items():
        lines.append(
            f"  {name:<{width}}  {entry['passed']:>6}  {entry['total']:>5}  "
            f"{_pct(entry['accuracy']):>8}  {_pct(entry['asr']):>8}  "
            f"{_pct(entry['utility']):>8}  {entry['false_positives']:>2}  "
            f"{entry['human_decisions']:>5}  "
            f"{_ms(entry['avg_execution_time_ms']):>9}  "
            f"{_ms(entry['avg_service_latency_ms']):>6}"
        )
    return lines


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6f}"


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())
