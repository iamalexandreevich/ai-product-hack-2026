"""Data model for a normalized action.

A NormalizedAction is the only representation later stages (profile
matching, LLM prompting, caching) are allowed to reason about — never the
raw command string. See service/CLAUDE.md: "Решение по сырой строке
команды запрещено везде; только по NormalizedAction."
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field

from agentgate.api.schemas import McpArgs, Tool


@dataclass
class Redirect:
    op: str
    target: str


@dataclass
class SimpleCommand:
    argv: list[str]
    redirects: list[Redirect] = field(default_factory=list)
    stdin_from: str | None = None
    pipeline_id: int = 0


@dataclass
class Flags:
    unparseable: bool = False
    has_eval: bool = False
    has_subst: bool = False
    has_env_assign: bool = False
    has_heredoc: bool = False
    has_unresolved_expansion: bool = False


@dataclass
class NormalizedAction:
    tool: Tool
    cwd: str
    raw: str
    commands: list[SimpleCommand] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    flags: Flags = field(default_factory=Flags)
    mcp: McpArgs | None = None

    def executables(self) -> list[str]:
        return [c.argv[0] for c in self.commands if c.argv]

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
