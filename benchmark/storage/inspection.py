"""Separate tables keep inspect verdicts out of pre-action scores and comparisons."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from schemas.inspect import InspectResult


def save_inspections(
    path: Path,
    run_id: str,
    config: dict[str, Any],
    results: list[InspectResult],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS inspect_runs "
            "(run_id TEXT PRIMARY KEY, configuration TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS inspect_results "
            "(run_id TEXT NOT NULL, case_id TEXT NOT NULL, result_json TEXT NOT NULL, "
            "PRIMARY KEY(run_id, case_id))"
        )
        # Run ids are immutable: a duplicate must not mix old and new populations.
        conn.execute("INSERT INTO inspect_runs VALUES (?, ?)", (run_id, json.dumps(config)))
        conn.executemany(
            "INSERT INTO inspect_results VALUES (?, ?, ?)",
            [(run_id, r.case_id, r.model_dump_json()) for r in results],
        )


def load_inspections(path: Path, run_id: str) -> tuple[dict[str, Any], list[InspectResult]]:
    if not path.is_file():
        raise ValueError(f"database does not exist: {path}")
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT configuration FROM inspect_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"inspection run not found: {run_id}")
        rows = conn.execute(
            "SELECT result_json FROM inspect_results WHERE run_id=? ORDER BY rowid", (run_id,)
        ).fetchall()
    return json.loads(row[0]), [InspectResult.model_validate_json(r[0]) for r in rows]
