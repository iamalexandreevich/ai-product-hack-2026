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

The protected-path guard enumerates each command's own argv tokens via
``agentgate.stage1.argv_paths.command_argv_paths`` rather than reading
``NormalizedAction.paths`` (fix round 2, task 6): the normalizer only
populates ``paths`` for a command in ``normalize/shell.py``'s
PATH_COMMANDS or a token that independently looks like a path, so a
READONLY command outside that list (``sort``, ``cut``, ``diff``,
``uniq``) reading a bare-name protected file (no leading ``/``, no
``/`` at all, not a hard-coded sensitive basename) previously left
``action.paths`` empty and slipped past the guard as ``allow``. The
shared enumeration is the same one ``check_profile`` uses for mutating
targets — one implementation, so the two checks cannot drift into two
different opinions of what path a command touches.
"""

from agentgate.api.schemas import Tool
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction, SimpleCommand
from agentgate.normalize.paths import is_within, matches_any
from agentgate.profiles.schema import Profile
from agentgate.stage1.argv_paths import command_argv_paths

READONLY = {"ls", "cat", "head", "tail", "wc", "grep", "rg", "pwd", "which", "stat", "du", "file", "tree", "sort", "uniq", "cut", "tr", "less", "more", "diff"}
GIT_READONLY = {"status", "diff", "log", "show", "branch", "rev-parse", "remote", "blame"}


def _is_readonly(cmd: SimpleCommand) -> bool:
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


def check_allowlist(action: NormalizedAction, profile: Profile) -> Verdict | None:
    allowed = profile.resolved_allowed_paths()
    protected = profile.resolved_protected_paths()
    if action.tool is Tool.file_read:
        if action.paths and all(
            is_within(p, allowed) and not matches_any(p, protected, profile.workspace) for p in action.paths
        ):
            return Verdict.allow("allowlist.file_read")
        return None
    if action.tool is Tool.file_write:
        if action.paths and all(is_within(p, allowed) and not matches_any(p, protected, profile.workspace) for p in action.paths):
            return Verdict.allow("allowlist.file_write")
        return None
    if action.tool is not Tool.shell or not action.commands or action.flags.unparseable:
        return None
    if action.flags.has_eval or action.flags.has_subst:
        return None
    if action.paths and not all(is_within(p, allowed) for p in action.paths):
        return None
    if any(
        matches_any(p, protected, profile.workspace)
        for c in action.commands
        for p in command_argv_paths(c, action.cwd)
    ):
        # An argv token of some command resolves to a protected path —
        # see the module docstring: neither allowlist.readonly nor
        # allowlist.prefix may bless a protected-path read. Enumerated
        # per-command via command_argv_paths, not via
        # NormalizedAction.paths — see the docstring for why that
        # distinction matters (fix round 2). Fall through to stage 2
        # rather than deny.
        return None
    if all(_matches_prefix(c, profile.safe_prefixes) for c in action.commands):
        return Verdict.allow("allowlist.prefix")
    if all(_is_readonly(c) or _matches_prefix(c, profile.safe_prefixes) for c in action.commands):
        return Verdict.allow("allowlist.readonly")
    return None
