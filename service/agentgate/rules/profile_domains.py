"""Domains outside the profile's network allowlist.

Denied in modes off/allowlist, escalated to ask in mode ask, passed
through in mode open. A subdomain of an allowed domain is allowed.

The reason text carries a domain taken from the action and must be
escaped where it reaches the stage-2 prompt.
"""

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import NetworkMode, Profile


class ProfileDomainRule:
    id = "profile.domain"
    hard = False

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        if not action.domains or profile.network.mode is NetworkMode.open:
            return None
        allowed = {d.lower() for d in profile.network.allowed_domains}
        for domain in action.domains:
            if domain in allowed or any(domain.endswith("." + a) for a in allowed):
                continue
            if profile.network.mode is NetworkMode.ask:
                return Verdict.ask(self.id, f"domain {domain} is not in the allowlist")
            return Verdict.deny(
                self.id, f"domain {domain} is not in the allowlist",
                "Use an allowed registry or ask the user to extend the allowlist",
            )
        return None
