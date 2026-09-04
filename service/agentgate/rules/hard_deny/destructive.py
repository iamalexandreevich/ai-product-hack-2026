"""Deleting what the agent was not given to delete.

Fires on a target outside the profile's allowed paths, and on a target
that IS the workspace root -- "within the allowed paths" is true of the
root itself, and wiping it is exactly the outcome the allowed paths exist
to prevent.

A token holding an unresolved expansion is resolved like any other here,
rather than skipped: the properties this rule reads survive fabrication
either way. `rm -rf $HOME/../..` escapes the workspace by at least one
level whatever $HOME turns out to be, because ".." collapses the same way
syntactically. The checks below only ever produce a deny or no signal --
never an explicit "this is safe" -- so a fabricated path can add a denial
it has earned but can never excuse one.
"""

import os
from dataclasses import dataclass

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.normalize.paths import is_within, resolve_path
from agentgate.profiles.schema import Profile
from agentgate.rules.hard_deny.shared import effective_argv

# find predicates that narrow -delete to a specific set of PATHS, as
# opposed to "every entry under the search root". -type/-size/-mtime/-newer
# are deliberately absent: they bound the file's TYPE or METADATA, not the
# path set, so "find . -type f -delete" at the workspace root still deletes
# every file there.
_NARROWING_PREDICATES = {"-name", "-iname", "-path", "-ipath", "-regex", "-iregex"}
# Every narrowing predicate takes a glob/regex PATTERN value, which can
# itself be trivially universal — these match everything just as
# thoroughly as no predicate at all.
_TRIVIAL_PREDICATE_VALUES = {"*", "**", ".*", ".**"}


@dataclass(frozen=True)
class _Deletion:
    """What one command deletes, and whether hitting the workspace root
    exactly is itself a denial.
    """

    targets: tuple[str, ...]
    deny_on_workspace_equal: bool


class DestructiveRule:
    id = "hard-deny.destructive"
    hard = True

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        allowed = profile.resolved_allowed_paths()
        workspace = os.path.normpath(profile.workspace) if profile.workspace else None
        for command in action.commands:
            argv = effective_argv(command.argv)
            deletion = _deletion(argv, action.cwd)
            if deletion is None:
                continue
            for target in deletion.targets:
                equals_workspace = (
                    deletion.deny_on_workspace_equal
                    and workspace is not None
                    and os.path.normpath(target) == workspace
                )
                if not is_within(target, allowed) or equals_workspace:
                    return Verdict.deny(
                        self.id, f"'{argv[0]}' targets {target} outside or equal to the workspace",
                        "Delete only build artifacts inside the workspace", hard=True,
                    )
        return None


def _deletion(argv: list[str], cwd: str) -> _Deletion | None:
    """What ``argv`` deletes, or None if it deletes nothing.

    Only rm and shred unconditionally destroy the exact paths they are
    given, so only those two also deny on the workspace root itself.
    `find <root> ... -delete` is different: <root> houses many files and
    -delete removes only the entries matching the predicate, so
    "find . -name '*.pyc' -delete" is ordinary cleanup even at the
    workspace root. With no narrowing predicate at all it removes
    everything under the root, which is `rm -rf <workspace>` by another
    name — hence the flag rather than a uniform equality check. A root
    reaching outside the allowed paths is denied either way.
    """
    if not argv:
        return None
    exe = argv[0]
    if exe == "rm" and any(a.startswith("-") and ("r" in a or "R" in a) for a in argv[1:]):
        return _Deletion(_positional_paths(argv, cwd), True)
    if exe == "shred":
        return _Deletion(_positional_paths(argv, cwd), True)
    if exe == "find" and "-delete" in argv:
        rest = argv[1:]
        # find's search root, if given, is the first positional (paths
        # always precede predicates); if the first token after "find" is
        # itself a predicate/flag, no path was given and find defaults to ".".
        root_token = rest[0] if rest and not rest[0].startswith("-") else None
        root = resolve_path(root_token, cwd) if root_token is not None else os.path.normpath(cwd)
        return _Deletion((root,), not _has_narrowing_predicate(rest))
    return None


def _positional_paths(argv: list[str], cwd: str) -> tuple[str, ...]:
    return tuple(resolve_path(a, cwd) for a in argv[1:] if not a.startswith("-"))


def _has_narrowing_predicate(rest: list[str]) -> bool:
    for i, tok in enumerate(rest):
        if tok not in _NARROWING_PREDICATES:
            continue
        value = rest[i + 1] if i + 1 < len(rest) else None
        if value is not None and value not in _TRIVIAL_PREDICATE_VALUES:
            return True
    return False
