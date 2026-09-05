"""Data model for a normalized action.

A NormalizedAction is the only representation later stages (profile
matching, LLM prompting, caching) are allowed to reason about — never the
raw command string. See service/CLAUDE.md: "Решение по сырой строке
команды запрещено везде; только по NormalizedAction."

Every part of it is frozen: action_hash() is an allow-cache key, so an
action that can be rewritten after the hash was taken is a cache that can
answer for a command it never saw. The action is built once, complete,
and never edited afterwards.

`method` is the HTTP method of a `network` action, already validated
against a closed set by the schema. A shell command's method lives in
its argv and is read there by the rule -- there is no second source.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field

from agentgate.api.schemas import McpArgs, Tool


@dataclass(frozen=True)
class Redirect:
    op: str
    target: str


@dataclass(frozen=True)
class SimpleCommand:
    argv: list[str]
    redirects: list[Redirect] = field(default_factory=list)
    stdin_from: str | None = None
    pipeline_id: int = 0
    # Literal text of every heredoc (<<, <<-) or here-string (<<<) body
    # attached to this command, whether or not it was ever parsed as
    # code (see shell.py's Flags.has_heredoc). Included unconditionally
    # so action_hash() cannot collide between two actions that differ
    # only in heredoc content — a cached allow for a benign body must
    # not replay for a destructive one just because we don't structurally
    # understand the body.
    heredoc_bodies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Flags:
    unparseable: bool = False
    has_eval: bool = False
    has_subst: bool = False
    has_env_assign: bool = False
    has_heredoc: bool = False
    has_unresolved_expansion: bool = False


@dataclass(frozen=True)
class NormalizedAction:
    tool: Tool
    cwd: str
    raw: str
    commands: list[SimpleCommand] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    flags: Flags = field(default_factory=Flags)
    mcp: McpArgs | None = None
    method: str | None = None

    def executables(self) -> list[str]:
        return [c.argv[0] for c in self.commands if c.argv]

    @property
    def mcp_name(self) -> str | None:
        """`server.tool` of an MCP call (`github.get_issue`), or None for anything else."""
        if self.tool is not Tool.mcp_call or self.mcp is None:
            return None
        return f"{self.mcp.server}.{self.mcp.tool}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["tool"] = self.tool.value
        data["mcp"] = self.mcp.model_dump() if self.mcp else None
        return data

    def action_hash(self) -> str:
        data = self.to_dict()
        data.pop("raw", None)
        payload = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
