"""Slot for the slopsquatting / package module. Always silent in v1."""

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


class PackagesRule:
    id = "packages"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        return None
