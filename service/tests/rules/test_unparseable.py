from agentgate.api.schemas import DecisionKind
from agentgate.rules.unparseable import UnparseableRule
from tests.factories import profile, shell_action, unparseable_action


def test_says_nothing_about_a_command_it_could_parse():
    assert UnparseableRule().evaluate(shell_action("ls -la"), profile()) is None


def test_asks_about_a_command_it_could_not_parse():
    verdict = UnparseableRule().evaluate(unparseable_action(), profile())
    assert verdict.decision is DecisionKind.ask


def test_the_ask_is_stage_one_and_names_no_model():
    verdict = UnparseableRule().evaluate(unparseable_action(), profile())
    assert verdict.stage == 1 and verdict.model is None


def test_the_ask_is_not_hard():
    assert UnparseableRule().evaluate(unparseable_action(), profile()).hard is False
