"""A profile bound to one workspace: what the rules and the prompt see.

`Profile` is operator configuration and knows nothing about any request.
`Policy` is that configuration resolved against the workspace of one
session -- placeholders expanded, paths normalized, the hash taken once.
Resolving on every call was both repeated work and the reason a later
`cwd` could silently widen the sandbox. The user's own rules bind to the
policy the same way the workspace does, so stage 1 sees only an action
and a policy.
"""

import os
from dataclasses import dataclass

from agentgate.domain.client_rules import ClientRules
from agentgate.profiles.schema import Escalation, History, Inspect, Network, Profile, Prose


@dataclass(frozen=True)
class Policy:
    profile: Profile
    workspace: str
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    profile_hash: str
    client_rules: ClientRules | None = None

    @classmethod
    def bind(
        cls, profile: Profile, workspace: str, client_rules: ClientRules | None = None
    ) -> "Policy":
        return cls(
            profile=profile,
            workspace=workspace,
            allowed_paths=tuple(
                os.path.normpath(_expand(path, workspace)) for path in profile.allowed_paths
            ),
            protected_paths=tuple(_expand(path, workspace) for path in profile.protected_paths),
            profile_hash=profile.profile_hash(),
            client_rules=client_rules,
        )

    @property
    def id(self) -> str:
        return self.profile.id

    @property
    def network(self) -> Network:
        return self.profile.network

    @property
    def protected_branches(self) -> list[str]:
        return self.profile.protected_branches

    @property
    def safe_prefixes(self) -> list[list[str]]:
        return self.profile.safe_prefixes

    @property
    def escalation(self) -> Escalation:
        return self.profile.escalation

    @property
    def prose(self) -> Prose:
        return self.profile.prose

    @property
    def history(self) -> History:
        return self.profile.history

    @property
    def inspect(self) -> Inspect:
        return self.profile.inspect


def _expand(path: str, workspace: str) -> str:
    return os.path.expanduser(path.replace("${WORKSPACE}", workspace))
