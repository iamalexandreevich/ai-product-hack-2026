"""The single outcome type of the cascade.

Stage 1 rules, the stage 2 classifier, the allow cache and the API's own
early refusals all answer the same question -- what happens to this
action -- so they answer it with one type.

`hard` marks a verdict no later step may replace: escalation refuses to
touch it, and stage 2 is never reached past it.

`floor` marks a verdict that is not a decision but a lower bound on
strictness for the rest of the chain: the chain records it and keeps
going. Only `ask` is ever produced as a floor -- a floor that could deny
would be a decision wearing a disguise.

`escalatable` says whether escalation may replace this verdict with an
ask. False for a hard verdict and for the user's own `client.deny`:
turning a user's "no" into "ask me" is not a softening the service is
entitled to.

`raised_to` is the floor algebra for a verdict that already exists --
a stage-2 classifier's answer, checked against the floor stage 1 left
behind. It is deliberately asymmetric at a tie with `ChainOutcome.settled`
(`rules/base.py`), which applies the floor to stage 1's own outcome: a
stage-1 verdict exactly as strict as the floor keeps its own `rule_id`
(it already settled the question stage 1 was asked), while a stage-2
verdict exactly as strict as the floor is relabelled to the floor's
`rule_id` (spec v3.1 §3.3: the user should see whose rule actually held,
even though the model's `reason`, `model`, latency and cost are kept).
Only a verdict strictly stricter than the floor is left alone by
`raised_to`.
"""

from dataclasses import dataclass, replace

from agentgate.api.schemas import Cost, DecisionKind

# The total order of strictness (spec v3.1 §3.1). Compared, never stored.
_STRICTNESS: dict[DecisionKind, int] = {
    DecisionKind.allow: 0,
    DecisionKind.ask: 1,
    DecisionKind.deny: 2,
}

# Verdicts escalation must not touch, by rule_id. `hard` covers hard-deny;
# the user's own denial is not hard, but is just as much theirs to keep.
_NOT_ESCALATABLE = frozenset({"client.deny"})


@dataclass(frozen=True)
class Verdict:
    decision: DecisionKind
    stage: int
    rule_id: str | None = None
    reason: str = ""
    suggest: str = ""
    hard: bool = False
    floor: bool = False
    model: str | None = None
    raw_response: dict | None = None
    error: str | None = None
    cost: Cost | None = None

    @classmethod
    def allow(cls, rule_id: str, *, stage: int = 1) -> "Verdict":
        return cls(decision=DecisionKind.allow, stage=stage, rule_id=rule_id)

    @classmethod
    def deny(
        cls, rule_id: str, reason: str, suggest: str = "", *, stage: int = 1, hard: bool = False
    ) -> "Verdict":
        return cls(
            decision=DecisionKind.deny, stage=stage, rule_id=rule_id,
            reason=reason, suggest=suggest, hard=hard,
        )

    @classmethod
    def ask(
        cls, rule_id: str, reason: str, suggest: str = "", *, stage: int = 1, floor: bool = False
    ) -> "Verdict":
        return cls(
            decision=DecisionKind.ask, stage=stage, rule_id=rule_id,
            reason=reason, suggest=suggest, floor=floor,
        )

    @property
    def strictness(self) -> int:
        """Where this verdict sits in the total order allow < ask < deny."""
        return _STRICTNESS[self.decision]

    @property
    def escalatable(self) -> bool:
        """Whether escalation may replace this verdict with an ask."""
        return not self.hard and self.rule_id not in _NOT_ESCALATABLE

    def raised_to(self, floor: "Verdict | None") -> "Verdict":
        """This verdict, or the floor's decision and rule_id if this
        verdict did not settle at least as strict on its own.

        Unchanged when there is no floor, when this verdict failed closed
        (`error` is set -- it is already an `ask` and relabelling it would
        hide that the classifier never answered), or when it is strictly
        stricter than the floor. Otherwise only `decision` and `rule_id`
        move to the floor's: `reason`, `model`, and `cost` belong to the
        call that was made and paid for, not to the rule that bounds it.
        """
        if floor is None or self.error is not None or self.strictness > floor.strictness:
            return self
        return replace(self, decision=floor.decision, rule_id=floor.rule_id)

    def escalated(self, hits: int) -> "Verdict":
        """The verdict this one becomes when the session has hit the policy
        `hits` times in a row and a human should look at the task.

        Callers must not apply this to a hard verdict -- hard-deny is never
        replaced by an ask.
        """
        return replace(
            self,
            decision=DecisionKind.ask,
            rule_id="escalation",
            reason=f"agent hit the policy {hits} times; a human should review the task",
            suggest="",
        )
