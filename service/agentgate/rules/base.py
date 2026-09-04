"""What a stage 1 rule is, and the one loop that runs them.

A rule answers about one thing and answers two ways: a Verdict that
settles the action, or None meaning "nothing I know about applies".
None never means "I could not tell" -- a rule that recognizes danger it
cannot pin down returns an ask instead, so silence is never mistaken for
safety.

`hard` on the rule declares the strength of the denials it produces:
hard-deny is final and no later step may replace it.

`id` names the rule; the `rule_id` on a verdict may be narrower, naming
which of the rule's cases fired -- AllowlistRule reports
`allowlist.readonly` or `allowlist.prefix`, WrapperUnresolvedRule
reports `ambiguous.wrapper-depth` or `ambiguous.wrapper-opaque`. Match
on `rule_id` to identify an outcome, on `id` to identify a rule.
"""

from collections.abc import Sequence
from typing import Protocol

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile


class Rule(Protocol):
    id: str
    hard: bool

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None: ...


class RuleChain:
    """Runs rules in order and returns the first verdict; the order the
    chain is built in is the whole of stage 1's priority policy.
    """

    def __init__(self, rules: Sequence[Rule]) -> None:
        self._rules = tuple(rules)

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        for rule in self._rules:
            verdict = rule.evaluate(action, profile)
            if verdict is not None:
                return verdict
        return None
