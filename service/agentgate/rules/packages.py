"""Slot for the slopsquatting / package module. Always silent in v1."""

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile


class PackagesRule:
    id = "packages"
    hard = False

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        return None
