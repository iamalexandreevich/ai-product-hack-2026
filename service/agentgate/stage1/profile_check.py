"""Stage 1, profile check.

Enforces two things from the operator's profile:

- Mutating filesystem targets (a small fixed set of commands, plus
  redirects and file_write paths) must resolve inside
  ``profile.resolved_allowed_paths()``. Reading outside the workspace
  (e.g. ``cat /etc/hosts``) is deliberately NOT denied here — see the
  spec clarification in the task-6 brief: profile path denial applies
  only to mutating commands and file_write, everything else falls
  through to stage 2.
- Domains outside the network allowlist are denied (mode off/allowlist),
  escalated to ask (mode ask), or passed through (mode open).

Reasons and suggestions are built from a fixed vocabulary plus, where
unavoidable, a resolved path or domain string that is itself
action-derived. See the task-6 report for the note to Task 10: this
text reaches the stage-2 LLM prompt as `stage1_note` and must be
escaped there like any other attacker-influenced content.
"""

from agentgate.api.schemas import DecisionKind, Tool
from agentgate.normalize.model import NormalizedAction
from agentgate.normalize.paths import is_within
from agentgate.profiles.schema import NetworkMode, Profile
from agentgate.stage1.argv_paths import command_argv_paths
from agentgate.stage1.types import Stage1Decision

MUTATING = {"rm", "mv", "cp", "mkdir", "rmdir", "touch", "chmod", "chown", "tee", "install", "ln", "truncate", "dd", "shred"}


def _mutating_targets(action: NormalizedAction) -> list[str]:
    out: list[str] = []
    for c in action.commands:
        exe = c.argv[0]
        argv_paths = command_argv_paths(c, action.cwd)
        if exe in MUTATING:
            out += argv_paths
        elif exe == "sed" and any(a.startswith("-i") for a in c.argv[1:]):
            # sed's first non-flag argument is the substitution script,
            # not a target — skip it. command_argv_paths already
            # dropped the flags themselves.
            out += argv_paths[1:]
        for r in c.redirects:
            if r.op.endswith(">") or r.op.endswith(">>"):
                if not r.target.startswith("/dev/"):
                    out.append(r.target)
    if action.tool is Tool.file_write:
        out += action.paths
    return out


def check_profile(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    allowed = profile.resolved_allowed_paths()
    for p in _mutating_targets(action):
        if not is_within(p, allowed):
            return Stage1Decision(DecisionKind.deny, "profile.path", f"write outside allowed paths: {p}",
                                  "Work inside the workspace")
    if action.domains and profile.network.mode is not NetworkMode.open:
        allowed_domains = {d.lower() for d in profile.network.allowed_domains}
        for d in action.domains:
            if d in allowed_domains or any(d.endswith("." + a) for a in allowed_domains):
                continue
            if profile.network.mode is NetworkMode.ask:
                return Stage1Decision(DecisionKind.ask, "profile.domain", f"domain {d} is not in the allowlist", "")
            return Stage1Decision(DecisionKind.deny, "profile.domain", f"domain {d} is not in the allowlist",
                                  "Use an allowed registry or ask the user to extend the allowlist")
    return None
