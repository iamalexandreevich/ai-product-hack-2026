"""Entry point turning a DecideRequest into a NormalizedAction.

This is the only conversion later stages are allowed to build decisions
on — see service/CLAUDE.md: no decision may be based on the raw command
string.
"""

from agentgate.api.schemas import DecideRequest, Tool
from agentgate.normalize.model import NormalizedAction
from agentgate.normalize.paths import looks_unresolved, resolve_path
from agentgate.normalize.shell import normalize_shell


def normalize(req: DecideRequest) -> NormalizedAction:
    cwd = req.args.cwd
    if req.tool is Tool.shell:
        return normalize_shell(req.raw, cwd)
    action = NormalizedAction(tool=req.tool, cwd=cwd, raw=req.raw)
    if req.tool in (Tool.file_read, Tool.file_write):
        paths: list[str] = []
        for p in req.args.paths:
            if looks_unresolved(p):
                # e.g. a harness-supplied "~someuser/secret": same rule
                # as the shell path — don't fabricate a resolved path
                # for a token that needs runtime state to resolve;
                # flag it instead.
                action.flags.has_unresolved_expansion = True
                continue
            paths.append(resolve_path(p, cwd))
        action.paths = paths
    elif req.tool is Tool.network:
        action.domains = sorted({d.lower() for d in req.args.domains})
    elif req.tool is Tool.mcp_call:
        action.mcp = req.args.mcp
    return action


__all__ = ["normalize", "NormalizedAction"]
