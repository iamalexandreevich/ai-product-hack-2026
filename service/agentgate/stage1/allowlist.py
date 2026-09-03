"""Stage 1, allowlist check.

Recognizes a fixed set of read-only commands, a fixed set of read-only
git subcommands, and operator-configured safe command prefixes
(``profile.safe_prefixes``), and allows them outright when every
referenced path (if any) stays inside the workspace, none of them is a
protected path, and no unresolved expansion (``eval``, command
substitution) is present. Also allows file_read/file_write tool calls
whose paths are inside ``resolved_allowed_paths()`` and outside
``resolved_protected_paths()``.

A protected path is never auto-allowed here, for reads or writes alike
(fix round 1, task 6): the readonly allowlist and protected_paths are
two independent mechanisms, and letting a readonly command (``cat``,
``head``, ``grep``, ...) bypass protection because reading isn't
mutation would hand an agent the gate's explicit blessing to put a
secret's contents into the model's context. This does not escalate to
deny — a tool legitimately inspecting protected config is not
inherently malicious — it returns None so the read falls through to
stage 2, exactly like a read outside the workspace already does.
Deleting or overwriting a protected path is a separate concern already
covered by Task 5's hard-deny protected-write rule, upstream of this
check in the chain.
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
        if action.paths and all(
            is_within(p, allowed) and not matches_any(p, protected, profile.workspace) for p in action.paths
        ):
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
    if action.paths and any(matches_any(p, protected, profile.workspace) for p in action.paths):
        # A referenced path (readonly command argument, or a safe-prefix
        # command's argument) is protected — see the module docstring:
        # neither allowlist.readonly nor allowlist.prefix may bless a
        # protected-path read. Fall through to stage 2 rather than deny.
        return None
    if all(_matches_prefix(c, profile.safe_prefixes) for c in action.commands):
        return Stage1Decision(DecisionKind.allow, "allowlist.prefix", "")
    if all(_is_readonly(c, True) or _matches_prefix(c, profile.safe_prefixes) for c in action.commands):
        return Stage1Decision(DecisionKind.allow, "allowlist.readonly", "")
    return None
