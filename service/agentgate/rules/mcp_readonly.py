"""The naming convention for MCP tools that only read.

Off unless the operator turns it on (`mcp.readonly_prefixes_allow`),
because a prefix is a convention and not a proof. When it is on, a tool
name beginning with one of the prefixes below is allowed outright.

The prefixes are a module constant and will not become a profile field.
An operator who needs their own list already has one: `mcp.allow` with
globs (`github.get_*`, `*.fetch_*`) says the same thing and says it more
precisely, because it is bound to a server. A second way to say it would
only raise the question of which of the two lists is stronger.

This rule sits below everything the operator and the user wrote by hand,
because it is a server convenience, not anyone's policy.
"""

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction

READONLY_PREFIXES: tuple[str, ...] = ("get_", "list_", "search_", "read_", "describe_")


class McpReadonlyRule:
    id = "allowlist.mcp-readonly"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        if not policy.mcp.readonly_prefixes_allow:
            return None
        if action.mcp is None:
            return None
        if action.mcp.tool.startswith(READONLY_PREFIXES):
            return Verdict.allow(self.id)
        return None
