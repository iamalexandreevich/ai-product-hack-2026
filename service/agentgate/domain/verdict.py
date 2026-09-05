"""The single outcome type of the cascade.

Stage 1 rules, the stage 2 classifier, the allow cache and the API's own
early refusals all answer the same question -- what happens to this
action -- so they answer it with one type.

`hard` marks a verdict no later step may replace: escalation refuses to
touch it, and stage 2 is never reached past it.
"""

from dataclasses import dataclass, replace

from agentgate.api.schemas import Cost, DecisionKind


@dataclass(frozen=True)
class Verdict:
    decision: DecisionKind
    stage: int
    rule_id: str | None = None
    reason: str = ""
    suggest: str = ""
    hard: bool = False
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
    def ask(cls, rule_id: str, reason: str, suggest: str = "", *, stage: int = 1) -> "Verdict":
        return cls(
            decision=DecisionKind.ask, stage=stage, rule_id=rule_id,
            reason=reason, suggest=suggest,
        )

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
