"""Slot for the slopsquatting / package module. Always passes in v1."""
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile


def check_packages(action: NormalizedAction, profile: Profile) -> Verdict | None:
    return None
