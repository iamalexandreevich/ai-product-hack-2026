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
reports `ambiguous.wrapper-depth` or `ambiguous.wrapper-opaque`. The two
can even disagree about family: GitForceRule is `hard-deny.git-force`
with `hard = True`, yet emits `ambiguous.git-force` when it recognizes a
force push whose target it cannot pin down. Match on `rule_id` to
identify an outcome, on `id` to identify a rule.

A rule may also answer with a floor -- a verdict carrying `floor=True`.
That is not a decision: the chain records the first one it is given and
keeps running, so a floor can never switch off a stricter rule below it.
`run` returns both halves; `evaluate` is the projection for callers that
only care what the chain decided.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


class Rule(Protocol):
    id: str
    hard: bool

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None: ...


@dataclass(frozen=True)
class ChainOutcome:
    """What one pass of the chain produced: at most one decision, and at
    most one lower bound on strictness for whatever decides next."""

    verdict: Verdict | None = None
    floor: Verdict | None = None

    def settled(self) -> Verdict | None:
        """What stage 1 answers once its own floor is taken into account.

        A stage-1 verdict at least as strict as the floor stands as it
        is -- that covers every denial, all of which sit above the floor
        in the chain, and a tie keeps the rule that actually answered.
        An `allow` under a floor becomes the floor itself, settled at
        stage 1 with no model call: the deterministic layer has already
        proved the action safe, so nothing stricter than `ask` could
        honestly come back from stage 2.
        """
        if self.verdict is None:
            return None
        if self.floor is None or self.verdict.strictness >= self.floor.strictness:
            return self.verdict
        return replace(self.floor, floor=False)


class RuleChain:
    """Runs rules in order and returns the first verdict; the order the
    chain is built in is the whole of stage 1's priority policy.
    """

    def __init__(self, rules: Sequence[Rule]) -> None:
        self._rules = tuple(rules)

    def run(self, action: NormalizedAction, policy: Policy) -> ChainOutcome:
        floor: Verdict | None = None
        for rule in self._rules:
            verdict = rule.evaluate(action, policy)
            if verdict is None:
                continue
            if verdict.floor:
                # The first floor is the strictest by position, so a later
                # one never replaces it.
                floor = floor if floor is not None else verdict
                continue
            return ChainOutcome(verdict=verdict, floor=floor)
        return ChainOutcome(verdict=None, floor=floor)

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        return self.run(action, policy).verdict
