"""Shared argv-to-path enumeration for stage 1 rules.

ProfilePathRule (mutating targets) and AllowlistRule (its protected-path
guard) both answer the same question -- "which filesystem paths does this
command's argv reference" -- and must not drift into two answers to it.
One enumeration, used by both.
"""

from agentgate.normalize.model import SimpleCommand
from agentgate.normalize.paths import resolve_path


def command_argv_paths(cmd: SimpleCommand, cwd: str) -> list[str]:
    """Resolve every non-flag argv token of ``cmd`` (excluding argv[0], the
    executable name) against ``cwd``.

    Deliberately does not consult NormalizedAction.paths, which the
    normalizer fills from a narrower rule than "every non-flag argument":
    a readonly command outside that rule reading a bare-name protected
    file would leave it empty. Every non-flag argument is treated as a
    potential path target unconditionally -- the conservative direction
    for a helper whose callers only ever use the result to fall through to
    a less permissive outcome, where over-including costs at most an
    unnecessary stage-2 review and under-including costs a missed target.
    """
    return [resolve_path(a, cwd) for a in cmd.argv[1:] if not a.startswith("-")]
