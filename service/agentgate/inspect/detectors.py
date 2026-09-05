"""What looks like an instruction aimed at the model, in one table.

Each detector is a rule id, a set of compiled patterns and what to do
with a line that matches: `mask` replaces the line, `clean` strips the
offending characters and keeps the line. The table is the single place
this knowledge lives; the classifier, when enabled, only re-judges what a
detector found.

Detectors read tool output as text. That is the one place the service
reads attacker-controlled prose as is -- and it never *executes* any of
it; it only decides whether the model may see it.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class Action(Enum):
    mask = "mask"
    clean = "clean"
    redact = "redact"


@dataclass(frozen=True)
class Detector:
    """A rule id, its patterns, and the cheap tests that gate them.

    `hints` and `precheck` are both prechecks run before the regex table.
    Invariant: either may only rule out a line the patterns provably cannot
    match, never the reverse -- a hint may only exclude lines none of the
    detector's patterns can match. `hints` covers the common case: casefolded
    substrings, and a line whose casefolded form contains none of them is
    skipped. `precheck` covers what a substring test cannot express (length,
    character class). `scan` never consults a detector for an empty line,
    so a detector must not rely on matching the empty string.

    `extra` is an escape hatch for a check a single regex cannot express
    without reintroducing the backtracking it was built to avoid -- the
    HTML-comment and long-run checks below use it because a `.*` between
    two literals, or an unbounded `{n,}` on a character class, both cost
    quadratic time on an adversarial line built to exploit them.
    """

    id: str
    patterns: tuple[re.Pattern[str], ...]
    action: Action
    hints: tuple[str, ...] = ()
    precheck: Callable[[str], bool] = field(default=lambda line: True)
    extra: Callable[[str], bool] = field(default=lambda line: False)

    def matches(self, line: str) -> bool:
        if self.hints and not any(hint in line.casefold() for hint in self.hints):
            return False
        if not self.precheck(line):
            return False
        if any(p.search(line) for p in self.patterns):
            return True
        return self.extra(line)


@dataclass(frozen=True)
class Finding:
    """One flagged range of lines. `line` is a 0-based index into
    `output.split("\\n")`; `line_end` (inclusive) defaults to `line`.

    `rewritten` is what a `redact` finding puts in place of its first line
    (the value replaced, the key name kept); the rest of a multi-line
    range is collapsed. `candidate_key` marks an entropy-only secret
    candidate the classifier may release, and names the key the prompt
    shows for it. `kind` and `confidence` are set on spans the model
    returned; detector findings derive their kind from `rule_id`.
    """

    line: int
    rule_id: str
    action: Action
    line_end: int | None = None
    rewritten: str | None = None
    candidate_key: str | None = None
    kind: str | None = None
    confidence: float | None = None

    @property
    def last(self) -> int:
        return self.line if self.line_end is None else self.line_end


# The word a `<!--...-->` comment must hide, checked only against the text
# between one comment's delimiters (see `_html_comment_hides_a_hint`) -- never
# against the whole line, which is what made the old single-regex version of
# this check quadratic on a line built out of many `<!--` with no `-->`.
_HTML_COMMENT_HINT = re.compile(r"\b(ignore|instructions?|обязательно|инструкци)\b", re.I)


def _html_comment_hides_a_hint(line: str) -> bool:
    """Linear replacement for the single pattern
    ``<!--.*\\b(ignore|instructions?|...)\\b.*-->``.

    That pattern is quadratic on a line with many ``<!--`` and no closing
    ``-->``: `.search` retries the greedy `.*` at every occurrence of
    ``<!--``, and each retry re-scans to the end of the line before giving
    up. Locating each comment's boundaries first with bounded `str.find`
    calls costs O(n) total -- `start` only ever moves forward -- and the
    word check then runs on a slice already known to be inside a comment.

    This is deliberately narrower than the regex it replaces: a hint between
    two separate comments (`<!-- a --> ignore <!-- b -->`) is visible text,
    not hidden, and no longer counts.
    """
    start = 0
    while True:
        open_idx = line.find("<!--", start)
        if open_idx == -1:
            return False
        close_idx = line.find("-->", open_idx + 4)
        if close_idx == -1:
            return False
        if _HTML_COMMENT_HINT.search(line, open_idx + 4, close_idx):
            return True
        start = close_idx + 3


INJECTION = Detector(
    id="inspect.injection",
    patterns=(
        re.compile(r"\bi\W*g\W*n\W*o\W*r\W*e\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
        re.compile(r"\bdisregard\s+(all\s+)?(prior|previous|above)\s+instructions", re.I),
        re.compile(r"\byou\s+are\s+now\s+(in\s+)?\w+\s+mode\b", re.I),
        re.compile(r"\bdo\s+not\s+(mention|tell|reveal)\s+(this|it)\s+to\s+the\s+user", re.I),
        re.compile(r"^\s*(system|assistant)\s*:", re.I),
        re.compile(r"игнорируй\s+(все\s+)?предыдущие\s+инструкции", re.I),
        re.compile(r"не\s+сообщай\s+(об\s+этом\s+)?пользователю", re.I),
    ),
    action=Action.mask,
    hints=(
        "ignor",
        "instruction",
        "disregard",
        "mode",
        "user",
        "system",
        "assistant",
        "<!--",
        "инструкци",
        "сообщай",
    ),
    extra=_html_comment_hides_a_hint,
)

PIPE_EXEC = Detector(
    id="inspect.pipe-exec",
    patterns=(re.compile(r"\b(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(sh|bash|zsh|python3?|node)\b", re.I),),
    action=Action.mask,
    hints=("|",),
)

# A run of 513+ characters is checked by finding every *maximal* run of
# candidate characters (`+`, not `{513,}`) and testing its length: `+`
# matches greedily starting at the first candidate character and never
# retries a partial run, so the whole line costs O(n). `{513,}` instead
# retries the count at every position, which is O(n) per position on a
# line built from runs just under the threshold -- quadratic-ish for a
# large adversarial line even though it never backtracks exponentially.
_ENCODED_RUN = re.compile(r"[A-Za-z0-9+/=]+")


def _has_long_encoded_run(line: str) -> bool:
    return any(m.end() - m.start() >= 513 for m in _ENCODED_RUN.finditer(line))


ENCODED = Detector(
    id="inspect.encoded",
    patterns=(),
    action=Action.mask,
    precheck=lambda line: len(line) >= 513,
    extra=_has_long_encoded_run,
)

# Zero-width space/joiners/LRM/RLM (U+200B-U+200F), bidi overrides
# (U+202A-U+202E), word joiner and invisible operators (U+2060-U+2064),
# a stray BOM (U+FEFF), bidi isolates (U+2066-U+2069), soft hyphen
# (U+00AD), and Unicode tag characters (U+E0000-U+E007F).
INVISIBLE_CHARS = re.compile(
    "[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\U000e0000-\U000e007f]"
)

INVISIBLE = Detector(
    id="inspect.invisible",
    patterns=(INVISIBLE_CHARS,),
    action=Action.clean,
    precheck=lambda line: not line.isascii(),
)


def scan(output: str, detectors: tuple[Detector, ...]) -> list[Finding]:
    """Every line a detector flags, in line order; one finding per line, the first detector that matched.

    An empty line is skipped before any detector runs: no detector's hints,
    precheck or patterns can match one (`ENCODED` needs length, `INVISIBLE`
    needs a non-ASCII character, the rest need a hint substring), so the
    per-detector overhead on a line that is output split on many `\\n`s in a
    row -- with nothing else in it -- is pure waste.
    """
    findings: list[Finding] = []
    for number, line in enumerate(output.split("\n")):
        if not line:
            continue
        for detector in detectors:
            if detector.matches(line):
                findings.append(Finding(line=number, rule_id=detector.id, action=detector.action))
                break
    return findings
