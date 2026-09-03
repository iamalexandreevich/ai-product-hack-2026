"""Stage 1, hard-deny rules.

Hard-deny is the layer that can never be overridden: a Stage1Decision
with hard=True is final — it is not replaced by an `ask` escalation and
no later stage (LLM stage 2, chain-of-actions heuristics) can turn it
into anything else. See service/CLAUDE.md: "Hard-deny не переопределяется
ничем и не заменяется на `ask` эскалацией."

Because of that asymmetry, every rule here stays deliberately narrow: it
must fire on the input its name promises and never on ordinary,
unrelated work. Two Flags from the normalizer are treated as "I don't
fully know what this token is" rather than "this token is dangerous":

- flags.has_unresolved_expansion fires on completely benign text (an awk
  program, a literal "$" in a string) and a hard-deny keyed off it would
  block routine work. It is an escalation signal for stage 2 / the
  action-chain checks, not a denial ground here.
- flags.has_heredoc marks a body that may or may not have been possible
  to parse as code; it is not itself evidence of anything.
- flags.unparseable means commands/paths/domains are empty by
  construction — "found nothing dangerous" in that state is reasoning
  from silence, not a clean bill of health, so no rule below concludes
  "safe" from an empty NormalizedAction; an empty result simply can't
  match any of these rules' positive conditions, and stage 2 is where an
  unparseable action gets its own (non-hard) escalation.
"""

import fnmatch
import os

from agentgate.api.schemas import DecisionKind, Tool
from agentgate.normalize.model import NormalizedAction, SimpleCommand
from agentgate.normalize.paths import is_within, matches_any, resolve_path
from agentgate.profiles.schema import Profile
from agentgate.stage1.types import Stage1Decision

SECRET_PATTERNS = [".env*", "*.pem", "id_rsa*", "id_ed25519*", "*.key", "*.p12", "~/.ssh/**", "~/.aws/**", "~/.kube/**"]
NETWORK_COMMANDS = {"curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp", "rsync", "ftp", "telnet", "socat"}
DOWNLOADERS = {"curl", "wget"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
INTERPRETERS = SHELLS | {"python", "python3", "node", "perl", "ruby"}
WRITE_COMMANDS = {"cp", "mv", "tee", "install", "ln"}
FIREWALL = {"iptables", "ip6tables", "nft", "ufw", "pfctl", "firewall-cmd"}


def _deny(rule: str, reason: str, suggest: str = "") -> Stage1Decision:
    return Stage1Decision(DecisionKind.deny, f"hard-deny.{rule}", reason, suggest, hard=True)


def _is_secret(path: str, profile: Profile) -> bool:
    return matches_any(path, SECRET_PATTERNS, profile.workspace)


def _cmd_paths(cmd: SimpleCommand, cwd: str) -> list[str]:
    out: list[str] = []
    for tok in cmd.argv[1:]:
        t = tok[1:] if tok.startswith("@") else tok
        if t.startswith("-") or "://" in t or not t:
            continue
        if t.startswith(("/", "./", "../", "~")) or "/" in t or t.startswith("."):
            out.append(resolve_path(t, cwd))
    for r in cmd.redirects:
        out.append(r.target)
    if cmd.stdin_from:
        out.append(cmd.stdin_from)
    return out


def _by_pipeline(action: NormalizedAction) -> dict[int, list[SimpleCommand]]:
    groups: dict[int, list[SimpleCommand]] = {}
    for c in action.commands:
        groups.setdefault(c.pipeline_id, []).append(c)
    return groups


def _rule_exfil(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    for cmds in _by_pipeline(action).values():
        secret_seen: str | None = None
        for c in cmds:
            for p in _cmd_paths(c, action.cwd):
                if _is_secret(p, profile):
                    secret_seen = p
            if c.argv[0] in NETWORK_COMMANDS and secret_seen:
                return _deny("exfil", f"network command '{c.argv[0]}' with secret file {secret_seen}",
                             "Never send secret files over the network; ask the user if credentials are needed")
    return None


def _rule_pipe_exec(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    for cmds in _by_pipeline(action).values():
        downloaded = False
        for c in cmds:
            if c.argv[0] in DOWNLOADERS:
                downloaded = True
            elif downloaded and c.argv[0] in INTERPRETERS:
                return _deny("pipe-exec", f"downloaded content piped into '{c.argv[0]}'",
                             "Download to a file inside the workspace, inspect it, then run it explicitly")
    if action.flags.has_subst:
        has_shell_c = any(c.argv[0] in SHELLS and "-c" in c.argv for c in action.commands)
        has_dl = any(c.argv[0] in DOWNLOADERS for c in action.commands)
        if has_shell_c and has_dl:
            return _deny("pipe-exec", "shell -c with command substitution that downloads content",
                         "Download to a file inside the workspace, inspect it, then run it explicitly")
    return None


def _rule_destructive(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    allowed = profile.resolved_allowed_paths()
    ws = os.path.normpath(profile.workspace) if profile.workspace else None
    for c in action.commands:
        exe = c.argv[0]
        targets: list[str] = []
        # Only "rm" and "shred" unconditionally destroy the exact path
        # they are given, so only those two also deny when the target
        # equals the workspace root itself (rm -rf <workspace> wipes
        # everything even though the workspace root is nominally "within"
        # the allowed paths, being their own root). "find <root> -delete"
        # is different: <root> is a search root that houses many files,
        # and -delete only removes entries matching the given predicate,
        # not the root directory itself — "find . -delete" is an
        # ordinary, common cleanup idiom and must not be denied just
        # because "." resolves to the workspace root. So the
        # workspace-equality check is scoped to rm/shred only; find
        # relies on "not is_within" alone (its search root reaching
        # outside the workspace/allowed paths, e.g. "find /", is still
        # denied).
        deny_on_ws_equal = False
        if exe == "rm" and any(a.startswith("-") and ("r" in a or "R" in a) for a in c.argv[1:]):
            targets = [resolve_path(a, action.cwd) for a in c.argv[1:] if not a.startswith("-")]
            deny_on_ws_equal = True
        elif exe == "find" and "-delete" in c.argv:
            targets = [resolve_path(a, action.cwd) for a in c.argv[1:2] if not a.startswith("-")]
        elif exe == "shred":
            targets = [resolve_path(a, action.cwd) for a in c.argv[1:] if not a.startswith("-")]
            deny_on_ws_equal = True
        for t in targets:
            outside = not is_within(t, allowed)
            equals_ws = deny_on_ws_equal and ws is not None and os.path.normpath(t) == ws
            if outside or equals_ws:
                return _deny("destructive", f"'{exe}' targets {t} outside or equal to the workspace",
                             "Delete only build artifacts inside the workspace")
    return None


def _rule_protected_write(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    protected = profile.resolved_protected_paths()
    ws = profile.workspace
    candidates: list[str] = []
    if action.tool is Tool.file_write:
        candidates = list(action.paths)
    for c in action.commands:
        exe = c.argv[0]
        for r in c.redirects:
            if r.op.endswith(">") or r.op.endswith(">>"):
                candidates.append(r.target)
        args = [a for a in c.argv[1:] if not a.startswith("-")]
        if exe in ("cp", "mv", "install", "ln") and len(args) >= 2:
            candidates.append(resolve_path(args[-1], action.cwd))
        elif exe == "tee":
            candidates += [resolve_path(a, action.cwd) for a in args]
        elif exe == "sed" and any(a.startswith("-i") for a in c.argv[1:]):
            candidates += [resolve_path(a, action.cwd) for a in args[1:]]
    for p in candidates:
        if matches_any(p, protected, ws):
            return _deny("protected-write", f"write to protected path {p}",
                         "Protected files are changed only by the user")
    return None


def _rule_privilege(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    allowed = profile.resolved_allowed_paths()
    for c in action.commands:
        exe = c.argv[0]
        if exe in ("sudo", "su", "doas"):
            return _deny("privilege", f"'{exe}' is not allowed", "Ask the user to run privileged commands")
        if exe in FIREWALL:
            return _deny("privilege", f"firewall change via '{exe}'", "Ask the user")
        if exe == "chmod":
            modes = [a for a in c.argv[1:] if not a.startswith("-")]
            if modes and (modes[0] in ("777", "0777", "a+rwx") or "o+w" in modes[0] or "a+w" in modes[0]):
                return _deny("privilege", f"chmod {modes[0]} makes files world-writable", "Use the minimal mode needed")
        if exe == "chown":
            for p in [resolve_path(a, action.cwd) for a in c.argv[2:] if not a.startswith("-")]:
                if not is_within(p, allowed):
                    return _deny("privilege", f"chown outside workspace: {p}", "")
    return None


def _rule_git_force(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    for c in action.commands:
        if c.argv[:2] != ["git", "push"]:
            continue
        if not any(a in ("--force", "-f", "--force-with-lease") or a.startswith("--force=") for a in c.argv):
            continue
        refs = [a for a in c.argv[2:] if not a.startswith("-")][1:]  # skip remote
        for ref in refs:
            branch = ref.split(":")[-1]
            if any(fnmatch.fnmatchcase(branch, pat) for pat in profile.protected_branches):
                return _deny("git-force", f"force push to protected branch {branch}", "Push to a feature branch")
    return None


RULES = [_rule_exfil, _rule_pipe_exec, _rule_destructive, _rule_protected_write, _rule_privilege, _rule_git_force]


def check_hard_deny(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    for rule in RULES:
        d = rule(action, profile)
        if d is not None:
            return d
    return None
