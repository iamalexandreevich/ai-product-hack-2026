"""Entry point turning a DecideRequest into a NormalizedAction.

This is the only conversion later stages are allowed to build decisions
on — see service/CLAUDE.md: no decision may be based on the raw command
string.
"""

from agentgate.api.schemas import DecideRequest, Tool
from agentgate.normalize.model import Flags, NormalizedAction
from agentgate.normalize.paths import looks_unresolved, resolve_path
from agentgate.normalize.shell import normalize_shell


def normalize(req: DecideRequest) -> NormalizedAction:
    cwd = req.args.cwd
    if req.tool is Tool.shell:
        return normalize_shell(req.raw, cwd)
    if req.tool in (Tool.file_read, Tool.file_write):
        return _file_action(req, cwd)
    if req.tool is Tool.network:
        domains = sorted({d.lower() for d in req.args.domains})
        return NormalizedAction(tool=req.tool, cwd=cwd, raw=req.raw, domains=domains)
    if req.tool is Tool.mcp_call:
        return NormalizedAction(tool=req.tool, cwd=cwd, raw=req.raw, mcp=req.args.mcp)
    return NormalizedAction(tool=req.tool, cwd=cwd, raw=req.raw)


def _file_action(req: DecideRequest, cwd: str) -> NormalizedAction:
    """A file_read/file_write action. A harness-supplied path that needs
    runtime state to resolve (e.g. "~someuser/secret") is dropped and
    flagged rather than fabricated into a resolved path that could look
    safely inside the workspace when the real one is not.
    """
    paths: list[str] = []
    unresolved = False
    for path in req.args.paths:
        if looks_unresolved(path):
            unresolved = True
            continue
        paths.append(resolve_path(path, cwd))
    return NormalizedAction(
        tool=req.tool, cwd=cwd, raw=req.raw, paths=paths,
        flags=Flags(has_unresolved_expansion=unresolved),
    )


__all__ = ["normalize", "NormalizedAction"]
