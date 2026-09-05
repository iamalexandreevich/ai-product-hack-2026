"""MCP calls against the operator's own lists.

`server.tool` (`github.get_issue`) is matched with `fnmatch.fnmatchcase`
against `mcp.deny`, `mcp.ask` and `mcp.allow`, in that order: when several
lists match, the strictest wins, and it is one check in one rule rather
than three positions in the chain.

There is deliberately no hard-deny here. A name proves nothing:
`filesystem.write_file` may be a sandbox, and `notes.append` may be a
write into `~/.ssh/authorized_keys`. Hard-deny means "never, under any
circumstances", and no such claim can be built on a name alone.

The call's `arguments` are never read.
"""

import fnmatch

from agentgate.api.schemas import Tool
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


def mcp_name(action: NormalizedAction) -> str | None:
    """`server.tool` of an MCP call, or None for anything else."""
    if action.tool is not Tool.mcp_call or action.mcp is None:
        return None
    return f"{action.mcp.server}.{action.mcp.tool}"


class ProfileMcpRule:
    id = "profile.mcp"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        name = mcp_name(action)
        if name is None:
            return None
        mcp = policy.mcp
        if _matches(name, mcp.deny):
            return Verdict.deny(
                "profile.mcp-deny", f"MCP tool {name} is denied by the profile",
                "Ask the operator to extend the profile's mcp.allow if this was intended",
            )
        if _matches(name, mcp.ask):
            return Verdict.ask("profile.mcp-ask", f"MCP tool {name} needs confirmation by the profile")
        if _matches(name, mcp.allow):
            return Verdict.allow("profile.mcp-allow")
        return None


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)
