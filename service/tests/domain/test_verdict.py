import dataclasses

import pytest

from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict


def test_allow_carries_rule_and_stage():
    verdict = Verdict.allow("allowlist.readonly")
    assert verdict.decision is DecisionKind.allow
    assert verdict.rule_id == "allowlist.readonly"
    assert verdict.stage == 1


def test_allow_never_carries_reason_or_suggest():
    verdict = Verdict.allow("allowlist.readonly")
    assert verdict.reason == "" and verdict.suggest == ""


def test_deny_is_soft_unless_asked_to_be_hard():
    assert Verdict.deny("profile.path", "outside").hard is False


def test_hard_deny_is_marked_hard():
    assert Verdict.deny("hard-deny.exfil", "secret sent", hard=True).hard is True


def test_ask_is_never_hard():
    assert Verdict.ask("ambiguous.wrapper-depth", "cannot resolve").hard is False


def test_classifier_verdict_carries_model_and_raw_response():
    verdict = Verdict(
        decision=DecisionKind.deny, stage=2, reason="why", suggest="alt",
        model="qwen-4b", raw_response={"choices": []},
    )
    assert verdict.stage == 2 and verdict.model == "qwen-4b"
    assert verdict.raw_response == {"choices": []}


def test_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Verdict.allow("allowlist.readonly").decision = DecisionKind.deny


def test_escalated_turns_any_verdict_into_ask():
    escalated = Verdict.deny("profile.path", "outside", "stay inside").escalated(3)
    assert escalated.decision is DecisionKind.ask
    assert escalated.rule_id == "escalation"
    assert escalated.suggest == ""


def test_escalated_reason_names_the_hit_count():
    assert "3" in Verdict.deny("profile.path", "outside").escalated(3).reason


def test_escalated_keeps_the_stage_of_the_verdict_it_replaces():
    assert Verdict.deny("profile.path", "x", stage=1).escalated(2).stage == 1
