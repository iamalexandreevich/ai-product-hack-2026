"""Streaming recorder: every finished case lands in SQLite and in a JSONL file.

Results are written as they arrive, so an interrupted run keeps everything it already
measured. A storage failure is logged and never aborts the run.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Self, TextIO

from schemas.result import BenchmarkResult
from storage.sqlite import BenchmarkStore

logger = logging.getLogger(__name__)


class Recorder:
    def __init__(
        self,
        store: BenchmarkStore | None = None,
        jsonl_path: Path | None = None,
        *,
        progress: bool = False,
    ) -> None:
        self.store = store
        self.jsonl_path = jsonl_path
        self.progress = progress
        self._jsonl: TextIO | None = None
        self.results: list[BenchmarkResult] = []

    def __enter__(self) -> Self:
        if self.jsonl_path is not None:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            self._jsonl = self.jsonl_path.open("w", encoding="utf-8")
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._jsonl is not None:
            self._jsonl.close()
            self._jsonl = None

    def record(self, result: BenchmarkResult) -> None:
        self.results.append(result)

        if self.store is not None:
            try:
                self.store.insert_result(result)
            except Exception:  # storage must never break a run
                logger.exception("failed to persist result for case %s", result.case_id)

        if self._jsonl is not None:
            try:
                self._jsonl.write(result.model_dump_json() + "\n")
                self._jsonl.flush()
            except OSError:
                logger.exception("failed to append result for case %s to JSONL", result.case_id)

        if self.progress:
            mark = "PASS" if result.score else "FAIL"
            print(
                f"  {mark}  {result.case_id:<38} "
                f"{result.service_result_type.value:<5} "
                f"(expected {result.expected_result_type.value}) "
                f"{result.execution_time_ms:7.1f} ms"
            )
