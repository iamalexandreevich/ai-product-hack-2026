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


def test_a_verdict_is_not_a_floor_by_default():
    assert Verdict.allow("allowlist.readonly").floor is False
    assert Verdict.ask("client.ask", "confirm").floor is False


def test_ask_can_be_built_as_a_floor():
    verdict = Verdict.ask("client.ask", "confirm", floor=True)
    assert verdict.floor is True and verdict.decision is DecisionKind.ask and verdict.stage == 1


def test_strictness_orders_allow_below_ask_below_deny():
    allow = Verdict.allow("allowlist.readonly")
    ask = Verdict.ask("client.ask", "confirm")
    deny = Verdict.deny("client.deny", "no")
    assert allow.strictness < ask.strictness < deny.strictness


def test_an_ordinary_verdict_is_escalatable():
    assert Verdict.deny("profile.path", "outside").escalatable is True


def test_a_hard_verdict_is_not_escalatable():
    assert Verdict.deny("hard-deny.pipe-exec", "no", hard=True).escalatable is False


def test_the_users_own_denial_is_not_escalatable():
    assert Verdict.deny("client.deny", "blocked by your rules").escalatable is False


def test_raised_to_no_floor_is_unchanged():
    verdict = Verdict.allow("allowlist.readonly")
    assert verdict.raised_to(None) is verdict


def test_raised_to_a_stricter_verdict_is_unchanged():
    verdict = Verdict.deny("hard-deny.exfil", "secret sent", hard=True)
    floor = Verdict.ask("client.ask", "confirm", floor=True)
    assert verdict.raised_to(floor) is verdict


def test_raised_to_a_tied_floor_takes_the_floors_decision_and_rule_id():
    verdict = Verdict.ask("ambiguous.wrapper-depth", "cannot resolve")
    floor = Verdict.ask("client.ask", "confirm", floor=True)
    raised = verdict.raised_to(floor)
    assert raised.decision is DecisionKind.ask
    assert raised.rule_id == "client.ask"


def test_raised_to_a_tied_floor_keeps_the_models_own_fields():
    verdict = Verdict(decision=DecisionKind.ask, stage=2, reason="unclear", model="qwen-4b")
    floor = Verdict.ask("client.ask", "confirm", floor=True)
    raised = verdict.raised_to(floor)
    assert raised.reason == "unclear"
    assert raised.model == "qwen-4b"


def test_raised_to_a_softer_verdict_is_raised_to_the_floor():
    verdict = Verdict.allow("stage2.allow")
    floor = Verdict.ask("client.ask", "confirm", floor=True)
    raised = verdict.raised_to(floor)
    assert raised.decision is DecisionKind.ask
    assert raised.rule_id == "client.ask"


def test_raised_to_a_failed_verdict_keeps_its_own_identity():
    verdict = Verdict.ask("classifier.unavailable", "timeout", stage=2)
    failed = dataclasses.replace(verdict, error="timeout")
    floor = Verdict.ask("client.ask", "confirm", floor=True)
    raised = failed.raised_to(floor)
    assert raised.rule_id == "classifier.unavailable"
    assert raised.error == "timeout"
