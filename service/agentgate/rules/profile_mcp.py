"""MCP calls against the operator's own lists.

`server.tool` (`github.get_issue`) is matched with `fnmatch.fnmatchcase`
against `mcp.deny`, `mcp.ask` and `mcp.allow`. Two instances of this rule
sit at two different points of `STAGE1`, mirroring `ClientRulesRule`:
`ProfileMcpRule("refuse")` checks `deny` then `ask`, right after the
profile's other denials, so an operator's refusal cannot be softened by
anything below it. `ProfileMcpRule("allow")` checks only `allow`, right
before the server allowlist -- below the user's own `ask` floor, so an
operator's `allow` on an MCP tool is settled as `ask` at stage 1 when the
user asked to confirm it, exactly like every other allow in the chain.

There is deliberately no hard-deny here. A name proves nothing:
`filesystem.write_file` may be a sandbox, and `notes.append` may be a
write into `~/.ssh/authorized_keys`. Hard-deny means "never, under any
circumstances", and no such claim can be built on a name alone.

The call's `arguments` are never read.
"""

import fnmatch
from typing import Literal

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction

Mode = Literal["refuse", "allow"]


class ProfileMcpRule:
    hard = False

    def __init__(self, mode: Mode) -> None:
        self.mode = mode
        self.id = f"profile.mcp-{mode}"

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        name = action.mcp_name
        if name is None:
            return None
        if self.mode == "refuse":
            return self._refuse(name, policy)
        return self._allow(name, policy)

    def _refuse(self, name: str, policy: Policy) -> Verdict | None:
        mcp = policy.mcp
        if _matches(name, mcp.deny):
            return Verdict.deny(
                "profile.mcp-deny", f"MCP tool {name} is denied by the profile",
                "Ask the operator to extend the profile's mcp.allow if this was intended",
            )
        if _matches(name, mcp.ask):
            return Verdict.ask("profile.mcp-ask", f"MCP tool {name} needs confirmation by the profile")
        return None

    def _allow(self, name: str, policy: Policy) -> Verdict | None:
        if _matches(name, policy.mcp.allow):
            return Verdict.allow("profile.mcp-allow")
        return None


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)
