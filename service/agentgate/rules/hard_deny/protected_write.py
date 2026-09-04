"""Writing to a path the operator declared off-limits.

Only writes: reading a protected path is not this rule's business (the
allowlist declines to bless it, and stage 2 judges it). A token holding
an unresolved expansion is resolved like any other, because a protected
pattern matched on a token's own basename holds whatever the unresolved
segment before it expands to.
"""

from agentgate.api.schemas import Tool
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction, SimpleCommand
from agentgate.normalize.paths import matches_any, resolve_path
from agentgate.rules.hard_deny.shared import LAST_ARG_WRITE_COMMANDS, effective_argv


class ProtectedWriteRule:
    id = "hard-deny.protected-write"
    hard = True

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        for path in _write_targets(action):
            if matches_any(path, policy.protected_paths, policy.workspace):
                return Verdict.deny(
                    self.id, f"write to protected path {path}",
                    "Protected files are changed only by the user", hard=True,
                )
        return None


def _write_targets(action: NormalizedAction) -> list[str]:
    targets = list(action.paths) if action.tool is Tool.file_write else []
    for command in action.commands:
        argv = effective_argv(command.argv)
        if argv:
            targets += _command_write_targets(command, argv, action.cwd)
    return targets


def _command_write_targets(command: SimpleCommand, argv: list[str], cwd: str) -> list[str]:
    exe = argv[0]
    # Any output-direction redirect op: ">", ">>", the clobber form ">|",
    # "&>"/"2>" duplications onto a file. Matched by substring rather than
    # a suffix test so ">|" (bash's noclobber override) is not missed.
    targets = [r.target for r in command.redirects if ">" in r.op]
    args = [a for a in argv[1:] if not a.startswith("-")]
    if exe in LAST_ARG_WRITE_COMMANDS and len(args) >= 2:
        targets.append(resolve_path(args[-1], cwd))
    elif exe == "tee":
        targets += [resolve_path(a, cwd) for a in args]
    elif exe == "sed" and any(a.startswith(("-i", "--in-place")) for a in argv[1:]):
        # sed's first non-flag argument is the substitution script, not a file.
        targets += [resolve_path(a, cwd) for a in args[1:]]
    return targets
