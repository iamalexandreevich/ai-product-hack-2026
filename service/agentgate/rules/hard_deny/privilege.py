"""Taking privileges the agent was not given.

Escalation (sudo/su/doas) and firewall changes are denied outright; chmod
and chown are denied only for what they actually widen -- a world-writable
mode, or an ownership change reaching outside the allowed paths.

sudo and doas are never unwrapped by wrapper resolution, so "env sudo rm
-rf /" still arrives here as sudo.
"""

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.normalize.paths import is_within, resolve_path
from agentgate.profiles.schema import Profile
from agentgate.rules.hard_deny.shared import effective_argv

_ESCALATORS = ("sudo", "su", "doas")
_FIREWALL = {"iptables", "ip6tables", "nft", "ufw", "pfctl", "firewall-cmd"}
_WORLD_WRITABLE_MODES = ("777", "0777", "a+rwx")


class PrivilegeRule:
    id = "hard-deny.privilege"
    hard = True

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        allowed = profile.resolved_allowed_paths()
        for command in action.commands:
            argv = effective_argv(command.argv)
            if not argv:
                continue
            verdict = self._for_command(argv, action.cwd, allowed)
            if verdict is not None:
                return verdict
        return None

    def _for_command(self, argv: list[str], cwd: str, allowed: list[str]) -> Verdict | None:
        exe = argv[0]
        if exe in _ESCALATORS:
            return self._deny(f"'{exe}' is not allowed", "Ask the user to run privileged commands")
        if exe in _FIREWALL:
            return self._deny(f"firewall change via '{exe}'", "Ask the user")
        if exe == "chmod":
            return self._widened_permissions(argv)
        if exe == "chown":
            return self._ownership_outside_workspace(argv, cwd, allowed)
        return None

    def _widened_permissions(self, argv: list[str]) -> Verdict | None:
        modes = [a for a in argv[1:] if not a.startswith("-")]
        if not modes:
            return None
        mode = modes[0]
        if mode in _WORLD_WRITABLE_MODES or "o+w" in mode or "a+w" in mode:
            return self._deny(f"chmod {mode} makes files world-writable", "Use the minimal mode needed")
        return None

    def _ownership_outside_workspace(
        self, argv: list[str], cwd: str, allowed: list[str]
    ) -> Verdict | None:
        # argv[1] is the owner spec, not a path.
        for arg in argv[2:]:
            if arg.startswith("-"):
                continue
            path = resolve_path(arg, cwd)
            if not is_within(path, allowed):
                return self._deny(f"chown outside workspace: {path}", "")
        return None

    def _deny(self, reason: str, suggest: str) -> Verdict:
        return Verdict.deny(self.id, reason, suggest, hard=True)
