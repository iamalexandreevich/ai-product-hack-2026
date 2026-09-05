"""The user's own rules, at three points of the stage-1 chain.

One class, three instances: `deny` right after hard-deny, `ask` after the
profile's own denials, `allow` before the server allowlist. That is the
whole priority policy for user rules -- a user can forbid more and can
permit what is otherwise gray, but cannot permit what hard-deny or the
operator's profile forbids.

Command patterns are matched against a canonical form built from the
normalized action, never against the raw line: argv joined by spaces for
one command, ` | ` between the commands of a pipeline. A compound
command (`&&`, `;`) is several units. `deny` and `ask` fire when any unit
or any single command matches; `allow` requires every unit to match and
refuses the same things the server allowlist refuses (eval, substitution,
file redirects), because an allow is a promise about the whole line.

`ask` is the one mode that does not settle anything: it returns a floor
(`Verdict.ask(..., floor=True)`), so the chain records it and keeps
running. A user asking to confirm a command must not thereby switch off
the classifier's own `deny` on it -- that is the whole of spec v3.1 §3.2.

An MCP call has one canonical form, `server.tool` (`github.get_issue`),
exactly as it arrives in `McpArgs`. It is not compound, so the "units" and
the "singles" are the same single string. Case is not folded -- MCP tool
names are case-sensitive -- and the call's `arguments` never take part in
matching: they are arbitrary JSON, and globbing their serialization would
be deciding on untrusted text.
"""

from typing import Literal

from agentgate.api.schemas import Tool
from agentgate.domain.client_rules import ClientRules
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.shell.paths import PathRole, command_paths, redirect_targets, writes_a_file

Mode = Literal["allow", "ask", "deny"]


def canonical_units(action: NormalizedAction) -> tuple[list[str], list[str]]:
    """Pipelines as one string each, and every single command on its own.

    An MCP call is neither: it is one `server.tool` string, returned as
    both, because there is nothing compound to take apart.
    """
    if action.tool is Tool.mcp_call:
        units = [action.mcp_name] if action.mcp_name is not None else []
        return (units, units)
    by_pipeline: dict[int, list[str]] = {}
    for command in action.commands:
        by_pipeline.setdefault(command.pipeline_id, []).append(" ".join(command.argv))
    units = [" | ".join(parts) for parts in by_pipeline.values()]
    singles = [" ".join(command.argv) for command in action.commands]
    return units, singles


class ClientRulesRule:
    hard = False

    def __init__(self, mode: Mode) -> None:
        self.mode = mode
        self.id = f"client.{mode}"

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        rules = policy.client_rules
        if rules is None:
            return None
        if self.mode == "allow":
            return self._allow(action, rules)
        return self._refuse_if_matched(action, rules)

    def _refuse_if_matched(self, action: NormalizedAction, rules: ClientRules) -> Verdict | None:
        if self._any_match(action, rules):
            return self._refusal()
        return None

    def _any_match(self, action: NormalizedAction, rules: ClientRules) -> bool:
        if any(rules.matches_path(self.mode, p) for p in _paths(action)):
            return True
        units, singles = canonical_units(action)
        return any(rules.matches_command(self.mode, s) for s in [*units, *singles])

    def _allow(self, action: NormalizedAction, rules: ClientRules) -> Verdict | None:
        if action.tool is Tool.mcp_call:
            return self._allow_mcp(action, rules)
        if action.tool is not Tool.shell:
            return self._allow_paths(action, rules)
        return self._allow_shell(action, rules)

    def _allow_mcp(self, action: NormalizedAction, rules: ClientRules) -> Verdict | None:
        # No eval, no substitution, no redirect to refuse: an MCP call
        # is a name and a JSON body, and only the name is matched.
        units, _ = canonical_units(action)
        if units and all(rules.matches_command("allow", u) for u in units):
            return Verdict.allow(self.id)
        return None

    def _allow_paths(self, action: NormalizedAction, rules: ClientRules) -> Verdict | None:
        paths = _paths(action)
        if paths and all(rules.matches_path("allow", p) for p in paths):
            return Verdict.allow(self.id)
        return None

    def _allow_shell(self, action: NormalizedAction, rules: ClientRules) -> Verdict | None:
        if not action.commands or action.flags.unparseable or action.flags.has_eval or action.flags.has_subst:
            return None
        if any(writes_a_file(c) for c in action.commands):
            return None
        units, _ = canonical_units(action)
        if all(rules.matches_command("allow", u) for u in units):
            return Verdict.allow(self.id)
        return None

    def _refusal(self) -> Verdict:
        if self.mode == "deny":
            return Verdict.deny(self.id, "blocked by your rules", "Adjust your gate rules if this was intended.")
        return Verdict.ask(self.id, "your rules ask for confirmation of this action", floor=True)


def _paths(action: NormalizedAction) -> list[str]:
    if action.tool is Tool.shell:
        paths: list[str] = []
        for command in action.commands:
            paths += command_paths(command.argv, action.cwd, PathRole.ANY)
            paths += redirect_targets(command)
        return paths
    return list(action.paths)
