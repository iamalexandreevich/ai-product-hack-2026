"""Slot for the slopsquatting / package module. Always passes in v1."""
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.stage1.types import Stage1Decision


def check_packages(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    return None
