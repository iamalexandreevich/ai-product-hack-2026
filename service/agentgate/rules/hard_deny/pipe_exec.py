"""Running downloaded content without ever looking at it.

Two shapes of the same thing: a downloader piped straight into an
interpreter, and a `shell -c` whose command substitution does the
downloading. Both hand a remote file the authority of the local shell.
"""

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.rules.hard_deny.shared import DOWNLOADERS, by_pipeline, effective_argv

_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_INTERPRETERS = _SHELLS | {"python", "python3", "node", "perl", "ruby"}

_SUGGEST = "Download to a file inside the workspace, inspect it, then run it explicitly"


class PipeExecRule:
    id = "hard-deny.pipe-exec"
    hard = True

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        for cmds in by_pipeline(action).values():
            downloaded = False
            for c in cmds:
                argv = effective_argv(c.argv)
                exe = argv[0] if argv else ""
                if exe in DOWNLOADERS:
                    downloaded = True
                elif downloaded and exe in _INTERPRETERS:
                    return Verdict.deny(
                        self.id, f"downloaded content piped into '{exe}'", _SUGGEST, hard=True
                    )
        if action.flags.has_subst:
            effs = [effective_argv(c.argv) for c in action.commands]
            has_shell_c = any(ea and ea[0] in _SHELLS and "-c" in ea for ea in effs)
            has_dl = any(ea and ea[0] in DOWNLOADERS for ea in effs)
            if has_shell_c and has_dl:
                return Verdict.deny(
                    self.id, "shell -c with command substitution that downloads content",
                    _SUGGEST, hard=True,
                )
        return None
