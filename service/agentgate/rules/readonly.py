"""Readonly and path-safety checks shared by the allowlist and the
trusted-domain rule.

Both `AllowlistRule` and `ProfileDomainTrustedRule` ask the same three
questions of a command: is it read-only, does it match one of the
operator's safe prefixes, and do its paths stay inside the workspace and
away from protected files. One module answers all three, so the two
rules cannot drift on what "read-only" or "safe" means.
"""

from collections.abc import Iterable

from agentgate.domain.policy import Policy

from agentgate.normalize.model import SimpleCommand
from agentgate.normalize.paths import is_within, matches_any
from agentgate.shell.commands import Role, commands_with_role, spec_for
from agentgate.shell.paths import writes_a_file

READONLY = commands_with_role(Role.READONLY)


def is_readonly(cmd: SimpleCommand) -> bool:
    exe = cmd.argv[0]
    if writes_a_file(cmd):
        return False
    if exe in READONLY:
        return True
    if len(cmd.argv) > 1 and cmd.argv[1] in spec_for(exe).readonly_subcommands:
        return True
    if exe == "echo":
        return True
    if exe == "env" and len(cmd.argv) == 1:
        return True
    if exe == "find" and not {"-delete", "-exec", "-execdir", "-ok"} & set(cmd.argv):
        return True
    return False


def matches_prefix(cmd: SimpleCommand, prefixes: list[list[str]]) -> bool:
    return any(cmd.argv[: len(p)] == p for p in prefixes if p)


def paths_are_safe(paths: Iterable[str], policy: Policy) -> bool:
    """Every path is inside `allowed_paths` and none is `protected_paths`.

    An empty iterable is vacuously safe -- there is nothing to have
    checked. A caller for whom "no paths" must not count as safe (a
    file_read/file_write call with nothing declared) checks that itself.
    """
    return all(
        is_within(p, policy.allowed_paths)
        and not matches_any(p, policy.protected_paths, policy.workspace)
        for p in paths
    )
