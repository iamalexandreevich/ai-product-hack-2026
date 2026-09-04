"""A profile bound to one workspace: what the rules and the prompt see.

`Profile` is operator configuration and knows nothing about any request.
`Policy` is that configuration resolved against the workspace of one
session -- placeholders expanded, paths normalized, the hash taken once.
Resolving on every call was both repeated work and the reason a later
`cwd` could silently widen the sandbox.
"""

import os
from dataclasses import dataclass

from agentgate.profiles.schema import Escalation, Network, Profile, Prose


@dataclass(frozen=True)
class Policy:
    profile: Profile
    workspace: str
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    profile_hash: str

    @classmethod
    def bind(cls, profile: Profile, workspace: str) -> "Policy":
        return cls(
            profile=profile,
            workspace=workspace,
            allowed_paths=tuple(
                os.path.normpath(_expand(path, workspace)) for path in profile.allowed_paths
            ),
            protected_paths=tuple(_expand(path, workspace) for path in profile.protected_paths),
            profile_hash=profile.profile_hash(),
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


def _expand(path: str, workspace: str) -> str:
    return os.path.expanduser(path.replace("${WORKSPACE}", workspace))
