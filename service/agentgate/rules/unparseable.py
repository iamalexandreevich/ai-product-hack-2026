"""An action bashlex could not structurally parse.

`commands`, `paths` and `domains` are empty by construction for such an
action, so no later rule can have looked at anything real, and the
classifier would be answering about a command it never saw. First in the
chain, so both facts stay true.
"""

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


class UnparseableRule:
    id = "unparseable"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        if not action.flags.unparseable:
            return None
        return Verdict.ask(
            self.id,
            "action could not be structurally parsed and was never verified",
        )
