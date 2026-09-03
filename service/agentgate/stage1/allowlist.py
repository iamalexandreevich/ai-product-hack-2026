"""Stage 1, allowlist check.

Recognizes a fixed set of read-only commands, a fixed set of read-only
git subcommands, and operator-configured safe command prefixes
(``profile.safe_prefixes``), and allows them outright when every
referenced path (if any) stays inside the workspace and no unresolved
expansion (``eval``, command substitution) is present. Also allows
file_read/file_write tool calls whose paths are inside
``resolved_allowed_paths()`` (file_write additionally must not touch a
protected path — that stays reserved for hard-deny).
"""

from agentgate.api.schemas import DecisionKind, Tool
from agentgate.normalize.model import NormalizedAction, SimpleCommand
from agentgate.normalize.paths import is_within, matches_any
from agentgate.profiles.schema import Profile
from agentgate.stage1.types import Stage1Decision

READONLY = {"ls", "cat", "head", "tail", "wc", "grep", "rg", "pwd", "which", "stat", "du", "file", "tree", "sort", "uniq", "cut", "tr", "less", "more", "diff"}
GIT_READONLY = {"status", "diff", "log", "show", "branch", "rev-parse", "remote", "blame"}


def _is_readonly(cmd: SimpleCommand, cwd_paths_ok: bool) -> bool:
    exe = cmd.argv[0]
    if any(r.op.endswith(">") or r.op.endswith(">>") for r in cmd.redirects):
        return False
    if exe in READONLY:
        return True
    if exe == "git" and len(cmd.argv) > 1 and cmd.argv[1] in GIT_READONLY:
        return True
    if exe == "echo":
        return True
    if exe == "env" and len(cmd.argv) == 1:
        return True
    if exe == "find" and "-delete" not in cmd.argv and "-exec" not in cmd.argv and "-execdir" not in cmd.argv and "-ok" not in cmd.argv:
        return True
    return False


def _matches_prefix(cmd: SimpleCommand, prefixes: list[list[str]]) -> bool:
    return any(cmd.argv[: len(p)] == p for p in prefixes if p)


def check_allowlist(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    allowed = profile.resolved_allowed_paths()
    protected = profile.resolved_protected_paths()
    if action.tool is Tool.file_read:
        if action.paths and all(is_within(p, allowed) for p in action.paths):
            return Stage1Decision(DecisionKind.allow, "allowlist.file_read", "")
        return None
    if action.tool is Tool.file_write:
        if action.paths and all(is_within(p, allowed) and not matches_any(p, protected, profile.workspace) for p in action.paths):
            return Stage1Decision(DecisionKind.allow, "allowlist.file_write", "")
        return None
    if action.tool is not Tool.shell or not action.commands or action.flags.unparseable:
        return None
    if action.flags.has_eval or action.flags.has_subst:
        return None
    if action.paths and not all(is_within(p, allowed) for p in action.paths):
        return None
    if all(_matches_prefix(c, profile.safe_prefixes) for c in action.commands):
        return Stage1Decision(DecisionKind.allow, "allowlist.prefix", "")
    if all(_is_readonly(c, True) or _matches_prefix(c, profile.safe_prefixes) for c in action.commands):
        return Stage1Decision(DecisionKind.allow, "allowlist.readonly", "")
    return None
