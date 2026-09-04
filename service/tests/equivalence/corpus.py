"""Every shell command the table tests already assert on, plus whatever a
real log contributes. Used to prove a refactor of the normalizer and the
rules changed nothing observable.
"""

import json
from pathlib import Path

_TABLE_INPUTS = Path(__file__).with_name("commands.txt")


def commands() -> list[str]:
    return [line for line in _TABLE_INPUTS.read_text(encoding="utf-8").splitlines() if line]


def from_decision_log(path: Path) -> list[str]:
    """Raw commands from a JSONL decision log, if one is present locally.

    The log is developer-local and never committed; an absent file
    contributes nothing rather than failing the run.
    """
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line).get("raw")
        if raw:
            out.append(raw)
    return out
