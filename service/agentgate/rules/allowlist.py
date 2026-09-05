"""Commands safe enough to allow without asking the classifier.

A fixed set of read-only commands, a fixed set of read-only git
subcommands, and the operator's own safe command prefixes -- allowed
outright when every referenced path stays inside the workspace, none of
them is protected, and no unresolved expansion (`eval`, command
substitution) is present. file_read/file_write tool calls are allowed on
the same terms.

A protected path is never auto-allowed, for reads as much as for writes:
blessing a readonly command because reading is not mutation would hand an
agent the gate's own permission to put a secret into the model's context.
That is not a denial either -- inspecting protected config is not
inherently malicious -- so it returns None and the read falls through to
stage 2, exactly like a read outside the workspace. Overwriting a
protected path is ProtectedWriteRule's concern, upstream in the chain.

The protected-path guard enumerates each command's own argv tokens rather
than reading NormalizedAction.paths, which the normalizer fills from a
narrower rule: a readonly command outside that rule reading a bare-name
protected file would otherwise slip past the guard as allow.
"""

from agentgate.api.schemas import Tool
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.normalize.paths import is_within, matches_any
from agentgate.rules.readonly import is_readonly, matches_prefix, paths_are_safe
from agentgate.shell.paths import PathRole, command_paths


class AllowlistRule:
    id = "allowlist"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        if action.tool is Tool.file_read:
            return Verdict.allow("allowlist.file_read") if _file_paths_are_safe(action, policy) else None
        if action.tool is Tool.file_write:
            return Verdict.allow("allowlist.file_write") if _file_paths_are_safe(action, policy) else None
        if action.tool is not Tool.shell or not action.commands or action.flags.unparseable:
            return None
        if action.flags.has_eval or action.flags.has_subst:
            return None
        if action.paths and not all(is_within(p, policy.allowed_paths) for p in action.paths):
            return None
        if any(
            matches_any(p, policy.protected_paths, policy.workspace)
            for c in action.commands
            for p in command_paths(c.argv, action.cwd, PathRole.ANY)
        ):
            return None
        if all(matches_prefix(c, policy.safe_prefixes) for c in action.commands):
            return Verdict.allow("allowlist.prefix")
        if all(is_readonly(c) or matches_prefix(c, policy.safe_prefixes) for c in action.commands):
            return Verdict.allow("allowlist.readonly")
        return None


def _file_paths_are_safe(action: NormalizedAction, policy: Policy) -> bool:
    """Every path of a file_read/file_write call is inside the allowed
    paths and none of them is protected. No paths at all is not safe --
    there is nothing to have checked.
    """
    return bool(action.paths) and paths_are_safe(action.paths, policy)
