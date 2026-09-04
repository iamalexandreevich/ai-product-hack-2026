from agentgate.classify.base import ReviewCase
from agentgate.domain.dialogue import Dialogue
from tests.factories import dialogue, policy, shell_action, turn


def test_build_fits_the_dialogue_to_the_policy_budget():
    d = dialogue(*[turn(role="toolresult", author="system", content="r" * 300) for _ in range(20)])
    case = ReviewCase.build(shell_action("ls"), "task", d, policy(history={"budget_chars": 100, "per_turn_chars": {"toolresult": 30}}), "note")
    assert sum(len(t.content) for t in case.dialogue.turns) <= 100 and case.dialogue.omitted == 17


def test_build_keeps_user_request_as_intent_when_present():
    d = dialogue(turn(content="from history"))
    assert ReviewCase.build(shell_action("ls"), "explicit", d, policy(), "note").intent == "explicit"


def test_build_takes_intent_from_the_last_human_authored_turn_when_user_request_is_empty():
    d = dialogue(turn(content="older"), turn(content="newest human"), turn(role="human", author="agent", content="subagent"))
    assert ReviewCase.build(shell_action("ls"), "", d, policy(), "note").intent == "newest human"


def test_build_intent_stays_empty_without_a_human_authored_turn():
    d = dialogue(turn(role="human", author="agent", content="subagent"))
    assert ReviewCase.build(shell_action("ls"), "", d, policy(), "note").intent == ""
    assert ReviewCase.build(shell_action("ls"), "", Dialogue(), policy(), "note").intent == ""
