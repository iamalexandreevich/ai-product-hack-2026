"""Two-run comparison: the paired, guardrail-vs-guardrail view.

The comparison must never compare different populations by accident: a run's overall
rates cover its whole population, but the headline comparison is *paired* — only the
cases both runs actually decided. These tests pin that, and pin that a run's own
``no_decision`` cases drop out of the paired set.
"""

from __future__ import annotations

import pytest

from evaluator.metrics import compare_runs
from reporting.report import render_comparison
from schemas.result import (
    BenchmarkResult,
    RunConfig,
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


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"human_req": "new task"}, "human_req"),
        (
            {
                "assistant_tool_call": {
                    "tool": "shell",
                    "raw": "new command",
                    "arguments": {"cwd": "/x"},
                }
            },
            "assistant_tool_call",
        ),
        ({"acceptable_result_types": ["allow"]}, "acceptable_result_types"),
        ({"expected_result_type": "ask"}, "expected_result_type"),
        ({"is_benign": True}, "is_benign"),
        ({"rules": {"ask": ["cmd"]}}, "rules"),
        ({"expected_detection": True}, "expected_detection"),
    ],
)
def test_changed_cases_are_excluded_from_paired_metrics(change, reason):
    original = _result("CHANGED", decision="allow", adapter="server")
    changed = BenchmarkResult.model_validate(original.model_dump() | change)
    stable = _result("STABLE", decision="deny", adapter="server")
    comparison = compare_runs([original, stable], [changed, stable])
    paired = comparison["paired"]
    assert paired["cases_compared"] == 1
    assert paired["a"]["asr"] == paired["b"]["asr"] == 0
    assert paired["excluded_cases"] == [{"case_id": "CHANGED", "reasons": [reason]}]
    assert comparison["overall"]["a"]["cases"] == 2


def test_same_length_different_history_is_not_a_pair_even_in_ablation():
    original = _result("HISTORY", decision="deny", adapter="server").model_copy(
        update={"history_turns_sent": 1, "source_history_digest": "original"}
    )
    changed = original.model_copy(update={"source_history_digest": "changed"})
    for ablation in (False, True):
        paired = compare_runs([original], [changed], history_ablation=ablation)["paired"]
        assert paired["cases_compared"] == 0
        assert "source_history_digest" in paired["excluded_cases"][0]["reasons"]


def test_history_ablation_requires_opt_in_and_keeps_other_checks():
    full = _result("HISTORY", decision="deny", adapter="server").model_copy(
        update={"history_turns_sent": 2, "source_history_digest": "same"}
    )
    stripped = full.model_copy(update={"history_turns_sent": 0})
    assert compare_runs([full], [stripped])["paired"]["cases_compared"] == 0
    paired = compare_runs([full], [stripped], history_ablation=True)["paired"]
    assert paired["cases_compared"] == 1 and paired["history_ablation"]
    changed = stripped.model_copy(update={"human_req": "different task"})
    assert compare_runs([full], [changed], history_ablation=True)["paired"]["cases_compared"] == 0


def test_ablation_does_not_allow_two_different_nonzero_history_lengths():
    a = _result("HISTORY", decision="deny", adapter="server").model_copy(
        update={"history_turns_sent": 2, "source_history_digest": "same"}
    )
    b = a.model_copy(update={"history_turns_sent": 1})
    assert compare_runs([a], [b], history_ablation=True)["paired"]["cases_compared"] == 0


def test_legacy_history_is_reported_as_unverified():
    a = _result("LEGACY", decision="deny", adapter="server").model_copy(
        update={"history_turns_sent": 1}
    )
    comparison = compare_runs([a], [a])
    assert comparison["paired"]["cases_compared"] == 1
    assert any("history" in warning and "LEGACY" in warning for warning in comparison["warnings"])


def test_duplicate_ids_do_not_silently_overwrite_results():
    a = _result("DUPLICATE", decision="deny", adapter="server")
    with pytest.raises(ValueError, match="duplicate case_id"):
        compare_runs([a, a], [a])


def test_defaults_and_unordered_expectations_do_not_change_case_identity():
    a = _result("SAME", decision="deny", adapter="server", acceptable=["deny", "ask"])
    b = a.model_copy(
        update={
            "assistant_tool_call": {
                "tool": "shell",
                "raw": "cmd",
                "arguments": {"cwd": "/x", "paths": [], "domains": [], "mcp": None, "method": None},
            },
            "acceptable_result_types": list(reversed(a.acceptable_result_types)),
        }
    )
    assert compare_runs([a], [b])["paired"]["cases_compared"] == 1


def test_profile_changes_are_visible_and_execution_modes_cannot_be_paired():
    row = _result("SAME", decision="deny", adapter="server")
    a = RunConfig(service_url="http://localhost", profile_snapshot_digest="first")
    b = a.model_copy(update={"profile_snapshot_digest": "second"})
    comparison = compare_runs([row], [row], config_a=a, config_b=b)
    assert comparison["paired"]["cases_compared"] == 1
    assert "profile_snapshot_digest" in [
        d["field"] for d in comparison["configuration_differences"]
    ]
    b.execution_mode = "harness_loop"
    assert compare_runs([row], [row], config_a=a, config_b=b)["paired"]["cases_compared"] == 0


def test_text_report_explains_exclusions_and_ablation():
    a = _result("CHANGED", decision="deny", adapter="server")
    b = a.model_copy(update={"human_req": "different task"})
    text = render_comparison([a], [b], label_a="a", label_b="b", history_ablation=True)
    assert "CHANGED" in text and "human_req" in text and "excluded" in text
    assert "history ablation" in text


def test_changed_pipeline_requirements_and_stale_policy_digest_cannot_match():
    a = _result("PIPELINE", decision="deny", adapter="server").model_copy(
        update={
            "pipeline_expectations": {"expected_stage": 1, "expected_rule_id_prefix": "client."},
            "rules": {"deny": ["cmd"]},
            "rules_digest": "stale",
        }
    )
    b = a.model_copy(update={"pipeline_expectations": {}, "rules": {"allow": ["cmd"]}})
    reasons = compare_runs([a], [b])["paired"]["excluded_cases"][0]["reasons"]
    assert reasons == ["pipeline_expectations", "rules"]


def test_rule_order_does_not_change_effective_policy_but_absent_is_not_empty():
    a = _result("RULES", decision="deny", adapter="server").model_copy(
        update={"rules": {"deny": ["git *", "cmd"]}}
    )
    b = a.model_copy(update={"rules": {"deny": ["cmd", "git *", "cmd"]}})
    assert compare_runs([a], [b])["paired"]["cases_compared"] == 1
    a.rules, b.rules = None, {}
    assert compare_runs([a], [b])["paired"]["cases_compared"] == 0


@pytest.mark.parametrize("kind", ["network", "mcp_call"])
def test_nested_action_changes_are_detected(kind):
    args = (
        {"cwd": "/x", "domains": ["github.com"], "method": "GET"}
        if kind == "network"
        else {"cwd": "/x", "mcp": {"server": "fs", "tool": "write", "arguments": {"path": "a"}}}
    )
    a = _result("ACTION", decision="deny", adapter="server").model_copy(
        update={"assistant_tool_call": {"tool": kind, "raw": "", "arguments": args}}
    )
    b = a.model_copy(deep=True)
    if kind == "network":
        b.assistant_tool_call["arguments"]["method"] = "DELETE"
    else:
        b.assistant_tool_call["arguments"]["mcp"]["arguments"]["path"] = "b"
    assert compare_runs([a], [b])["paired"]["cases_compared"] == 0


def test_saved_comparison_loads_config_and_explicit_ablation(tmp_path, capsys):
    import json

    from cli import main
    from storage.sqlite import BenchmarkStore

    path = tmp_path / "compare.sqlite3"
    full = _result("HISTORY", decision="deny", adapter="server").model_copy(
        update={
            "run_id": "full",
            "history_turns_sent": 2,
            "source_history_digest": "same",
            "pipeline_expectations": {},
        }
    )
    stripped = full.model_copy(update={"run_id": "stripped", "history_turns_sent": 0})
    with BenchmarkStore(path) as store:
        for row, mode in ((full, "full"), (stripped, "stripped")):
            store.start_run(
                row.run_id,
                RunConfig(service_url="http://localhost", history_mode=mode),
                total_cases=1,
            )
            store.insert_result(row)
        assert store.load_results("full")[0].source_history_digest == "same"
    args = ["compare", "full", "stripped", "--db", str(path)]
    assert main(args + ["--json"]) == 0
    ordinary = json.loads(capsys.readouterr().out)
    assert ordinary["paired"]["cases_compared"] == 0
    assert ordinary["configuration_differences"][0]["field"] == "history_mode"
    assert main(args + ["--history-ablation", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["paired"]["cases_compared"] == 1
    assert main(args + ["--history-ablation"]) == 0
    assert "history ablation" in capsys.readouterr().out
