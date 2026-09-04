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

import logging
import os
import re
from typing import NamedTuple

import bashlex

from agentgate.api.schemas import Tool
from agentgate.normalize.domains import extract_domains
from agentgate.normalize.model import Flags, NormalizedAction, Redirect, SimpleCommand
from agentgate.normalize.paths import looks_like_path, looks_unresolved, resolve_path
from agentgate.shell.commands import (
    CommandSpec,
    PathArguments,
    Role,
    commands_with_role,
    spec_for,
)
from agentgate.shell.wrappers import resolve_effective_argv

log = logging.getLogger(__name__)

_EVAL_LIKE = {"eval", "exec", "source", "."}
_VAR = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_BRACE_EXPANSION = re.compile(r"\{[^{}]*,[^{}]*\}")

# argv[0] values (matched by basename, so "/bin/bash" counts too) for
# which a heredoc/here-string body is executable code, not inert data —
# see _is_shell_exe.
_SHELL_NAMES = commands_with_role(Role.SHELL)

# Bound on our OWN recursive descent into heredoc/here-string bodies and
# command/process substitutions. Each heredoc level forces a fresh bashlex.parse() call on a body that
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
    beyond the fail-closed mechanism itself.
    """


def _is_shell_exe(argv0: str) -> bool:
    return os.path.basename(argv0) in _SHELL_NAMES


def _shell_after_wrappers(tokens: list[str]) -> bool:
    """True if ``tokens`` (an argv-shaped list of literal words) ultimately
    names a shell, looking past leading wrapper commands (env, sudo, ...)
    and everything those wrappers consume before the command they run.

    Delegates to ``resolve_effective_argv`` rather than repeating the skip
    loop: two divergent copies of "what does this argv really run" is
    exactly the drift that let `nice -n 10 bash <<EOF`, `timeout 30 bash
    <<EOF` and `env FOO=bar bash <<EOF` file their heredoc bodies as inert
    data, silently hiding every command inside them.
    """
    resolved = resolve_effective_argv(tokens)
    if not resolved:
        return False
    return _is_shell_exe(resolved[0])


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
        self.assignments: dict[str, str] = {}
        self.depth = 0
        self._commands: list[SimpleCommand] = []
        self._pipeline_counter = 0
        self._has_eval = False
        self._has_subst = False
        self._has_env_assign = False
        self._has_heredoc = False
        self._has_unresolved_expansion = False

    def run(self, raw: str) -> None:
        for tree in bashlex.parse(raw):
            self.walk(tree)

    def result(self, raw: str) -> NormalizedAction:
        """The whole action, built once, after the walk is over."""
        commands = list(self._commands)
        scan = _collect_paths(commands, self.cwd)
        return NormalizedAction(
            tool=Tool.shell,
            cwd=self.cwd,
            raw=raw,
            commands=commands,
            paths=scan.paths,
            domains=_collect_domains(commands),
            flags=Flags(
                has_eval=self._has_eval,
                has_subst=self._has_subst,
                has_env_assign=self._has_env_assign,
                has_heredoc=self._has_heredoc,
                has_unresolved_expansion=self._has_unresolved_expansion or scan.saw_unresolved,
            ),
        )

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
            # `cat <<EOF | bash`. This pre-scan only looks at direct "command"
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
                self._has_subst = True
                self._push_nesting()
                try:
                    self.walk(part.command)
                finally:
                    self._pop_nesting()
            elif part.kind == "parameter":
                m = _VAR.match(value)
                if m and m.group(1) in self.assignments:
                    value = self.assignments[m.group(1)]
                    self._has_env_assign = True
                else:
                    # A $VAR/${VAR} we cannot resolve from a same-line
                    # assignment (either it's embedded in a larger token
                    # like "$HOME/dist", or it's a whole-word reference to
                    # a variable we never saw assigned). The literal text
                    # is kept in argv; downstream path collection must not
                    # fabricate a resolved path from it.
                    self._has_unresolved_expansion = True
            elif part.kind == "tilde":
                if part.value != "~":
                    # ~user (not a bare ~ or ~/...): same "cannot resolve
                    # without runtime state" class as an unresolved $VAR.
                    self._has_unresolved_expansion = True
        if _BRACE_EXPANSION.search(value):
            # bashlex does not decompose brace expansion into word parts
            # (it stays a single literal token), so this check is
            # independent of the parts loop above.
            self._has_unresolved_expansion = True
        return value

    def _command(self, node, pipeline_id: int) -> tuple[list[str], list[str]]:
        """Build one SimpleCommand from a bashlex 'command' node.

        Returns (heredoc_bodies, argv) rather than deciding itself
        whether to parse the bodies as code: that decision depends on
        context this method doesn't have (whether a *sibling* command in
        the same pipeline is a shell), so it's made by the caller in
        walk().
        """
        argv: list[str] = []
        redirects: list[Redirect] = []
        stdin_from: str | None = None
        heredoc_bodies: list[str] = []
        for part in node.parts:
            if part.kind == "assignment":
                name, _, val = part.word.partition("=")
                self.assignments[name] = val
                self._has_env_assign = True
            elif part.kind == "word":
                argv.append(self._word_value(part))
            elif part.kind == "redirect":
                if part.type in ("<<", "<<-"):
                    self._has_heredoc = True
                    delimiter = part.output.word if hasattr(part.output, "word") else ""
                    raw_body = part.heredoc.value if part.heredoc is not None else ""
                    heredoc_bodies.append(_strip_heredoc_delimiter(raw_body, delimiter))
                    continue
                if part.type == "<<<":
                    self._has_heredoc = True
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
                    # dropped target is never silent.
                    self._has_unresolved_expansion = True
                    target_path = target
                else:
                    target_path = resolve_path(target, self.cwd)
                op = f"{part.input}{part.type}" if isinstance(part.input, int) else part.type
                redirects.append(Redirect(op=op, target=target_path))
                if part.type == "<":
                    stdin_from = target_path
        if argv and argv[0] in _EVAL_LIKE:
            self._has_eval = True
        if argv:
            self._commands.append(
                SimpleCommand(
                    argv=argv,
                    redirects=redirects,
                    stdin_from=stdin_from,
                    pipeline_id=pipeline_id,
                    # Recorded unconditionally, whether or not this body
                    # ends up being parsed as code below — action_hash()
                    # must differ between two actions whose heredoc
                    # bodies differ, even when neither is understood
                    # structurally.
                    heredoc_bodies=list(heredoc_bodies),
                )
            )
        return heredoc_bodies, argv


class _PathScan(NamedTuple):
    """What one pass over the commands found: the paths, and whether any
    token had to be dropped because it needed runtime state to resolve.
    """

    paths: list[str]
    saw_unresolved: bool


def _argument_path(spec: CommandSpec, args: list[str], index: int, cwd: str) -> str | None:
    """The path ``args[index]`` names, or None if it names none.

    A command whose arguments are not declared paths is judged token by
    token by the heuristic; a command that declares them is trusted, minus
    its own flags and the values they take.
    """
    token = args[index]
    if spec.path_arguments is PathArguments.UNDECLARED:
        return resolve_path(token, cwd) if looks_like_path(token) else None
    if token.startswith("-"):
        return None
    if index > 0 and args[index - 1] in spec.value_flags:
        return None  # the option before it took this token as its value
    if spec.path_arguments is PathArguments.SEARCH_ROOTS and token.startswith("*"):
        return None  # a glob among search roots is a pattern, not a path
    return resolve_path(token, cwd)


def _collect_paths(commands: list[SimpleCommand], cwd: str) -> _PathScan:
    paths: list[str] = []
    saw_unresolved = False

    def add(p: str) -> None:
        if p not in paths:
            paths.append(p)

    for cmd in commands:
        spec = spec_for(cmd.argv[0])
        args = cmd.argv[1:]
        for index, token in enumerate(args):
            if looks_unresolved(token):
                # A quoted/escaped "$"/"~" produces no bashlex
                # parameter/tilde part, so _word_value never flagged it;
                # dropping a path must never be silent, so the drop
                # reports it here rather than assuming upstream did.
                saw_unresolved = True
                continue
            path = _argument_path(spec, args, index, cwd)
            if path is not None:
                add(path)
        for r in cmd.redirects:
            if r.target.startswith("/dev/"):
                continue
            if looks_unresolved(r.target):
                saw_unresolved = True
                continue
            add(r.target)
    return _PathScan(paths, saw_unresolved)


def _collect_domains(commands: list[SimpleCommand]) -> list[str]:
    domains: list[str] = []
    for command in commands:
        for domain in extract_domains(command.argv):
            if domain not in domains:
                domains.append(domain)
    return domains


def normalize_shell(raw: str, cwd: str) -> NormalizedAction:
    walker = _Walker(cwd)
    try:
        walker.run(raw)
        return walker.result(raw)
    except Exception:  # noqa: BLE001 - any failure here means "unparseable", not a crash
        # bashlex's own tokenizer/parser errors, anything raised while
        # walking the tree or collecting paths/domains afterwards, a
        # nested bashlex.parse() failure on a heredoc or
        # process-substitution body, our own _MaxNestingDepthExceeded
        # bound tripping: all of them fail closed as a whole. A partial
        # result built before the failure is discarded rather than
        # returned, so we never claim "nothing suspicious" about an
        # action we only partially understood. Logged because a bug in
        # our own code must not be indistinguishable from garbage input.
        log.warning("shell normalization failed, treating the action as unparseable", exc_info=True)
        return NormalizedAction(tool=Tool.shell, cwd=cwd, raw=raw, flags=Flags(unparseable=True))
