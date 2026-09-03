"""Shared argv-to-path enumeration for stage 1 checks.

check_profile (mutating-target detection) and check_allowlist
(protected-path guard for readonly/safe-prefix commands) both need to
answer the same question — "which filesystem paths, if any, does this
command's argv reference" — and must not drift into two different
answers to it (fix round 2, task 6: check_allowlist's original
protected-path guard consulted only NormalizedAction.paths, which the
normalizer populates from a narrower rule — PATH_COMMANDS membership or
looks_like_path — than "every non-flag argument of this command". A
READONLY command outside PATH_COMMANDS (``sort``, ``cut``, ``diff``,
``uniq`` are all in allowlist.READONLY but none are in
normalize/shell.py's PATH_COMMANDS) reading a bare-name protected path
— no leading ``/``, no ``/`` at all, not a hard-coded sensitive
basename like ``.env`` — produced ``action.paths == []`` and slipped
through the guard as ``allow``. Reproduced against the shipped default
profile's bare-name protected entries: ``AGENTS.md``, ``SKILL.md``,
``.cursorrules``).

A single shared enumeration, used by both checks, closes this class of
gap for good instead of leaving two copies to drift apart again.
"""

from agentgate.normalize.model import SimpleCommand
from agentgate.normalize.paths import resolve_path


def command_argv_paths(cmd: SimpleCommand, cwd: str) -> list[str]:
    """Resolve every non-flag argv token of ``cmd`` (excluding argv[0],
    the executable name) against ``cwd``.

    Deliberately does not consult NormalizedAction.paths or
    PATH_COMMANDS — every non-flag argument is treated as a potential
    path target unconditionally. That is the conservative direction for
    a helper whose callers only ever use the result to fall through to
    a less permissive outcome (deny, in check_profile; None, in
    check_allowlist's protected-path guard) — over-including an
    argument that happens not to be a path costs at most an unnecessary
    stage-2 review or a path-shaped-string coincidence, never a missed
    dangerous target.
    """
    return [resolve_path(a, cwd) for a in cmd.argv[1:] if not a.startswith("-")]
