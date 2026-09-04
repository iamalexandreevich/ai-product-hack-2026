"""The refactor changed nothing observable.

Deleted once tasks 4 and 7 are merged -- it exists to make those two
steps safe, not to be maintained.
"""

import json
from pathlib import Path

import pytest

from agentgate.api.schemas import DecideRequest
from agentgate.normalize import normalize
from agentgate.rules.chain import STAGE1
from tests.factories import WORKSPACE, hard_deny_policy

BASELINE = json.loads(Path(__file__).with_name("baseline.json").read_text(encoding="utf-8"))


def _current(raw: str) -> dict:
    try:
        action = normalize(DecideRequest(
            harness="t", tool="shell", raw=raw, args={"cwd": WORKSPACE}, user_request="x",
        ))
        verdict = STAGE1.evaluate(action, hard_deny_policy())
    except Exception as exc:  # noqa: BLE001 - an input the request schema rejects is part of the corpus
        return {"error": type(exc).__name__}
    return {
        "action": action.to_dict(),
        "hash": action.action_hash(),
        "verdict": None if verdict is None else
                   {"decision": verdict.decision.value, "rule_id": verdict.rule_id,
                    "reason": verdict.reason, "suggest": verdict.suggest, "hard": verdict.hard},
    }


@pytest.mark.parametrize("raw", sorted(BASELINE), ids=range(len(BASELINE)))
def test_normalization_and_verdict_match_the_baseline(raw):
    assert _current(raw) == BASELINE[raw]
