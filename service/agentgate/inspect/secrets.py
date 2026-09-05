"""What looks like a secret value in tool output, and how to hide it.

A secret is recognized by *shape*, never by meaning: the forms below are
token prefixes, header names, PEM fences and the `name=value` line whose
name says what it holds. The replacement keeps the line and the key name
and drops only the value, so an agent reading `.env` still learns which
variables exist.

Two passes keep this linear on 256 KB. The first is over the whole text
in C and decides which lines are worth reading at all: one `str.find` per
literal needle (at most one hit per line -- the search jumps past the
newline after a hit) plus one regex per separator character, `=` and `:`
being what every `name value` form needs and what memchr can hunt for
without entering the matcher. Every hit is turned into a line number by
counting newlines once, cumulatively. Only the lines that were hit are
read in Python; an ordinary log has none, and neither has a line built
out of `token=a` repeated, because no value on it is long enough to be a
secret. The second pass runs, per hit line, only the forms whose needle
actually landed on that line.

The budget bounds the *scan*. Reporting is linear in what was found, and
a 256 KB output whose every line is a secret costs some 5 us a line to
rewrite and record -- ten times the budget, and unreachable by any
arrangement of these passes.

Shannon entropy is the last resort, only for a `name=value` line with a
neutral name, only where a secret is plausible (`entropy_candidates`),
and only as a *candidate*: the classifier may release it. A recognized
form is never a candidate. The whitelist keeps the everyday high-entropy
values -- hashes, ids, paths -- out of the candidate list; the corpus in
tests/inspect/fixtures/secret_false_positives.txt is the contract.

The list of secret *paths* is `shell/secrets.py`'s, reused, not copied.
"""

import base64
import binascii
import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from agentgate.api.schemas import Provenance
from agentgate.inspect.detectors import Action, Finding
from agentgate.inspect.mask import PRIVATE_KEY_REPLACEMENT, SECRET_REPLACEMENT
from agentgate.shell.secrets import is_secret_path

SECRET_RULE = "inspect.secret"

ENTROPY_MIN_LENGTH = 20
ENTROPY_MIN_BITS = 3.5
# Spec 4.1: a sensitive name counts only when it carries a value longer than 8.
NAMED_VALUE_MIN_LENGTH = 9

SENSITIVE_NAME_HINTS = ("token", "secret", "password", "passwd", "api_key", "apikey", "access_key", "credential")
# Neutral names whose values are long and noisy by construction.
IGNORED_KEYS = frozenset({"LS_COLORS", "LSCOLORS", "PS1", "PROMPT", "TERM_SESSION_ID"})

_NAME = r"[A-Za-z_][A-Za-z0-9_.-]*"
_VALUE = r"[^\s\"',;]+"
_LONG_VALUE = r"[^\s\"',;]{" + str(NAMED_VALUE_MIN_LENGTH) + r",}"
# Separator and value as every `name value` form spells them. Horizontal
# space only: `\s` would let the matcher walk over a newline and read the
# next line's key as this line's value, which on a file of short
# `KEY=`-style lines costs more than the whole scan is allowed.
_SEP = r"\"?[ \t]*[:=][ \t]*\"?"


@dataclass(frozen=True)
class Form:
    """One token shape: literal needles, at least one of which must appear
    (lowercased) on a line before the pattern is run against it, and the
    pattern whose `value` group is what gets hidden."""

    needles: tuple[str, ...]
    pattern: re.Pattern[str]
    accept: Callable[[re.Match[str]], bool] = lambda match: True


def _jwt_is_a_token(match: re.Match[str]) -> bool:
    """A JWT, not a word that merely starts with `eyJ`.

    The pattern carries no leading `\\b` -- an anchored assertion costs a
    matcher entry at every position of a line built out of `eyJ.`, three
    times the whole budget -- so the left boundary is checked here, where
    it runs once per candidate instead of once per character.
    """
    subject, start = match.string, match.start("value")
    if start and (subject[start - 1].isalnum() or subject[start - 1] in "_-"):
        return False
    head = match.group("value").split(".", 1)[0]
    try:
        decoded = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4))
    except (binascii.Error, ValueError):
        return False
    return b'"alg"' in decoded


def _names_a_secret(text: str) -> bool:
    """True if `text` carries a word that says what it holds. Asked of a
    key by the form itself, and of a whole line by the prefilter, which
    only needs to know whether running the form on it can pay off."""
    folded = text.lower()
    return any(hint in folded for hint in SENSITIVE_NAME_HINTS)


def _accepts_sensitive_name(match: re.Match[str]) -> bool:
    return _names_a_secret(match.group("key"))


AWS_KEY_ID = Form(("akia", "asia"), re.compile(r"(?P<value>\b(?:AKIA|ASIA)[0-9A-Z]{16}\b)"))
GITHUB_TOKEN = Form(("gh",), re.compile(r"(?P<value>\bgh[opusr]_[A-Za-z0-9]{20,}\b)"))
GITHUB_PAT = Form(("gh",), re.compile(r"(?P<value>\bgithub_pat_[A-Za-z0-9_]{20,}\b)"))
GITLAB_TOKEN = Form(("glpat-",), re.compile(r"(?P<value>\bglpat-[A-Za-z0-9_-]{20,}\b)"))
SK_TOKEN = Form(("sk-",), re.compile(r"(?P<value>\bsk-[A-Za-z0-9_-]{32,}\b)"))
SLACK_TOKEN = Form(("xox",), re.compile(r"(?P<value>\bxox[baprs]-[A-Za-z0-9-]{10,}\b)"))
JWT = Form(
    ("eyj",),
    re.compile(r"(?P<value>eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"),
    _jwt_is_a_token,
)
AUTH_HEADER = Form(
    ("authorization:", "x-api-key:"),
    re.compile(
        r"^\s*[<>*]?\s*(?:proxy-)?(?:authorization|x-api-key)\s*:\s*(?:(?:bearer|basic|token)\s+)?(?P<value>\S.*?)\s*$",
        re.I,
    ),
)
SENSITIVE_NAME = Form(
    SENSITIVE_NAME_HINTS,
    re.compile(r"(?:^|[\s\"'{,(-])(?P<key>" + _NAME + r")(?P<sep>" + _SEP + r")(?P<value>" + _LONG_VALUE + r")"),
    _accepts_sensitive_name,
)

# Every form but the last is reached through a literal needle. The last
# needs a separator as well as a name, and a name alone is far too common
# to prefilter on -- a file of `KEY_1=` lines would hand every one of its
# lines to the matcher -- so it rides the separator prefilter instead.
NEEDLE_FORMS: tuple[Form, ...] = (
    AWS_KEY_ID, GITHUB_TOKEN, GITHUB_PAT, GITLAB_TOKEN, SK_TOKEN, SLACK_TOKEN, JWT, AUTH_HEADER,
)

_PEM_HINT = "-----begin "
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")

_SEP_FLAG = 1
_PEM_FLAG = 2
_FORM_FLAGS: tuple[int, ...] = tuple(1 << (index + 2) for index in range(len(NEEDLE_FORMS)))


def _needle_table() -> tuple[tuple[str, int], ...]:
    """Each needle once, carrying the flags of every form that wants it.
    Two forms share `gh`, and searching 256 KB twice for the same string
    is a fifth of a millisecond spent on nothing."""
    table: dict[str, int] = {_PEM_HINT: _PEM_FLAG}
    for form, flag in zip(NEEDLE_FORMS, _FORM_FLAGS):
        for needle in form.needles:
            table[needle] = table.get(needle, 0) | flag
    return tuple(table.items())


_NEEDLES: tuple[tuple[str, int], ...] = _needle_table()

# The whole-text prefilter for every `name value` form, anchored on the
# separator rather than on the name: a name is an alphabetic run that
# starts almost everywhere, where `=` and `:` are rare and the matcher can
# skip between them without being entered. A line whose separator carries
# no value this long holds neither a named secret nor an entropy
# candidate, whose threshold is higher still. One pattern per separator
# character and not one `[:=]` class, because only a literal first
# character reaches the memchr skip -- a two-character class costs a
# bitmap test per byte, three times the scan over the same 256 KB.
_SEP_HINTS: tuple[re.Pattern[str], ...] = (
    re.compile(r"=[ \t]*\"?" + _LONG_VALUE),
    re.compile(r":[ \t]*\"?" + _LONG_VALUE),
)
_CANDIDATE = re.compile(r"^[ \t]*(?:export[ \t]+)?\"?(?P<key>" + _NAME + r")" + _SEP + r"(?P<value>" + _VALUE + r")")
_HEX = re.compile(r"^[0-9a-fA-F]{7,}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_LOCKFILE_HASH = re.compile(r"^(?:sha(?:1|256|384|512)[-:]|md5[-:])")
_SHELL_ENV_READERS = ("printenv", "env")


def scan_secrets(output: str, *, entropy_candidates: bool) -> list[Finding]:
    """Every line (or PEM range) holding a secret, in line order, with the
    rewrite that hides the value. One finding per line: all forms on the
    line are applied to the same rewrite. Line numbers index
    ``output.split("\\n")``."""
    hits = _hit_lines(output)
    if not hits:
        return []
    lines = output.split("\n")
    findings: list[Finding] = []
    skip_through = -1
    for number, flags in sorted(hits.items()):
        if number <= skip_through:
            continue
        line = lines[number]
        if flags & _PEM_FLAG and _PEM_BEGIN.search(line):
            end = _pem_end(lines, number)
            findings.append(
                Finding(line=number, rule_id=SECRET_RULE, action=Action.redact, line_end=end, rewritten=PRIVATE_KEY_REPLACEMENT)
            )
            skip_through = end
            continue
        finding = _scan_line(number, line, flags, entropy_candidates)
        if finding is not None:
            findings.append(finding)
    return findings


def entropy_candidates_allowed(provenance: Provenance, workspace: str | None) -> bool:
    """Where a neutral-named high-entropy value is plausibly a secret
    (spec 4.1): a secret file, a shell command that prints the
    environment or reads a secret file, anything fetched from the web or
    an MCP server. Source files and lockfiles are where the false
    positives live, so a plain file path says no; `subagent` and
    `unknown` say no too."""
    kind = provenance.kind
    if kind in ("web", "mcp"):
        return True
    if kind == "file":
        return is_secret_path(provenance.path, workspace)
    if kind == "shell":
        return _reads_secrets(provenance.command, workspace)
    return False


def _reads_secrets(command: str, workspace: str | None) -> bool:
    words = command.split()
    if any(word in _SHELL_ENV_READERS for word in words):
        return True
    return any(is_secret_path(word, workspace) for word in words if not word.startswith("-"))


def _hit_lines(output: str) -> dict[int, int]:
    """Line numbers worth reading, each with the flags of what landed on it.

    Lowercasing may change the length of a rare character, never a
    newline, so positions are taken in the lowered text and converted to
    line numbers there -- the count of newlines before a position is the
    same in both strings.
    """
    lowered = output.lower()
    marks: list[tuple[int, int]] = []
    for needle, flag in _NEEDLES:
        position = lowered.find(needle)
        while position != -1:
            marks.append((position, flag))
            newline = lowered.find("\n", position)
            if newline == -1:
                break
            position = lowered.find(needle, newline + 1)
    for pattern in _SEP_HINTS:
        marks.extend((match.start(), _SEP_FLAG) for match in pattern.finditer(lowered))
    if not marks:
        return {}
    marks.sort()
    hits: dict[int, int] = {}
    line = 0
    previous = 0
    for position, flag in marks:
        line += lowered.count("\n", previous, position)
        previous = position
        hits[line] = hits.get(line, 0) | flag
    return hits


def _pem_end(lines: Sequence[str], start: int) -> int:
    for number in range(start, len(lines)):
        if _PEM_END.search(lines[number]):
            return number
    # No END fence: what follows is still key material, so hide it all.
    return len(lines) - 1


def _scan_line(number: int, line: str, flags: int, entropy_candidates: bool) -> Finding | None:
    rewritten = line
    for form, flag in zip(NEEDLE_FORMS, _FORM_FLAGS):
        if flags & flag:
            rewritten = _apply_form(form, rewritten)
    if flags & _SEP_FLAG and _names_a_secret(line):
        rewritten = _apply_form(SENSITIVE_NAME, rewritten)
    if rewritten != line:
        return Finding(line=number, rule_id=SECRET_RULE, action=Action.redact, rewritten=rewritten)
    if entropy_candidates and flags & _SEP_FLAG:
        return _candidate(number, line)
    return None


def _apply_form(form: Form, line: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if not form.accept(match):
            return match.group(0)
        start, end = match.span("value")
        return line[match.start():start] + SECRET_REPLACEMENT + line[end:match.end()]

    return form.pattern.sub(replace, line)


def _candidate(number: int, line: str) -> Finding | None:
    match = _CANDIDATE.match(line)
    if match is None:
        return None
    key, value = match.group("key"), match.group("value")
    if key in IGNORED_KEYS or _names_a_secret(key) or not _looks_random(value):
        return None
    start, end = match.span("value")
    return Finding(
        line=number, rule_id=SECRET_RULE, action=Action.redact,
        rewritten=line[:start] + SECRET_REPLACEMENT + line[end:], candidate_key=key,
    )


def _looks_random(value: str) -> bool:
    if len(value) < ENTROPY_MIN_LENGTH or _whitelisted(value):
        return False
    counts = Counter(value)
    total = len(value)
    entropy = -sum(c / total * math.log2(c / total) for c in counts.values())
    return entropy >= ENTROPY_MIN_BITS


def _whitelisted(value: str) -> bool:
    if _HEX.match(value) or _UUID.match(value) or _LOCKFILE_HASH.match(value) or value.startswith("data:"):
        return True
    return _is_path_list(value)


def _is_path_list(value: str) -> bool:
    parts = value.split(":")
    return all(part.startswith(("/", "./", "~/", ".")) for part in parts)
