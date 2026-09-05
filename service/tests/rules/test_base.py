from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.rules.base import ChainOutcome, RuleChain


class StaticRule:
    """A rule that answers the same thing every time, for chain tests."""

    def __init__(self, rule_id: str, verdict: Verdict | None, hard: bool = False) -> None:
        self.id = rule_id
        self.hard = hard
        self._verdict = verdict
        self.calls = 0

    def evaluate(self, action, policy):
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


def floor_rule(rule_id: str = "client.ask") -> StaticRule:
    return StaticRule(rule_id, Verdict.ask(rule_id, "confirm", floor=True))


def test_run_returns_an_empty_outcome_for_an_empty_chain():
    outcome = RuleChain([]).run(None, None)
    assert outcome.verdict is None and outcome.floor is None


def test_a_floor_does_not_stop_the_chain():
    later = StaticRule("c", Verdict.allow("c"))
    outcome = RuleChain([floor_rule(), later]).run(None, None)
    assert later.calls == 1
    assert outcome.verdict.rule_id == "c" and outcome.floor.rule_id == "client.ask"


def test_the_first_floor_wins_over_a_later_one():
    outcome = RuleChain([floor_rule("client.ask"), floor_rule("second")]).run(None, None)
    assert outcome.floor.rule_id == "client.ask" and outcome.verdict is None


def test_a_floor_survives_a_chain_that_reaches_its_end():
    outcome = RuleChain([floor_rule(), StaticRule("c", None)]).run(None, None)
    assert outcome.verdict is None and outcome.floor.rule_id == "client.ask"


def test_an_ordinary_verdict_after_a_floor_stops_the_chain_and_keeps_the_floor():
    later = StaticRule("d", Verdict.allow("d"))
    outcome = RuleChain([floor_rule(), StaticRule("c", Verdict.deny("c", "no")), later]).run(None, None)
    assert outcome.verdict.rule_id == "c" and outcome.floor.rule_id == "client.ask"
    assert later.calls == 0


def test_evaluate_still_answers_with_the_verdict_alone():
    chain = RuleChain([floor_rule(), StaticRule("c", Verdict.deny("c", "no"))])
    assert chain.evaluate(None, None).rule_id == "c"


def test_evaluate_hides_a_floor_that_settled_nothing():
    assert RuleChain([floor_rule()]).evaluate(None, None) is None


def test_settled_is_none_when_the_chain_answered_nothing():
    assert ChainOutcome().settled() is None


def test_settled_is_none_when_only_a_floor_was_recorded():
    outcome = ChainOutcome(verdict=None, floor=Verdict.ask("client.ask", "confirm", floor=True))
    assert outcome.settled() is None


def test_settled_returns_the_verdict_when_there_is_no_floor():
    outcome = ChainOutcome(verdict=Verdict.deny("profile.path", "outside"))
    assert outcome.settled().rule_id == "profile.path"


def test_settled_returns_a_denial_unchanged_under_a_floor():
    outcome = ChainOutcome(
        verdict=Verdict.deny("profile.path", "outside"),
        floor=Verdict.ask("client.ask", "confirm", floor=True),
    )
    assert outcome.settled().rule_id == "profile.path"


def test_settled_keeps_a_tied_asks_own_rule_id():
    outcome = ChainOutcome(
        verdict=Verdict.ask("ambiguous.wrapper-depth", "cannot resolve"),
        floor=Verdict.ask("client.ask", "confirm", floor=True),
    )
    assert outcome.settled().rule_id == "ambiguous.wrapper-depth"


def test_settled_raises_an_allow_to_the_floor():
    outcome = ChainOutcome(
        verdict=Verdict.allow("allowlist.readonly"),
        floor=Verdict.ask("client.ask", "confirm", floor=True),
    )
    settled = outcome.settled()
    assert settled.decision is DecisionKind.ask
    assert settled.rule_id == "client.ask"


def test_settled_raised_allow_is_not_itself_a_floor():
    outcome = ChainOutcome(
        verdict=Verdict.allow("allowlist.readonly"),
        floor=Verdict.ask("client.ask", "confirm", floor=True),
    )
    assert outcome.settled().floor is False
