"""Entry point turning a DecideRequest into a NormalizedAction.

This is the only conversion later stages are allowed to build decisions
on — see service/CLAUDE.md: no decision may be based on the raw command
string.
"""

from agentgate.api.schemas import DecideRequest, Tool
from agentgate.normalize.model import NormalizedAction
from agentgate.normalize.paths import resolve_path
from agentgate.normalize.shell import normalize_shell


def normalize(req: DecideRequest) -> NormalizedAction:
    cwd = req.args.cwd
    if req.tool is Tool.shell:
        return normalize_shell(req.raw, cwd)
    action = NormalizedAction(tool=req.tool, cwd=cwd, raw=req.raw)
    if req.tool in (Tool.file_read, Tool.file_write):
        action.paths = [resolve_path(p, cwd) for p in req.args.paths]
    elif req.tool is Tool.network:
        action.domains = sorted({d.lower() for d in req.args.domains})
    elif req.tool is Tool.mcp_call:
        action.mcp = req.args.mcp
    return action


__all__ = ["normalize", "NormalizedAction"]
