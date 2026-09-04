"""Which paths an argv references, given what the command table says it is.

Separate from the table itself so the table stays a leaf: the normalizer
imports the table, and anything importing `agentgate.normalize.paths` at
module level runs the normalize package's own eager init, which imports
the normalizer straight back.
"""

from collections.abc import Sequence
from enum import Enum, auto

from agentgate.normalize.paths import resolve_path
from agentgate.shell.argv import ParsedArgv
from agentgate.shell.commands import WriteTarget, spec_for

# Options asking a command to rewrite its input files in place. Prefix
# matched: sed's -i takes an optional attached backup suffix ("-i.bak").
_IN_PLACE_FLAGS: tuple[str, ...] = ("-i", "--in-place")


class PathRole(Enum):
    """Which paths of a command a caller is asking about."""

    # Every argument that could name a path. Over-includes on purpose:
    # its callers only ever use the answer to fall through to a less
    # permissive outcome, where over-including costs an unnecessary
    # stage-2 review and under-including costs a missed target.
    ANY = auto()
    # The arguments the command writes to, per its WriteTarget.
    WRITE = auto()
    # The arguments the command writes WITHOUT reading. An in-place edit
    # is excluded: sed -i both reads and writes its file, and calling it
    # write-only would hide a read a caller must still account for.
    WRITE_ONLY = auto()


def command_paths(argv: Sequence[str], cwd: str, role: PathRole) -> tuple[str, ...]:
    """Paths this argv references in the given role, resolved against ``cwd``.

    ANY and the two write roles are different questions and their answers
    must not drift: the allowlist's protected-path guard asks the first,
    the protected-write rule the second, and a command answering them
    inconsistently was already a bug once.

    Deliberately does not consult NormalizedAction.paths, which the
    normalizer fills from a narrower rule than "every non-flag argument":
    a readonly command whose arguments are not declared paths, reading a
    bare-name protected file, would leave it empty.
    """
    if role is PathRole.ANY:
        return tuple(resolve_path(a, cwd) for a in argv[1:] if not a.startswith("-"))
    return tuple(resolve_path(a, cwd) for a in _written_positionals(argv, role))


def _written_positionals(argv: Sequence[str], role: PathRole) -> tuple[str, ...]:
    """The positionals this argv writes to, before resolution.

    A LAST_POSITIONAL command needs at least a source and a destination
    to have named a destination at all; with fewer, nothing here is
    claimed to be written.
    """
    spec = spec_for(argv[0] if argv else "")
    positionals = ParsedArgv.of(argv).positionals
    if spec.write_target is WriteTarget.EVERY_POSITIONAL:
        return positionals
    if spec.write_target is WriteTarget.LAST_POSITIONAL:
        return positionals[-1:] if len(positionals) >= 2 else ()
    if spec.write_target is WriteTarget.POSITIONALS_AFTER_FIRST:
        if role is PathRole.WRITE_ONLY or not _edits_in_place(argv):
            return ()
        return positionals[1:]
    return ()


def _edits_in_place(argv: Sequence[str]) -> bool:
    """True if this argv asks the command to rewrite its inputs in place."""
    return any(a.startswith(_IN_PLACE_FLAGS) for a in argv[1:])
