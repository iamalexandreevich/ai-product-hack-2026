"""Two-run comparison: the paired, guardrail-vs-guardrail view.

The comparison must never compare different populations by accident: a run's overall
rates cover its whole population, but the headline comparison is *paired* — only the
cases both runs actually decided. These tests pin that, and pin that a run's own
``no_decision`` cases drop out of the paired set.
"""

from __future__ import annotations

from evaluator.metrics import compare_runs
from reporting.report import render_comparison
from schemas.result import (
    BenchmarkResult,
    ServiceResultType,
)


def _result(
    case_id: str,
    *,
    decision: str,
    adapter: str,
    is_benign: bool = False,
    acceptable: list[str] | None = None,
) -> BenchmarkResult:
    expected = "allow" if is_benign else "deny"
    return BenchmarkResult(
        run_id="r",
        case_id=case_id,
        adapter_name=adapter,
        attack_category="benign_utility" if is_benign else "data_exfiltration",
        attack_name="a",
        difficulty="easy",
        is_benign=is_benign,
        human_req="req",
        assistant_tool_call={"tool": "shell", "raw": "cmd", "arguments": {"cwd": "/x"}},
        execution_time_ms=1.0,
        service_result_type=ServiceResultType(decision),
        expected_result_type=expected,
        acceptable_result_types=acceptable or [expected],
        score=1,
    )


def test_paired_view_covers_only_cases_both_runs_decided():
    # server decided all three; claude gave no decision on EXFIL_002
    server = [
        _result("EXFIL_001", decision="deny", adapter="server"),
        _result("EXFIL_002", decision="allow", adapter="server"),
        _result("BENIGN_001", decision="allow", adapter="server", is_benign=True),
    ]
    claude = [
        _result("EXFIL_001", decision="deny", adapter="claude-code"),
        _result("EXFIL_002", decision="error", adapter="claude-code"),  # no decision
        _result("BENIGN_001", decision="allow", adapter="claude-code", is_benign=True),
    ]
    cmp = compare_runs(server, claude)

    assert cmp["paired"]["cases_in_both_runs"] == 3
    assert cmp["paired"]["cases_compared"] == 2  # EXFIL_002 dropped: claude gave no decision
    # paired ASR is over the 1 decided attack (EXFIL_001), which both denied -> 0
    assert cmp["paired"]["a"]["asr"] == 0.0
    assert cmp["paired"]["b"]["asr"] == 0.0


def test_overall_view_keeps_each_runs_whole_population_and_no_decision_count():
    server = [_result("EXFIL_001", decision="allow", adapter="server")]
    claude = [_result("EXFIL_001", decision="error", adapter="claude-code")]
    cmp = compare_runs(server, claude)

    assert cmp["overall"]["a"]["adapter_name"] == "server"
    assert cmp["overall"]["a"]["asr"] == 1.0  # server allowed a forbidden action
    assert cmp["overall"]["b"]["adapter_name"] == "claude-code"
    assert cmp["overall"]["b"]["no_decision"] == 1
    # nothing was decided by both, so there is no paired attack to score
    assert cmp["paired"]["cases_compared"] == 0
    assert cmp["paired"]["a"]["asr"] is None


def test_disagreements_list_only_cases_ruled_differently():
    server = [
        _result("EXFIL_001", decision="deny", adapter="server"),
        _result("EXFIL_002", decision="deny", adapter="server"),
    ]
    claude = [
        _result("EXFIL_001", decision="allow", adapter="claude-code"),  # differs
        _result("EXFIL_002", decision="deny", adapter="claude-code"),  # agrees
    ]
    disagreements = compare_runs(server, claude)["paired"]["disagreements"]
    assert [d["case_id"] for d in disagreements] == ["EXFIL_001"]
    assert disagreements[0]["a"] == "deny"
    assert disagreements[0]["b"] == "allow"


def test_render_comparison_shows_both_labels_and_the_paired_block():
    server = [_result("EXFIL_001", decision="deny", adapter="server")]
    claude = [_result("EXFIL_001", decision="allow", adapter="claude-code")]
    text = render_comparison(server, claude, label_a="server-run", label_b="claude-run")
    assert "run comparison" in text
    assert "server-run" in text and "claude-run" in text
    assert "paired" in text
    assert "server" in text and "claude-code" in text
