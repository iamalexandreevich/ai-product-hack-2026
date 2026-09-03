"""Append-only JSONL decision log.

Fail-closed for logging, not for decisions: a write failure here must never
propagate and must never change (or block) the decision already returned to
the caller — see service/CLAUDE.md's fail-closed rule and Gate.decide().
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class JsonlLogger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def write(self, record: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            log.warning("jsonl write failed: %s", exc)
