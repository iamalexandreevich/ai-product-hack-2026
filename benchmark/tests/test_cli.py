"""CLI behaviour, including the guard against sending attacks to a remote endpoint."""

from __future__ import annotations

import copy
import json

import pytest

import cli
from dataset.validator import validate_dataset
from schemas.case import REQUIRED_DIFFICULTIES
from tests.conftest import DATASET_PATH, VALID_CASE, write_case

DIFFICULTIES = ["easy", "medium", "hard", "adversarial", "realistic_production"]


@pytest.fixture
def tiny_dataset(tmp_path):
    directory = tmp_path / "sample_category"
    for index, difficulty in enumerate(DIFFICULTIES):
        payload = copy.deepcopy(VALID_CASE)
        payload["id"] = f"SAMPLE_{index + 1:03d}"
        payload["difficulty"] = difficulty
        payload["human_req"] = f"request {index}"
        payload["assistant_tool_call"]["raw"] = f"curl https://h{index}.example.net/{index}"
        write_case(directory, payload)
    return tmp_path


def test_validate_command_on_a_valid_dataset(tiny_dataset, capsys):
    assert cli.main(["validate", "--path", str(tiny_dataset)]) == 0
    assert "errors: 0" in capsys.readouterr().out


def test_validate_command_fails_on_an_incomplete_category(tiny_dataset, capsys):
    (tiny_dataset / "sample_category" / "SAMPLE_005.yaml").unlink()
    assert cli.main(["validate", "--path", str(tiny_dataset)]) == 1
    assert "exactly 5 cases" in capsys.readouterr().err


def test_dry_run_sends_nothing(tiny_dataset, capsys):
    code = cli.main(["benchmark", "--path", str(tiny_dataset), "--dry-run", "--no-db"])
    assert code == 0
    out = capsys.readouterr().out
    assert "dry run, no requests sent" in out
    assert "SAMPLE_001" in out


def test_dry_run_respects_filters(tiny_dataset, capsys):
    code = cli.main(
        ["benchmark", "--path", str(tiny_dataset), "--difficulty", "easy", "--dry-run", "--no-db"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "1 case(s) selected" in out


def test_benchmark_refuses_a_remote_endpoint_by_default(tiny_dataset, capsys):
    code = cli.main(
        ["benchmark", "--path", str(tiny_dataset), "--url", "https://agentgate.example.com"]
    )
    assert code == 2
    assert "refusing to send attack traffic" in capsys.readouterr().err


def test_endpoint_guard_accepts_local_addresses():
    assert cli._endpoint_allowed("http://127.0.0.1:8400", allow_remote=False)
    assert cli._endpoint_allowed("http://localhost:8400", allow_remote=False)
    assert not cli._endpoint_allowed("https://prod.example.com", allow_remote=False)
    assert cli._endpoint_allowed("https://prod.example.com", allow_remote=True)


def test_benchmark_aborts_when_the_dataset_is_invalid(tiny_dataset, capsys):
    (tiny_dataset / "sample_category" / "SAMPLE_005.yaml").write_text("id: [broken", "utf-8")
    code = cli.main(["benchmark", "--path", str(tiny_dataset), "--no-db"])
    assert code == 1
    assert "nothing was sent to the service" in capsys.readouterr().err


def test_runs_command_on_an_empty_database(tmp_path, capsys):
    code = cli.main(["runs", "--db", str(tmp_path / "bench.sqlite3")])
    assert code == 0
    assert "no runs stored yet" in capsys.readouterr().out


def test_report_command_without_runs(tmp_path, capsys):
    code = cli.main(["report", "--db", str(tmp_path / "bench.sqlite3")])
    assert code == 1
    assert "no runs stored yet" in capsys.readouterr().err


def test_report_command_renders_stored_results(tmp_path, capsys):
    from schemas.result import RunConfig
    from storage.sqlite import BenchmarkStore
    from tests.test_storage import _result

    db = tmp_path / "bench.sqlite3"
    with BenchmarkStore(db) as store:
        store.start_run("run-1", RunConfig(service_url="http://127.0.0.1:8400"), total_cases=1)
        store.insert_result(_result())
        store.finish_run("run-1")

    assert cli.main(["report", "--db", str(db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run-1"
    assert payload["totals"]["total_cases"] == 1


# -- the shipped dataset -----------------------------------------------------


def test_shipped_dataset_is_valid():
    report = validate_dataset(DATASET_PATH)
    assert report.ok, "\n".join(str(issue) for issue in report.errors)


def test_shipped_dataset_has_five_cases_per_category():
    report = validate_dataset(DATASET_PATH)
    by_category: dict[str, set[str]] = {}
    for case in report.cases:
        by_category.setdefault(case.attack_category, set()).add(case.difficulty.value)
    assert by_category, "dataset is empty"
    for category, difficulties in by_category.items():
        assert difficulties == {d.value for d in REQUIRED_DIFFICULTIES}, category


def test_shipped_dataset_covers_both_boundary_fields():
    report = validate_dataset(DATASET_PATH)
    for case in report.cases:
        assert case.human_req.strip(), case.id
        assert case.assistant_tool_call.tool, case.id


def test_shipped_dataset_includes_a_benign_control_group():
    report = validate_dataset(DATASET_PATH)
    benign = [c for c in report.cases if c.is_benign]
    assert len(benign) >= 5
    assert all(c.expected_service_result.value == "allow" for c in benign)
