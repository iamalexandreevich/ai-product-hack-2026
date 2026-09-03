"""Report aggregation and rendering."""

from __future__ import annotations

import json

from reporting.report import build_summary, percentile, render_failures, render_text, write_reports
from schemas.result import (
    BenchmarkResult,
    ComponentsSource,
    CostSource,
    ModelSource,
    RunConfig,
    ServiceResultType,
)


def _result(
    case_id: str,
    *,
    category: str = "data_exfiltration",
    difficulty: str = "easy",
    result_type: str = "deny",
    expected: str = "deny",
    score: int = 1,
    execution_time_ms: float = 10.0,
    cost: float | None = None,
    cost_reason: str | None = "service does not report token usage",
    model: str | None = None,
    is_benign: bool = False,
    components: list[str] | None = None,
    service_latency: float | None = 1.0,
    tags: list[str] | None = None,
) -> BenchmarkResult:
    return BenchmarkResult(
        run_id="run-1",
        case_id=case_id,
        attack_category=category,
        attack_name="a",
        difficulty=difficulty,
        is_benign=is_benign,
        tags=tags or [],
        human_req="req",
        assistant_tool_call={"tool": "shell", "raw": "cmd", "arguments": {"cwd": "/x"}},
        execution_time_ms=execution_time_ms,
        service_latency_total_ms=service_latency,
        cost=cost,
        cost_source=CostSource.COMPUTED_FROM_TOKENS if cost else CostSource.UNAVAILABLE,
        cost_unavailable_reason=None if cost else cost_reason,
        components_activated=components if components is not None else ["normalizer"],
        components_source=ComponentsSource.DERIVED,
        service_result_type=ServiceResultType(result_type),
        service_raw_response={"decision": result_type},
        stage=1,
        expected_result_type=expected,
        acceptable_result_types=[expected],
        score=score,
        score_explanation="x",
        expected_detection=not is_benign,
        model=model,
        model_source=ModelSource.SERVICE_REPORTED if model else ModelSource.NOT_APPLICABLE,
    )


def test_percentile_is_nearest_rank():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert percentile(values, 0.5) == 5.0
    assert percentile(values, 0.95) == 10.0
    assert percentile([], 0.5) is None
    assert percentile([42.0], 0.95) == 42.0


def test_totals_and_accuracy():
    results = [_result("A", score=1), _result("B", score=0, result_type="allow")]
    summary = build_summary(results, run_id="run-1")
    assert summary["totals"] == {
        "total_cases": 2,
        "passed": 1,
        "failed": 1,
        "accuracy": 0.5,
        "errors": 0,
        "contract_violations": 0,
    }


def test_grouping_by_category_and_difficulty():
    results = [
        _result("A", category="data_exfiltration", difficulty="easy", score=1),
        _result("B", category="data_exfiltration", difficulty="hard", score=0),
        _result("C", category="excessive_agency", difficulty="easy", score=1),
    ]
    summary = build_summary(results, run_id="run-1")
    assert summary["by_attack_category"]["data_exfiltration"]["total"] == 2
    assert summary["by_attack_category"]["data_exfiltration"]["accuracy"] == 0.5
    assert summary["by_difficulty"]["easy"]["passed"] == 2
    assert summary["by_difficulty"]["hard"]["accuracy"] == 0.0


def test_security_metrics_separate_attacks_from_controls():
    results = [
        _result("A1", result_type="deny", score=1),
        _result("A2", result_type="allow", expected="deny", score=0),
        _result(
            "B1",
            is_benign=True,
            category="benign_utility",
            result_type="allow",
            expected="allow",
            score=1,
        ),
        _result(
            "B2",
            is_benign=True,
            category="benign_utility",
            result_type="ask",
            expected="allow",
            score=0,
        ),
    ]
    metrics = build_summary(results, run_id="run-1")["security_metrics"]
    assert metrics["attack_cases"] == 2
    assert metrics["attacks_not_blocked"] == 1
    assert metrics["attack_pass_through_rate"] == 0.5
    assert metrics["benign_cases"] == 2
    assert metrics["benign_allowed"] == 1
    assert metrics["benign_asked_friction"] == 1
    assert metrics["false_positive_rate"] == 0.5


def test_latency_section_reports_client_and_service():
    results = [
        _result("A", execution_time_ms=10.0, service_latency=1.0),
        _result("B", execution_time_ms=30.0, service_latency=3.0),
    ]
    config = RunConfig(service_url="u", concurrency=4)
    latency = build_summary(results, run_id="run-1", config=config)["latency"]
    assert latency["client_avg_ms"] == 20.0
    assert latency["client_p50_ms"] == 10.0
    assert latency["client_max_ms"] == 30.0
    assert latency["service_reported_available"] == 2
    assert latency["concurrency"] == 4
    assert "concurrency=4" in latency["note"]


def test_unknown_cost_is_counted_with_reasons():
    results = [
        _result("A", cost=None, cost_reason="no pricing table configured"),
        _result("B", cost=None, cost_reason="no pricing table configured"),
        _result("C", cost=0.002),
    ]
    cost = build_summary(results, run_id="run-1")["cost"]
    assert cost["requests_with_unknown_cost"] == 2
    assert cost["requests_with_known_cost"] == 1
    assert cost["total_known_cost"] == 0.002
    assert cost["average_known_cost_per_request"] == 0.002
    assert cost["unknown_cost_reasons"]["no pricing table configured"] == 2


def test_missing_model_metadata_is_visible():
    results = [_result("A", model=None), _result("B", model="sonnet")]
    observed = build_summary(results, run_id="run-1")["models_observed"]
    sources = {entry["model"]: entry["model_source"] for entry in observed}
    assert sources[None] == "not_applicable"
    assert sources["sonnet"] == "service_reported"


def test_components_and_decisions_are_counted():
    results = [
        _result("A", components=["normalizer", "stage1_rules"]),
        _result("B", components=["normalizer", "stage2_llm"], result_type="ask", score=0),
    ]
    summary = build_summary(results, run_id="run-1")
    assert summary["components_observed"]["normalizer"] == 2
    assert summary["components_observed"]["stage2_llm"] == 1
    assert summary["decision_distribution"] == {"deny": 1, "ask": 1}


def test_error_results_are_counted_and_excluded_from_rates():
    error = _result("E", result_type="error", score=0)
    summary = build_summary([error, _result("A")], run_id="run-1")
    assert summary["totals"]["errors"] == 1
    assert summary["security_metrics"]["attack_cases_with_decision"] == 1


def test_failed_cases_carry_inspection_fields():
    failed = _result("F", result_type="allow", expected="deny", score=0, tags=["v1_limitation"])
    summary = build_summary([failed], run_id="run-1")
    record = summary["failed_cases"][0]
    assert record["case_id"] == "F"
    assert record["human_req"] == "req"
    assert record["assistant_tool_call"]["raw"] == "cmd"
    assert record["expected_result_type"] == "deny"
    assert record["service_result_type"] == "allow"
    assert record["service_raw_response"] == {"decision": "allow"}
    assert summary["tag_failures"]["v1_limitation"] == 1


def test_render_text_contains_the_required_sections():
    results = [
        _result("A"),
        _result(
            "B", is_benign=True, category="benign_utility", result_type="allow", expected="allow"
        ),
    ]
    text = render_text(build_summary(results, run_id="run-1", config=RunConfig(service_url="u")))
    for fragment in (
        "accuracy",
        "by attack category",
        "by difficulty",
        "latency",
        "cost",
        "service metadata",
        "failed cases",
    ):
        assert fragment in text


def test_render_failures_is_empty_when_all_pass():
    assert "No failed cases" in render_failures([_result("A")])


def test_render_failures_shows_the_boundary():
    text = render_failures([_result("F", result_type="allow", expected="deny", score=0)])
    assert "human_req" in text
    assert "assistant_tool_call" in text
    assert "raw service response" in text


def test_write_reports_produces_all_files(tmp_path):
    results = [_result("A"), _result("B", score=0, result_type="allow")]
    summary = build_summary(results, run_id="run-1")
    paths = write_reports(results, summary, tmp_path, run_id="run-1")

    assert set(paths) == {"summary_json", "results_jsonl", "summary_txt", "failures_txt"}
    assert json.loads(paths["summary_json"].read_text(encoding="utf-8"))["run_id"] == "run-1"
    lines = paths["results_jsonl"].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["case_id"] == "A"
    assert "AgentGate Benchmark V1" in paths["summary_txt"].read_text(encoding="utf-8")
