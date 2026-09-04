"""SQLite storage for Benchmark V1.

Three tables, as required by the benchmark spec: ``benchmark_cases``,
``benchmark_runs``, ``benchmark_results``. Every result row keeps the six required
dimensions in dedicated columns *and* the complete result document in ``result_json``,
so a report can be regenerated from the database without loss.

The dedicated columns exist so that the metrics can be grouped in SQL without unpacking
JSON: ``attack_category``, ``difficulty``, ``dataset_source`` and ``stage`` are the four
dimensions the aggregate metrics group by. Re-running the same case inside the same run
refreshes every measured column, not just the score, so a column never disagrees with
``result_json``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from schemas.case import BenchmarkCase
from schemas.result import BenchmarkResult, RunConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmark_cases (
    case_id         TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    difficulty      TEXT NOT NULL,
    attack_name     TEXT NOT NULL,
    is_benign       INTEGER NOT NULL DEFAULT 0,
    dataset_source  TEXT NOT NULL DEFAULT 'team',
    case_definition TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    run_id          TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    service_version TEXT,
    configuration   TEXT NOT NULL,
    total_cases     INTEGER,
    status          TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS benchmark_results (
    run_id                   TEXT NOT NULL,
    case_id                  TEXT NOT NULL,
    ts                       TEXT NOT NULL,
    adapter_name             TEXT NOT NULL DEFAULT 'server',
    attack_category          TEXT NOT NULL,
    difficulty               TEXT NOT NULL,
    is_benign                INTEGER NOT NULL DEFAULT 0,
    dataset_source           TEXT NOT NULL DEFAULT 'team',
    execution_time_ms        REAL NOT NULL,
    service_latency_total_ms REAL,
    input_tokens             INTEGER,
    output_tokens            INTEGER,
    total_tokens             INTEGER,
    cost                     REAL,
    cost_currency            TEXT,
    cost_source              TEXT NOT NULL,
    cost_unavailable_reason  TEXT,
    components_activated     TEXT NOT NULL,
    components_source        TEXT NOT NULL,
    service_result_type      TEXT NOT NULL,
    raw_response             TEXT NOT NULL,
    stage                    INTEGER,
    rule_id                  TEXT,
    cached                   INTEGER,
    expected_result_type     TEXT NOT NULL,
    score                    INTEGER NOT NULL,
    score_explanation        TEXT NOT NULL,
    expected_detection       INTEGER NOT NULL,
    detected                 INTEGER,
    detection_correct        INTEGER,
    model                    TEXT,
    provider                 TEXT,
    model_version            TEXT,
    model_source             TEXT NOT NULL,
    error                    TEXT,
    contract_violation       TEXT,
    result_json              TEXT NOT NULL,
    PRIMARY KEY (run_id, case_id),
    FOREIGN KEY (run_id) REFERENCES benchmark_runs (run_id)
);

CREATE INDEX IF NOT EXISTS idx_results_run ON benchmark_results (run_id);
CREATE INDEX IF NOT EXISTS idx_results_category ON benchmark_results (attack_category, score);
CREATE INDEX IF NOT EXISTS idx_results_difficulty ON benchmark_results (difficulty, score);
"""

# Columns added after the first release. A database created by an older version is
# migrated in place: the metrics that group by these columns must work on runs that were
# already stored. ``result_json`` is the source of truth either way, so an old row keeps
# the default until it is written again.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("benchmark_cases", "dataset_source", "TEXT NOT NULL DEFAULT 'team'"),
    ("benchmark_results", "dataset_source", "TEXT NOT NULL DEFAULT 'team'"),
    ("benchmark_results", "cost_currency", "TEXT"),
    ("benchmark_results", "adapter_name", "TEXT NOT NULL DEFAULT 'server'"),
)


class BenchmarkStore:
    """Thin synchronous wrapper around a SQLite database file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path.parent) not in ("", "."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        The index over ``dataset_source`` is created here rather than in ``SCHEMA``: on a
        database written before the column existed, ``SCHEMA`` runs first and would fail
        on an index over a column that is only added below.
        """
        for table, column, definition in MIGRATIONS:
            existing = {
                row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self._conn.executescript(
            "CREATE INDEX IF NOT EXISTS idx_results_source "
            "ON benchmark_results (dataset_source, score);"
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- cases ---------------------------------------------------------------

    def upsert_cases(self, cases: Iterable[BenchmarkCase]) -> int:
        now = _now()
        rows = [
            (
                case.id,
                case.attack_category,
                case.difficulty.value,
                case.attack_name,
                int(case.is_benign),
                case.dataset_source.value,
                case.model_dump_json(),
                now,
            )
            for case in cases
        ]
        self._conn.executemany(
            """
            INSERT INTO benchmark_cases
                (case_id, category, difficulty, attack_name, is_benign, dataset_source,
                 case_definition, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (case_id) DO UPDATE SET
                category = excluded.category,
                difficulty = excluded.difficulty,
                attack_name = excluded.attack_name,
                is_benign = excluded.is_benign,
                dataset_source = excluded.dataset_source,
                case_definition = excluded.case_definition,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        self._conn.commit()
        return len(rows)

    def count_cases(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM benchmark_cases").fetchone()[0])

    # -- runs ----------------------------------------------------------------

    def start_run(
        self,
        run_id: str,
        config: RunConfig,
        *,
        total_cases: int,
        service_version: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO benchmark_runs
                (run_id, started_at, service_version, configuration, total_cases, status)
            VALUES (?, ?, ?, ?, ?, 'running')
            ON CONFLICT (run_id) DO UPDATE SET
                configuration = excluded.configuration,
                total_cases = excluded.total_cases
            """,
            (run_id, _now(), service_version, config.model_dump_json(), total_cases),
        )
        self._conn.commit()

    def finish_run(self, run_id: str, *, status: str = "completed") -> None:
        self._conn.execute(
            "UPDATE benchmark_runs SET finished_at = ?, status = ? WHERE run_id = ?",
            (_now(), status, run_id),
        )
        self._conn.commit()

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT r.run_id, r.started_at, r.finished_at, r.status, r.total_cases,
                   r.service_version, r.configuration,
                   COUNT(res.case_id) AS executed,
                   COALESCE(SUM(res.score), 0) AS passed
            FROM benchmark_runs r
            LEFT JOIN benchmark_results res ON res.run_id = r.run_id
            GROUP BY r.run_id
            ORDER BY r.started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_run_id(self) -> str | None:
        row = self._conn.execute(
            "SELECT run_id FROM benchmark_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return row["run_id"] if row else None

    def run_config(self, run_id: str) -> RunConfig | None:
        row = self._conn.execute(
            "SELECT configuration FROM benchmark_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return RunConfig.model_validate_json(row["configuration"]) if row else None

    # -- results -------------------------------------------------------------

    def insert_result(self, result: BenchmarkResult) -> None:
        self._conn.execute(
            """
            INSERT INTO benchmark_results (
                run_id, case_id, ts, adapter_name,
                attack_category, difficulty, is_benign, dataset_source,
                execution_time_ms, service_latency_total_ms,
                input_tokens, output_tokens, total_tokens,
                cost, cost_currency, cost_source, cost_unavailable_reason,
                components_activated, components_source,
                service_result_type, raw_response, stage, rule_id, cached,
                expected_result_type, score, score_explanation,
                expected_detection, detected, detection_correct,
                model, provider, model_version, model_source,
                error, contract_violation, result_json
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            ON CONFLICT (run_id, case_id) DO UPDATE SET
                ts = excluded.ts,
                adapter_name = excluded.adapter_name,
                dataset_source = excluded.dataset_source,
                execution_time_ms = excluded.execution_time_ms,
                service_latency_total_ms = excluded.service_latency_total_ms,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                total_tokens = excluded.total_tokens,
                cost = excluded.cost,
                cost_currency = excluded.cost_currency,
                cost_source = excluded.cost_source,
                cost_unavailable_reason = excluded.cost_unavailable_reason,
                components_activated = excluded.components_activated,
                components_source = excluded.components_source,
                service_result_type = excluded.service_result_type,
                raw_response = excluded.raw_response,
                stage = excluded.stage,
                rule_id = excluded.rule_id,
                cached = excluded.cached,
                score = excluded.score,
                score_explanation = excluded.score_explanation,
                detected = excluded.detected,
                detection_correct = excluded.detection_correct,
                model = excluded.model,
                provider = excluded.provider,
                model_version = excluded.model_version,
                model_source = excluded.model_source,
                error = excluded.error,
                contract_violation = excluded.contract_violation,
                result_json = excluded.result_json
            """,
            (
                result.run_id,
                result.case_id,
                result.ts.isoformat(),
                result.adapter_name,
                result.attack_category,
                result.difficulty.value,
                int(result.is_benign),
                result.dataset_source.value,
                result.execution_time_ms,
                result.service_latency_total_ms,
                result.input_tokens,
                result.output_tokens,
                result.total_tokens,
                result.cost,
                result.cost_currency,
                result.cost_source.value,
                result.cost_unavailable_reason,
                json.dumps(result.components_activated, ensure_ascii=False),
                result.components_source.value,
                result.service_result_type.value,
                json.dumps(result.service_raw_response, ensure_ascii=False),
                result.stage,
                result.rule_id,
                None if result.cached is None else int(result.cached),
                result.expected_result_type.value,
                result.score,
                result.score_explanation,
                int(result.expected_detection),
                None if result.detected is None else int(result.detected),
                None if result.detection_correct is None else int(result.detection_correct),
                result.model,
                result.provider,
                result.model_version,
                result.model_source.value,
                result.error,
                result.contract_violation,
                result.model_dump_json(),
            ),
        )
        self._conn.commit()

    def load_results(self, run_id: str) -> list[BenchmarkResult]:
        rows = self._conn.execute(
            "SELECT result_json FROM benchmark_results WHERE run_id = ? ORDER BY case_id",
            (run_id,),
        ).fetchall()
        return [BenchmarkResult.model_validate_json(row["result_json"]) for row in rows]


def _now() -> str:
    return datetime.now(UTC).isoformat()
