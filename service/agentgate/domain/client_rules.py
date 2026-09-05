"""The user's own deterministic policy, as stage 1 sees it.

A pattern's kind is read off its shape: one that contains `/` or starts
with `~` is a path pattern and is matched against normalized paths; every
other pattern is a command pattern and is matched against the canonical
form of a command. Path patterns match case-insensitively -- the
operator's `protected_paths` matcher in `normalize/paths.py` is
case-insensitive because macOS filesystems are, and a user `deny:
["**/.env"]` must not be weaker than the operator's own pattern on
`/repo/.ENV`. Command patterns stay case-sensitive, because shells are.
`*` crosses `/`, so `**/.env` and `*/.env` mean the same thing, which is
what the adapters' install levels rely on.

The digest ignores order, duplicates and `level`: two rule sets that
permit and forbid the same things are the same policy. It is computed
over the patterns as the user wrote them, before `~` expansion, so it
does not depend on `HOME`.
"""

import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Literal

from agentgate.api.schemas import RuleSet

Kind = Literal["allow", "ask", "deny"]


def is_path_pattern(pattern: str) -> bool:
    return "/" in pattern or pattern.startswith("~")


@dataclass(frozen=True)
class ClientRules:
    level: str
    allow: tuple[str, ...]
    ask: tuple[str, ...]
    deny: tuple[str, ...]
    _digest: str

    @classmethod
    def of(cls, rules: RuleSet | None) -> "ClientRules | None":
        if rules is None:
            return None
        digest = _digest_of(rules.allow, rules.ask, rules.deny)
        return cls(
            level=rules.level,
            allow=tuple(_prepare(p) for p in rules.allow),
            ask=tuple(_prepare(p) for p in rules.ask),
            deny=tuple(_prepare(p) for p in rules.deny),
            _digest=digest,
        )

    def _patterns(self, kind: Kind) -> tuple[str, ...]:
        return {"allow": self.allow, "ask": self.ask, "deny": self.deny}[kind]

    def path_patterns(self, kind: Kind) -> tuple[str, ...]:
        return tuple(p for p in self._patterns(kind) if is_path_pattern(p))

    def command_patterns(self, kind: Kind) -> tuple[str, ...]:
        return tuple(p for p in self._patterns(kind) if not is_path_pattern(p))

    def matches_path(self, kind: Kind, path: str) -> bool:
        folded_path = path.casefold()
        return any(fnmatch.fnmatchcase(folded_path, p) for p in self.path_patterns(kind))

    def matches_command(self, kind: Kind, canonical: str) -> bool:
        return any(fnmatch.fnmatchcase(canonical, p) for p in self.command_patterns(kind))

    def digest(self) -> str:
        return self._digest


def _digest_of(allow: list[str], ask: list[str], deny: list[str]) -> str:
    payload = json.dumps(
        {"allow": sorted(set(allow)), "ask": sorted(set(ask)), "deny": sorted(set(deny))},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


def _prepare(pattern: str) -> str:
    """Expand ``~`` and, for a path pattern, casefold it once here.

    Folding at construction time means `matches_path` never repeats the
    fnmatch case-fold work per call; command patterns stay untouched
    because shells are case-sensitive.
    """
    expanded = _expand(pattern)
    return expanded.casefold() if is_path_pattern(expanded) else expanded


def _expand(pattern: str) -> str:
    """Expand a leading ``~`` using ``HOME`` only.

    Mirrors ``normalize.paths.resolve_path``: resolving ``~user`` through
    the system user database can cost ~0.77ms per token, enough alone to
    blow the p50 <= 1ms stage 1 budget, so only a bare ``~`` or ``~/...``
    is expanded here. A ``~user`` pattern, or a bare ``~`` with no
    ``HOME`` set, is left literal.
    """
    if pattern != "~" and not pattern.startswith("~/"):
        return pattern
    home = os.environ.get("HOME")
    if not home:
        return pattern
    return home + pattern[1:]
