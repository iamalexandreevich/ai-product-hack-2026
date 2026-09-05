"""Import a run recorded inside the sandbox container into the host results database.

The Claude Code sessions write to a database of their own (the container's ``/home/bench/out``),
so a payload that trashes the sandbox cannot take earlier runs with it. ``cli.py compare``
reads two runs from one database, so the sandbox run has to be copied over before the two
adapters can be compared:

    python tools/sandbox/import_run.py results/claude-sandbox/benchmark.sqlite3

Copies whole rows as they were stored; ``result_json`` stays the source of truth. Re-running
it is safe: rows are replaced, never duplicated.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Run from anywhere: the benchmark packages are imported by bare name (pyproject sets
# pythonpath = ["."]), which only holds when benchmark/ is on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from storage.sqlite import SCHEMA

TABLES = ("benchmark_cases", "benchmark_runs", "benchmark_results")


def import_runs(source: Path, target: Path) -> dict[str, int]:
    if not source.exists():
        raise SystemExit(f"no such database: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        conn.executescript(SCHEMA)
        conn.execute("ATTACH DATABASE ? AS src", (str(source),))
        counts: dict[str, int] = {}
        for table in TABLES:
            columns = [r[1] for r in conn.execute(f"PRAGMA src.table_info({table})")]
            if not columns:
                counts[table] = 0
                continue
            names = ", ".join(columns)
            cur = conn.execute(
                f"INSERT OR REPLACE INTO main.{table} ({names}) SELECT {names} FROM src.{table}"
            )
            counts[table] = cur.rowcount
        conn.commit()
        return counts
    finally:
        conn.close()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    source = Path(sys.argv[1])
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("results/benchmark.sqlite3")
    counts = import_runs(source, target)
    print(f"imported into {target}: " + ", ".join(f"{t} {n}" for t, n in counts.items()))
    runs = (
        sqlite3.connect(target)
        .execute(
            "SELECT run_id, status, total_cases FROM benchmark_runs ORDER BY started_at DESC LIMIT 5"
        )
        .fetchall()
    )
    for run_id, status, total in runs:
        print(f"  {run_id}  {status}  {total} case(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
