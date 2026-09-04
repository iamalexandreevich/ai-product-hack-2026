"""SQLite storage and the streaming recorder."""

from __future__ import annotations

import copy
import sqlite3

from runner.recorder import Recorder
from schemas.case import BenchmarkCase
from schemas.result import (
    BenchmarkResult,
    ComponentsSource,
    CostSource,
    ModelSource,
    RunConfig,
    ServiceResultType,
)
from storage.sqlite import BenchmarkStore
from tests.conftest import VALID_CASE


def _result(case_id: str = "SAMPLE_001", score: int = 1) -> BenchmarkResult:
    return BenchmarkResult(
        run_id="run-1",
        case_id=case_id,
        attack_category="sample_category",
        attack_name="sample_attack",
        difficulty="easy",
        tags=["exfiltration"],
        human_req="Check whether the service is up.",
        assistant_tool_call={"tool": "shell", "raw": "curl ...", "arguments": {"cwd": "/x"}},
        execution_time_ms=12.5,
        service_latency_total_ms=1.1,
        cost=None,
        cost_source=CostSource.UNAVAILABLE,
        cost_unavailable_reason="no token usage",
        components_activated=["normalizer", "stage1_rules"],
        components_source=ComponentsSource.DERIVED,
        service_result_type=ServiceResultType.DENY,
        service_raw_response={"decision": "deny"},
        stage=1,
        rule_id="hard-deny.exfil",
        cached=False,
        expected_result_type="deny",
        acceptable_result_types=["deny"],
        score=score,
        score_explanation="decision=deny accepted={deny}",
        expected_detection=True,
        detected=True,
        detection_correct=True,
        model_source=ModelSource.NOT_APPLICABLE,
    )


def test_store_creates_all_three_tables(tmp_path):
    path = tmp_path / "bench.sqlite3"
    with BenchmarkStore(path):
        pass
    with sqlite3.connect(path) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"benchmark_cases", "benchmark_runs", "benchmark_results"} <= tables


def test_store_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "bench.sqlite3"
    with BenchmarkStore(path):
        pass
    assert path.exists()


def test_cases_are_upserted(tmp_path):
    case = BenchmarkCase.model_validate(copy.deepcopy(VALID_CASE))
    with BenchmarkStore(tmp_path / "bench.sqlite3") as store:
        assert store.upsert_cases([case]) == 1
        assert store.upsert_cases([case]) == 1
        assert store.count_cases() == 1


def test_run_and_results_round_trip(tmp_path):
    config = RunConfig(service_url="http://127.0.0.1:8400", concurrency=2)
    with BenchmarkStore(tmp_path / "bench.sqlite3") as store:
        store.start_run("run-1", config, total_cases=2, service_version="0.1.0")
        store.insert_result(_result("SAMPLE_001", score=1))
        store.insert_result(_result("SAMPLE_002", score=0))
        store.finish_run("run-1")

        loaded = store.load_results("run-1")
        assert [r.case_id for r in loaded] == ["SAMPLE_001", "SAMPLE_002"]
        assert loaded[0].components_activated == ["normalizer", "stage1_rules"]
        assert loaded[0].cost_source is CostSource.UNAVAILABLE
        assert loaded[1].score == 0

        assert store.run_config("run-1").concurrency == 2
        assert store.latest_run_id() == "run-1"

        runs = store.list_runs()
        assert runs[0]["executed"] == 2
        assert runs[0]["passed"] == 1
        assert runs[0]["status"] == "completed"
        assert runs[0]["service_version"] == "0.1.0"


def test_insert_is_idempotent_per_run_and_case(tmp_path):
    with BenchmarkStore(tmp_path / "bench.sqlite3") as store:
        store.start_run("run-1", RunConfig(service_url="u"), total_cases=1)
        store.insert_result(_result(score=0))
        store.insert_result(_result(score=1))
        results = store.load_results("run-1")
        assert len(results) == 1
        assert results[0].score == 1


def test_required_dimensions_are_queryable_as_columns(tmp_path):
    path = tmp_path / "bench.sqlite3"
    with BenchmarkStore(path) as store:
        store.start_run("run-1", RunConfig(service_url="u"), total_cases=1)
        store.insert_result(_result())
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            """
            SELECT execution_time_ms, cost, cost_source, components_activated,
                   service_result_type, score, model, model_source
            FROM benchmark_results WHERE run_id = 'run-1'
            """
        ).fetchone()
    assert row[0] == 12.5
    assert row[1] is None
    assert row[2] == "unavailable"
    assert "stage1_rules" in row[3]
    assert row[4] == "deny"
    assert row[5] == 1
    assert row[6] is None
    assert row[7] == "not_applicable"


def test_recorder_writes_sqlite_and_jsonl(tmp_path):
    jsonl = tmp_path / "out" / "stream.jsonl"
    with BenchmarkStore(tmp_path / "bench.sqlite3") as store:
        store.start_run("run-1", RunConfig(service_url="u"), total_cases=1)
        with Recorder(store=store, jsonl_path=jsonl) as recorder:
            recorder.record(_result())
        assert len(store.load_results("run-1")) == 1
    assert jsonl.exists()
    assert '"case_id":"SAMPLE_001"' in jsonl.read_text(encoding="utf-8").replace(" ", "")


def test_recorder_works_without_storage(tmp_path):
    with Recorder() as recorder:
        recorder.record(_result())
    assert len(recorder.results) == 1


def test_recorder_survives_storage_failure(tmp_path):
    store = BenchmarkStore(tmp_path / "bench.sqlite3")
    store.close()  # every write now raises
    with Recorder(store=store) as recorder:
        recorder.record(_result())
    assert len(recorder.results) == 1
