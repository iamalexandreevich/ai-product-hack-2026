"""Stage 2: what the classifier is, from the engine's point of view.

`classify` never raises. Every failure -- a timeout, a malformed reply, a
bug in the client -- comes back as an `ask` verdict carrying `error`, so
`allow` on a broken classifier is not expressible.

`ReviewCase` is everything the classifier is asked about. It is built in
one place so the two rules about its contents live there: the dialogue is
fitted to the policy budget, and an empty `user_request` falls back to the
last turn the human actually wrote.
"""

from dataclasses import dataclass
from typing import Protocol

from agentgate.domain.dialogue import Dialogue
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


@dataclass(frozen=True)
class ReviewCase:
    action: NormalizedAction
    intent: str
    dialogue: Dialogue
    policy: Policy
    stage1_note: str

    @classmethod
    def build(
        cls, action: NormalizedAction, user_request: str, dialogue: Dialogue,
        policy: Policy, stage1_note: str,
    ) -> "ReviewCase":
        intent = user_request or dialogue.last_human_request() or ""
        return cls(
            action=action, intent=intent, dialogue=dialogue.fit(policy.history),
            policy=policy, stage1_note=stage1_note,
        )


class Classifier(Protocol):
    name: str

    async def classify(self, case: ReviewCase) -> Verdict: ...
