"""Mutating filesystem targets must resolve inside the profile's allowed
paths.

Reading outside the workspace (`cat /etc/hosts`) is deliberately not
denied here: path denial applies to mutating commands and file_write
only, everything else falls through to the classifier.

The reason text carries a resolved path, which is action-derived and
therefore attacker-influenced; it reaches the stage-2 prompt as the
stage 1 note and must be escaped there like any other such content.
"""

from agentgate.api.schemas import Tool
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.normalize.paths import is_within
from agentgate.shell.commands import Role, commands_with_role
from agentgate.shell.paths import PathRole, command_paths

_MUTATING = commands_with_role(Role.MUTATING)


class ProfilePathRule:
    id = "profile.path"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        for path in _mutating_targets(action):
            if not is_within(path, policy.allowed_paths):
                return Verdict.deny(
                    self.id, f"write outside allowed paths: {path}", "Work inside the workspace"
                )
        return None


def _mutating_targets(action: NormalizedAction) -> list[str]:
    targets: list[str] = []
    for command in action.commands:
        exe = command.argv[0]
        referenced = command_paths(command.argv, action.cwd, PathRole.ANY)
        if exe in _MUTATING:
            targets += referenced
        elif exe == "sed" and any(a.startswith("-i") for a in command.argv[1:]):
            # sed's first non-flag argument is the substitution script, not
            # a target. The ANY role already dropped the flags.
            targets += referenced[1:]
        for redirect in command.redirects:
            if redirect.op.endswith((">", ">>")) and not redirect.target.startswith("/dev/"):
                targets.append(redirect.target)
    if action.tool is Tool.file_write:
        targets += action.paths
    return targets
