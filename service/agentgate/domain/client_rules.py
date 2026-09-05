"""The user's own deterministic policy, as stage 1 sees it.

A pattern's kind is read off its shape: one that contains `/` or starts
with `~` is a path pattern and is matched against normalized paths; every
other pattern is a command pattern and is matched against the canonical
form of a command. Matching is fnmatch, case-sensitive, and `*` crosses
`/` -- so `**/.env` and `*/.env` mean the same thing, which is what the
adapters' install levels rely on.

The digest ignores order and `level`: two rule sets that permit and forbid
the same things are the same policy.
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

    @classmethod
    def of(cls, rules: RuleSet | None) -> "ClientRules | None":
        if rules is None:
            return None
        return cls(
            level=rules.level,
            allow=tuple(_expand(p) for p in rules.allow),
            ask=tuple(_expand(p) for p in rules.ask),
            deny=tuple(_expand(p) for p in rules.deny),
        )

    def path_patterns(self, kind: Kind) -> tuple[str, ...]:
        return tuple(p for p in getattr(self, kind) if is_path_pattern(p))

    def command_patterns(self, kind: Kind) -> tuple[str, ...]:
        return tuple(p for p in getattr(self, kind) if not is_path_pattern(p))

    def matches_path(self, kind: Kind, path: str) -> bool:
        return any(fnmatch.fnmatchcase(path, p) for p in self.path_patterns(kind))

    def matches_command(self, kind: Kind, canonical: str) -> bool:
        return any(fnmatch.fnmatchcase(canonical, p) for p in self.command_patterns(kind))

    def digest(self) -> str:
        payload = json.dumps(
            {"allow": sorted(self.allow), "ask": sorted(self.ask), "deny": sorted(self.deny)},
            ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


def _expand(pattern: str) -> str:
    return os.path.expanduser(pattern) if pattern.startswith("~") else pattern
