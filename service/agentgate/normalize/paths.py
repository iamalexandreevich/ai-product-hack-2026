"""Path resolution and matching helpers.

Pure string/filesystem-path manipulation — no filesystem or network I/O,
so this stays inside the normalize+stage1 latency budget (p50 <= 1ms).
"""

import fnmatch
import os

_URL_MARK = "://"


def resolve_path(token: str, cwd: str) -> str:
    """Resolve a shell token to an absolute, normalized path.

    ``~`` expands to the home directory; a relative token is joined onto
    ``cwd``. A wildcard tail (e.g. ``src/*.py``) is preserved as-is since
    normpath does not touch glob characters.
    """
    expanded = os.path.expanduser(token)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return os.path.normpath(expanded)


def looks_like_path(token: str) -> bool:
    """Heuristic: does this argv token look like a filesystem path?

    Used for arguments of commands not in PATH_COMMANDS, where we cannot
    assume every non-flag token is a path. Deliberately excludes URLs and
    flag-like tokens (leading ``-``).
    """
    if not token or token.startswith("-") or _URL_MARK in token:
        return False
    if token.startswith(("/", "./", "../", "~")) or token in (".", ".."):
        return True
    return "/" in token


def is_within(path: str, roots: list[str]) -> bool:
    """True if ``path`` is equal to or nested under one of ``roots``.

    Uses os.path.commonpath so it operates purely on path components, not
    string prefixes (avoids "/r" matching "/rx/a").
    """
    p = os.path.normpath(path)
    for root in roots:
        r = os.path.normpath(root)
        try:
            if os.path.commonpath([p, r]) == r:
                return True
        except ValueError:
            # commonpath raises when paths don't share a drive/anchor,
            # or when mixing absolute and relative paths; treat as no match.
            continue
    return False


def _glob_match(path: str, pattern: str) -> bool:
    # fnmatch treats '*' as matching '/', so '**/' prefix and '/**' suffix work naturally.
    if pattern.endswith("/**"):
        base = pattern[:-3]
        return fnmatch.fnmatchcase(path, base) or fnmatch.fnmatchcase(path, base + "/*")
    return fnmatch.fnmatchcase(path, pattern)


def matches_any(path: str, patterns: list[str], workspace: str | None) -> bool:
    """True if ``path`` matches any of ``patterns``.

    A pattern without ``/`` matches by basename (fnmatch). A pattern with
    ``/`` matches the absolute path, or the path relative to ``workspace``
    (including nested occurrences, so ".claude/**" also catches
    "sub/.claude/settings.json"). ``**`` matches any prefix of directories.
    ``~`` in a pattern is expanded to the user's home directory.
    """
    abs_path = os.path.normpath(path)
    rel_path = None
    if workspace and is_within(abs_path, [workspace]):
        rel_path = os.path.relpath(abs_path, workspace)
    for pattern in patterns:
        pat = os.path.expanduser(pattern)
        if "/" not in pat:
            if fnmatch.fnmatchcase(os.path.basename(abs_path), pat):
                return True
            continue
        if _glob_match(abs_path, pat):
            return True
        # relative to workspace, including nested occurrences (sub/.claude/settings.json)
        if rel_path is not None and (_glob_match(rel_path, pat) or _glob_match(rel_path, "**/" + pat)):
            return True
    return False
