"""Shell command normalization via bashlex AST walking.

Turns a raw shell string into structured SimpleCommand entries plus
extracted paths/domains and risk flags. Never makes any allow/deny
decision here — this module only produces facts for later stages.

Fail-closed: if bashlex cannot parse the input (or raises anything else
while tokenizing), we do not silently return "nothing found" — we set
flags.unparseable=True with empty commands/paths/domains so stage 1/2 can
escalate on the unparseable flag instead of treating it as a safe no-op.
"""

import re

import bashlex
import bashlex.errors

from agentgate.api.schemas import Tool
from agentgate.normalize.domains import extract_domains
from agentgate.normalize.model import Flags, NormalizedAction, Redirect, SimpleCommand
from agentgate.normalize.paths import looks_like_path, resolve_path

# Commands whose non-flag arguments are always paths.
PATH_COMMANDS = {
    "rm", "cp", "mv", "cat", "ls", "mkdir", "rmdir", "touch", "chmod", "chown", "find",
    "shred", "tee", "head", "tail", "less", "more", "stat", "du", "tar", "unzip", "zip",
    "sed", "awk", "wc", "grep", "rg", "ln", "truncate", "dd", "cd",
}
_EVAL_LIKE = {"eval", "exec", "source", "."}
_VAR = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")


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
            if part.kind == "commandsubstitution":
                self.flags.has_subst = True
                self.walk(part.command)
            elif part.kind == "parameter":
                m = _VAR.match(value)
                if m and m.group(1) in self.assignments:
                    value = self.assignments[m.group(1)]
                    self.flags.has_env_assign = True
        return value

    def _command(self, node, pipeline_id: int) -> None:
        argv: list[str] = []
        redirects: list[Redirect] = []
        stdin_from: str | None = None
        for part in node.parts:
            if part.kind == "assignment":
                name, _, val = part.word.partition("=")
                self.assignments[name] = val
                self.flags.has_env_assign = True
            elif part.kind == "word":
                argv.append(self._word_value(part))
            elif part.kind == "redirect":
                if not hasattr(part.output, "word"):  # e.g. 2>&1 duplicates a descriptor, no file
                    continue
                target = self._word_value(part.output)
                target_path = target if target.startswith("/dev/") else resolve_path(target, self.cwd)
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


def _collect_paths(commands: list[SimpleCommand], cwd: str) -> list[str]:
    paths: list[str] = []

    def add(p: str) -> None:
        if p not in paths:
            paths.append(p)

    for cmd in commands:
        exe = cmd.argv[0]
        args = cmd.argv[1:]
        for i, tok in enumerate(args):
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
            if not r.target.startswith("/dev/"):
                add(r.target)
    return paths


def normalize_shell(raw: str, cwd: str) -> NormalizedAction:
    action = NormalizedAction(tool=Tool.shell, cwd=cwd, raw=raw)
    try:
        trees = bashlex.parse(raw)
    except Exception:
        # bashlex raises several distinct tokenizer/parser error types
        # (bashlex.errors.ParsingError and others) for malformed input;
        # treat any of them as fail-closed rather than silently omitting
        # the construct we could not parse.
        action.flags.unparseable = True
        return action
    walker = _Walker(cwd)
    for tree in trees:
        walker.walk(tree)
    action.commands = walker.commands
    action.flags = walker.flags
    action.paths = _collect_paths(action.commands, cwd)
    domains: list[str] = []
    for c in action.commands:
        for d in extract_domains(c.argv):
            if d not in domains:
                domains.append(d)
    action.domains = domains
    return action
