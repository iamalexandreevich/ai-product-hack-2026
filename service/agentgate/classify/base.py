"""Stage 2: what the classifier is, from the engine's point of view.

`classify` never raises. Every failure -- a timeout, a malformed reply, a
bug in the client -- comes back as an `ask` verdict carrying `error`, so
`allow` on a broken classifier is not expressible.
"""

from typing import Protocol

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


class Classifier(Protocol):
    name: str

    async def classify(
        self, action: NormalizedAction, user_request: str, policy: Policy, stage1_note: str
    ) -> Verdict: ...
