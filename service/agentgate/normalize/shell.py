"""Shell command normalization via bashlex AST walking.

Turns a raw shell string into structured SimpleCommand entries plus
extracted paths/domains and risk flags. Never makes any allow/deny
decision here — this module only produces facts for later stages.

Fail-closed: if bashlex cannot parse the input (or anything raised while
walking the parsed tree — including a nested bashlex.parse of a heredoc
or process-substitution body, or any other unexpected exception), we do
not silently return "nothing found" — we set flags.unparseable=True with
empty commands/paths/domains so stage 1/2 can escalate on the
unparseable flag instead of treating it as a safe no-op. The entire
post-initial-parse pipeline (AST walk, path collection, domain
extraction) runs inside one try/except for exactly this reason: a
partial result that silently omits the one construct we failed on is a
bypass, not a best-effort answer.
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


def _is_shell_exe(argv0: str) -> bool:
    return os.path.basename(argv0) in _SHELL_NAMES


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

    def _next_pipeline(self) -> int:
        self._pipeline_counter += 1
        return self._pipeline_counter

    def walk(self, node, pipeline_id: int | None = None) -> None:
        kind = node.kind
        if kind == "pipeline":
            pid = self._next_pipeline()
            for part in node.parts:
                if part.kind != "pipe":
                    self.walk(part, pid)
            return
        if kind == "command":
            self._command(node, pipeline_id if pipeline_id is not None else self._next_pipeline())
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
                self.walk(part.command)
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

    def _command(self, node, pipeline_id: int) -> None:
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
                SimpleCommand(argv=argv, redirects=redirects, stdin_from=stdin_from, pipeline_id=pipeline_id)
            )
        if heredoc_bodies and argv and _is_shell_exe(argv[0]):
            # argv[0] is a shell: the heredoc/here-string body is code
            # that shell will execute, not inert data (contrast `cat
            # <<EOF > file`, where the body is data and stays opaque
            # except for the has_heredoc flag). Parse it exactly like the
            # raw script and walk the result so its commands, paths and
            # domains surface. If this nested parse fails, the exception
            # propagates out of walk() and is caught by normalize_shell's
            # fail-closed wrapper, same as a top-level parse failure.
            for body in heredoc_bodies:
                for tree in bashlex.parse(body):
                    self.walk(tree)


def _collect_paths(commands: list[SimpleCommand], cwd: str) -> list[str]:
    paths: list[str] = []

    def add(p: str) -> None:
        if p not in paths:
            paths.append(p)

    for cmd in commands:
        exe = cmd.argv[0]
        args = cmd.argv[1:]
        for i, tok in enumerate(args):
            if looks_unresolved(tok):
                # Flagged (Flags.has_unresolved_expansion) during word
                # construction already; do not also fabricate a resolved
                # path for it here.
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
        paths = _collect_paths(commands, cwd)
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
        # body, or a urllib ValueError that slipped past extract_domains'
        # own guard. Any of these must fail closed as a whole: a partial
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
