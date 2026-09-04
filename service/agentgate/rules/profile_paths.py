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
from agentgate.normalize.model import NormalizedAction, SimpleCommand
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
        targets += _command_targets(command, action.cwd)
        for redirect in command.redirects:
            if redirect.op.endswith((">", ">>")) and not redirect.target.startswith("/dev/"):
                targets.append(redirect.target)
    if action.tool is Tool.file_write:
        targets += action.paths
    return targets


def _command_targets(command: SimpleCommand, cwd: str) -> tuple[str, ...]:
    """What one command has to keep inside the allowed paths.

    Every argument of a mutating command counts, not only its destination:
    `cp /etc/shadow ./x` reaches outside the workspace through its source.
    Anything else contributes only what it writes, which is how an
    in-place edit reaches here without its substitution script being
    mistaken for a file.
    """
    if command.argv[0] in _MUTATING:
        return command_paths(command.argv, cwd, PathRole.ANY)
    return command_paths(command.argv, cwd, PathRole.WRITE)
