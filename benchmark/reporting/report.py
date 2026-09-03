"""Machine-readable and human-readable benchmark reports."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from schemas.result import BenchmarkResult, RunConfig, ServiceResultType

MAX_FAILURE_DETAIL = 4000


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile. Deterministic and dependency-free."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


def build_summary(
    results: list[BenchmarkResult],
    *,
    run_id: str,
    config: RunConfig | None = None,
) -> dict[str, Any]:
    """Aggregate results into the JSON summary document."""
    total = len(results)
    passed = sum(r.score for r in results)
    times = [r.execution_time_ms for r in results]
    service_times = [
        r.service_latency_total_ms for r in results if r.service_latency_total_ms is not None
    ]

    known_costs = [r.cost for r in results if r.cost is not None]
    unknown_cost_reasons = Counter(
        r.cost_unavailable_reason or "unspecified" for r in results if r.cost is None
    )

    attacks = [r for r in results if not r.is_benign]
    benign = [r for r in results if r.is_benign]
    decided_attacks = [r for r in attacks if r.service_result_type is not ServiceResultType.ERROR]
    decided_benign = [r for r in benign if r.service_result_type is not ServiceResultType.ERROR]

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
        "by_attack_category": _group_stats(results, key=lambda r: r.attack_category),
        "by_difficulty": _group_stats(results, key=lambda r: r.difficulty.value),
        "security_metrics": {
            "attack_cases": len(attacks),
            "attack_cases_with_decision": len(decided_attacks),
            "attacks_not_blocked": sum(
                1 for r in decided_attacks if r.service_result_type is ServiceResultType.ALLOW
            ),
            "attack_pass_through_rate": _ratio(
                sum(1 for r in decided_attacks if r.service_result_type is ServiceResultType.ALLOW),
                len(decided_attacks),
            ),
            "benign_cases": len(benign),
            "benign_allowed": sum(
                1 for r in decided_benign if r.service_result_type is ServiceResultType.ALLOW
            ),
            "benign_asked_friction": sum(
                1 for r in decided_benign if r.service_result_type is ServiceResultType.ASK
            ),
            "benign_denied_false_positive": sum(
                1 for r in decided_benign if r.service_result_type is ServiceResultType.DENY
            ),
            "false_positive_rate": _ratio(
                sum(
                    1
                    for r in decided_benign
                    if r.service_result_type in (ServiceResultType.DENY, ServiceResultType.ASK)
                ),
                len(decided_benign),
            ),
        },
        "latency": {
            "note": (
                "execution_time_ms is client-side wall clock and includes queueing; with "
                f"concurrency={config.concurrency if config else 'unknown'} it is not a pure "
                "service-latency measurement. service_latency_* comes from the service itself."
            ),
            "concurrency": config.concurrency if config else None,
            "client_avg_ms": _mean(times),
            "client_p50_ms": percentile(times, 0.50),
            "client_p95_ms": percentile(times, 0.95),
            "client_max_ms": max(times) if times else None,
            "service_reported_available": len(service_times),
            "service_avg_ms": _mean(service_times),
            "service_p50_ms": percentile(service_times, 0.50),
            "service_p95_ms": percentile(service_times, 0.95),
        },
        "cost": {
            "total_known_cost": sum(known_costs) if known_costs else 0.0,
            "requests_with_known_cost": len(known_costs),
            "average_known_cost_per_request": _mean(known_costs),
            "requests_with_unknown_cost": total - len(known_costs),
            "unknown_cost_reasons": dict(unknown_cost_reasons.most_common()),
            "cost_sources": dict(Counter(r.cost_source.value for r in results)),
            "tokens_reported": sum(1 for r in results if r.total_tokens is not None),
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
        "stage_distribution": dict(
            Counter(str(r.stage) if r.stage is not None else "null" for r in results).most_common()
        ),
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

    lines.append("-- security --------------------------------------------------------------")
    lines.append(
        f"attack cases: {security['attack_cases']}   "
        f"not blocked (allow): {security['attacks_not_blocked']}   "
        f"pass-through rate: {_pct(security['attack_pass_through_rate'])}"
    )
    lines.append(
        f"benign cases: {security['benign_cases']}   allowed: {security['benign_allowed']}   "
        f"ask (friction): {security['benign_asked_friction']}   "
        f"deny (false positive): {security['benign_denied_false_positive']}   "
        f"FP+friction rate: {_pct(security['false_positive_rate'])}"
    )
    lines.append("")

    lines.append("-- by attack category ----------------------------------------------------")
    lines.extend(_render_group_table(summary["by_attack_category"], "category"))
    lines.append("")
    lines.append("-- by difficulty ---------------------------------------------------------")
    lines.extend(_render_group_table(summary["by_difficulty"], "difficulty"))
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
    lines.append("")

    lines.append("-- cost ------------------------------------------------------------------")
    lines.append(
        f"total known cost: {cost['total_known_cost']:.6f}   "
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
                    f"expected:    {result.expected_result_type.value} "
                    f"(accepted: {', '.join(d.value for d in result.acceptable_result_types)})",
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
    grouped: dict[str, list[BenchmarkResult]] = defaultdict(list)
    for result in results:
        grouped[key(result)].append(result)

    stats: dict[str, dict[str, Any]] = {}
    for name, group in sorted(grouped.items()):
        passed = sum(r.score for r in group)
        stats[name] = {
            "total": len(group),
            "passed": passed,
            "failed": len(group) - passed,
            "accuracy": _ratio(passed, len(group)),
            "avg_execution_time_ms": _mean([r.execution_time_ms for r in group]),
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
    width = max([len(header), *(len(name) for name in stats)] or [len(header)])
    lines = [f"  {header:<{width}}  passed  total  accuracy  avg ms"]
    for name, entry in stats.items():
        lines.append(
            f"  {name:<{width}}  {entry['passed']:>6}  {entry['total']:>5}  "
            f"{_pct(entry['accuracy']):>8}  {_ms(entry['avg_execution_time_ms']):>6}"
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
