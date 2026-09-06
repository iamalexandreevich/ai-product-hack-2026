"""Task outcomes stay separate from pre-action accuracy and inspect containment."""

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def measured_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize(root: Path):
    source = read_json(root / "source.json")
    manifests = list(root.glob("*/one_click_result.json"))
    if len(manifests) != 1:
        raise ValueError("expected exactly one native invocation below the run directory")
    invocation = manifests[0].parent
    manifest = read_json(manifests[0])
    score = read_json(invocation / "score.json")
    if score.get("schema_version") != "actbench.offline_score.v1":
        raise ValueError("unsupported native attack score schema")
    # A collection result's zero-valued placeholder fields are never read as scores.
    rows = score["results"]
    valid = [
        r
        for r in rows
        if r.get("evaluation_error") is False
        and isinstance(r.get("attack_pass"), bool)
        and measured_number(r.get("ags"))
    ]
    if len(rows) != score.get("trajectory_count") or len(valid) != score.get("valid_scores"):
        raise ValueError("native score counts disagree with its rows")
    events_path = root / "gate-events.jsonl"
    events = (
        [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if events_path.exists()
        else []
    )
    decisions = [e for e in events if e.get("phase") == "decide"]
    errors = sum(e.get("phase") == "error" for e in events)
    self_test = source["self_test"] or manifest["request"]["backend"] == "fake"
    eligible = (
        not self_test
        and manifest["status"] == "complete"
        and bool(valid)
        and len(valid) == len(rows)
        and not errors
        and (source["guard"] == "off" or bool(decisions))
    )
    clean = {}
    for path in (root / "clean-cache").glob("*.json"):
        baseline = read_json(path)
        duration = baseline.get("execution_time")
        if baseline.get("status") == "success" and measured_number(duration) and duration > 0:
            clean[baseline["source_task_id"]] = duration
    utility_path = root / "utility.json"
    utility = read_json(utility_path) if utility_path.exists() else None
    if utility is not None and utility.get("schema_version") != "actbench.utility_score.v1":
        raise ValueError("unsupported native utility score schema")
    utility_rows = utility.get("results", []) if utility else []
    valid_utility = [
        row
        for row in utility_rows
        if row.get("evaluation_error") is False
        and measured_number(row.get("ugs"))
        and 0 <= row["ugs"] <= 1
        and isinstance(row.get("task_pass"), bool)
    ]
    expected_tasks = Counter(row["task_id"] for row in rows)
    utility_tasks = Counter(row.get("source_task_id") for row in valid_utility)
    utility_available = bool(
        eligible
        and utility
        and utility.get("trajectory_count") == utility.get("valid_scores") == len(valid_utility)
        and len(valid_utility) == len(utility_rows)
        and utility_tasks == expected_tasks
        and utility.get("evaluation_errors") == 0
        and utility.get("judge_model") == manifest["request"]["judge_model"]
    )
    return {
        "schema_version": "agentgate.actbench_report.v1",
        "execution_mode": "harness_task",
        "source": source["source"],
        "guard": source["guard"],
        "backend": manifest["request"]["backend"],
        "model": manifest["request"]["model"],
        "self_test": self_test,
        "scoring_mode": score["scoring_mode"],
        "judge_model": manifest["request"]["judge_model"],
        "attack_success_threshold": score["attack_success_threshold"],
        "measurement_eligible": eligible,
        "tasks": [r["task_id"] for r in rows],
        "valid_attack_scores": len(valid),
        "invalid_attack_scores": len(rows) - len(valid),
        "asr": mean(r["attack_pass"] for r in valid) if eligible else None,
        "mean_ags": mean(r["ags"] for r in valid) if eligible else None,
        "valid_utility_scores": len(valid_utility),
        "mean_ugs": mean(row["ugs"] for row in valid_utility) if utility_available else None,
        "task_completion_rate": mean(row["task_pass"] for row in valid_utility)
        if utility_available
        else None,
        "utility_unavailable_reason": None
        if utility_available
        else "No complete native UGS evaluation of this run's clean trajectories",
        "clean_task_seconds": clean if eligible else {},
        "gate_decisions": dict(Counter(e["response"].get("decision") for e in decisions)),
        "benign_ask_count": sum(
            e.get("role") == "benign" and e["response"].get("decision") == "ask" for e in decisions
        ),
        "gate_errors": errors,
        "ask_handling": "block_and_feedback; no human approvals are simulated",
        "metrics_unavailable_reason": None
        if eligible
        else "Self-test, incomplete scoring, integration errors, or unverified guard activity",
        "artifacts": {
            "source_sha256": hashlib.sha256((root / "source.json").read_bytes()).hexdigest(),
            "score_sha256": hashlib.sha256((invocation / "score.json").read_bytes()).hexdigest(),
        },
    }


def compare(before, after):
    for key in (
        "execution_mode",
        "source",
        "backend",
        "model",
        "scoring_mode",
        "judge_model",
        "attack_success_threshold",
        "tasks",
    ):
        if before[key] != after[key]:
            raise ValueError(f"incomparable ActBench runs: {key} differs")
    if not before["measurement_eligible"] or not after["measurement_eligible"]:
        raise ValueError("cannot compare self-tests or incomplete measurements")
    if before["guard"] != "off" or after["guard"] == "off":
        raise ValueError("comparison expects guard-off followed by guard-on")
    matched = sorted(set(before["clean_task_seconds"]) & set(after["clean_task_seconds"]))
    return {
        "matched_clean_tasks": len(matched),
        "asr_before": before["asr"],
        "asr_after": after["asr"],
        "mean_ugs_before": before["mean_ugs"],
        "mean_ugs_after": after["mean_ugs"],
        "mean_clean_task_slowdown_ratio": mean(
            after["clean_task_seconds"][task] / before["clean_task_seconds"][task]
            for task in matched
        )
        if matched
        else None,
        "slowdown_unavailable_reason": None if matched else "No matched clean task timings",
        "task_completion_rate_before": before["task_completion_rate"],
        "task_completion_rate_after": after["task_completion_rate"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument(
        "--compare-to", type=Path, help="guarded run to pair with this guard-off run"
    )
    args = parser.parse_args(argv)
    result = summarize(args.run)
    if args.compare_to:
        result = compare(result, summarize(args.compare_to))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
