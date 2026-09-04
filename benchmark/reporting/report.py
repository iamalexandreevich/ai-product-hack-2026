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
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluator.metrics import compute_run_metrics, percentile
from schemas.result import BenchmarkResult, ExecutionMode, RunConfig

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

    ``metrics`` is the only place a metric appears: the overall figures plus ``by``, one
    breakdown per dimension. ``totals`` next to it is score-level (how many cases the
    service got right), which is a different question from any of the metrics.
    """
    concurrency = config.concurrency if config else None
    execution_mode = config.execution_mode if config else ExecutionMode.SINGLE_DECISION
    metrics = compute_run_metrics(results, concurrency=concurrency, execution_mode=execution_mode)

    total = len(results)
    passed = sum(r.score for r in results)

    return {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "configuration": config.model_dump(mode="json") if config else None,
        "totals": {
            "total_cases": total,
            "passed": passed,
            "failed": total - passed,
            "accuracy": _ratio(passed, total),
            "contract_violations": sum(1 for r in results if r.contract_violation),
        },
        "metrics": metrics,
        "models_observed": _models_observed(results),
        "components_observed": dict(
            Counter(
                component for r in results for component in r.components_activated
            ).most_common()
        ),
        "components_sources": dict(Counter(r.components_source.value for r in results)),
        "rule_ids_observed": dict(Counter(r.rule_id for r in results if r.rule_id).most_common()),
        "tag_failures": _tag_failures(results),
        "failed_cases": [_failure_record(r) for r in results if not r.score],
    }


def render_text(summary: dict[str, Any]) -> str:
    """Human-readable summary. Reads ``metrics``; computes nothing."""
    totals = summary["totals"]
    metrics = summary["metrics"]
    security = metrics["security"]
    usability = metrics["usability"]
    friction = metrics["friction"]
    performance = metrics["performance"]
    decision_latency = performance["decision_latency_ms"]
    client_latency = performance["client_execution_time_ms"]
    price = metrics["cost"]
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
            f"adapter={config.get('adapter_name') or '-'} "
            f"profile={config.get('profile_id') or '-'} model={config.get('model') or '-'} "
            f"concurrency={config.get('concurrency')} "
            f"scoring={'strict' if config.get('strict_scoring') else 'default'}"
        )
    lines.append(f"mode:         execution_mode={performance['execution_mode']}")
    lines.append("")

    lines.append(
        f"cases: {totals['total_cases']}   passed: {totals['passed']}   "
        f"failed: {totals['failed']}   accuracy: {_pct(totals['accuracy'])}"
    )
    if metrics["no_decision"]:
        lines.append(f"service errors / no decision: {metrics['no_decision']}")
    if totals["contract_violations"]:
        lines.append(f"contract violations: {totals['contract_violations']}")
    lines.append("")

    lines.append("-- security --------------------------------------------------------------")
    lines.append(
        f"ASR: {_pct(security['asr'])}   "
        f"({security['successful_attacks']} of {security['attacks_with_decision']} answered "
        f"attacks succeeded; {security['attack_cases']} attack cases, "
        f"{security['attacks_without_decision']} without a decision)"
    )
    lines.append(f"blocked: {security['blocked_attacks']}   asked: {security['asked_attacks']}")
    lines.append("")

    lines.append("-- usability -------------------------------------------------------------")
    lines.append(
        f"Utility: {_pct(usability['utility'])}   "
        f"({usability['completed_tasks']} of {usability['legitimate_tasks_with_decision']} "
        "legitimate tasks completed without intervention)"
    )
    lines.append(
        f"FP: {usability['false_positives']}   "
        f"(blocked {usability['false_positives_blocked']}, "
        f"confirmation {usability['false_positives_confirmation']})   "
        f"rate: {_pct(usability['false_positive_rate'])}"
    )
    lines.append(
        f"Friction: {friction['human_decisions_total']} human decision(s)   "
        f"avg per task: {_num(friction['average_per_task'])}   "
        f"legitimate: {friction['legitimate']['human_decisions']}   "
        f"attack: {friction['attack']['human_decisions']}"
    )
    lines.append("")

    for title, dimension in (
        ("by attack category", "attack_type"),
        ("by difficulty", "difficulty"),
        ("by dataset source", "dataset_source"),
        ("by stage", "stage"),
    ):
        rows = metrics["by"].get(dimension) or {}
        if not rows:
            continue
        lines.append(f"-- {title} " + "-" * max(0, 73 - len(title)))
        lines.extend(_render_group_table(rows, dimension))
        lines.append("")

    lines.append("-- latency ---------------------------------------------------------------")
    lines.append(
        f"decision (service-reported)  avg {_ms(decision_latency['avg'])}  "
        f"p50 {_ms(decision_latency['p50'])}  p95 {_ms(decision_latency['p95'])}  "
        f"max {_ms(decision_latency['max'])}  "
        f"(reported for {decision_latency['reported_for']}, missing for "
        f"{decision_latency['missing_for']})"
    )
    lines.append(
        f"client wall clock            avg {_ms(client_latency['avg'])}  "
        f"p50 {_ms(client_latency['p50'])}  p95 {_ms(client_latency['p95'])}  "
        f"max {_ms(client_latency['max'])}  "
        f"(concurrency={client_latency['concurrency']}, includes queueing)"
    )
    lines.append(f"task slowdown: {performance['task_slowdown_unavailable_reason']}")
    lines.append("")

    lines.append("-- price -----------------------------------------------------------------")
    lines.append(
        f"total: {_num(price['total_price'])}   "
        f"priced requests: {price['priced_requests']} of {price['requests']}   "
        f"average per priced request: {_num(price['average_price_per_request'])}"
    )
    lines.append(
        f"free (no model call): {price['free_requests_no_model_call']}   "
        f"unknown: {price['requests_without_price']}"
    )
    for reason, count in price["unknown_price_reasons"].items():
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
    lines.append(f"  decisions: {metrics['decisions']}")
    lines.append(f"  stages: {metrics['stage_distribution']}")
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
    """One row per group, straight from ``metrics["by"][<dimension>]``."""
    width = max([len(header), *(len(name) for name in stats)]) if stats else len(header)
    head = (
        f"  {header:<{width}}  passed  total  accuracy       ASR   utility  FP  human  "
        f"svc ms  {'price':>10}"
    )
    lines = [head]
    partial = False
    for name, entry in stats.items():
        # A group where some requests have no price carries a partial total; marking it
        # keeps "these cost nothing" apart from "we could not price these".
        incomplete = entry["requests_without_price"] > 0 and entry["priced_requests"] > 0
        partial = partial or incomplete
        price = _num(entry["total_price"]) + ("+?" if incomplete else "")
        lines.append(
            f"  {name:<{width}}  {entry['passed']:>6}  {entry['total']:>5}  "
            f"{_pct(entry['accuracy']):>8}  {_pct(entry['asr']):>8}  "
            f"{_pct(entry['utility']):>8}  {entry['false_positives']:>2}  "
            f"{entry['human_decisions']:>5}  "
            f"{_ms(entry['decision_latency_ms']['avg']):>6}  {price:>10}"
        )
    if partial:
        lines.append(
            "  (price +? = the group also holds requests whose price is unknown; "
            "the total covers only the priced ones)"
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
