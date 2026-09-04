from agentgate.domain.verdict import Verdict
from agentgate.rules.base import RuleChain


class StaticRule:
    """A rule that answers the same thing every time, for chain tests."""

    def __init__(self, rule_id: str, verdict: Verdict | None, hard: bool = False) -> None:
        self.id = rule_id
        self.hard = hard
        self._verdict = verdict
        self.calls = 0

    def evaluate(self, action, profile):
        self.calls += 1
        return self._verdict


def test_empty_chain_says_nothing():
    assert RuleChain([]).evaluate(None, None) is None


def test_chain_returns_the_first_verdict():
    chain = RuleChain([
        StaticRule("a", None),
        StaticRule("b", Verdict.deny("b", "no")),
        StaticRule("c", Verdict.allow("c")),
    ])
    assert chain.evaluate(None, None).rule_id == "b"


def test_chain_stops_at_the_first_verdict():
    later = StaticRule("c", Verdict.allow("c"))
    RuleChain([StaticRule("b", Verdict.deny("b", "no")), later]).evaluate(None, None)
    assert later.calls == 0


def test_chain_says_nothing_when_every_rule_is_silent():
    assert RuleChain([StaticRule("a", None), StaticRule("b", None)]).evaluate(None, None) is None


def test_chain_asks_every_rule_until_one_answers():
    first, second = StaticRule("a", None), StaticRule("b", None)
    RuleChain([first, second]).evaluate(None, None)
    assert (first.calls, second.calls) == (1, 1)
