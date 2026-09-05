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


@dataclass(frozen=True)
class Detector:
    """A rule id, its patterns, and the cheap tests that gate them.

    `hints` and `precheck` are both prechecks run before the regex table.
    Invariant: either may only rule out a line the patterns provably cannot
    match, never the reverse -- a hint may only exclude lines none of the
    detector's patterns can match. `hints` covers the common case: casefolded
    substrings, and a line whose casefolded form contains none of them is
    skipped. `precheck` covers what a substring test cannot express (length,
    character class).
    """

    id: str
    patterns: tuple[re.Pattern[str], ...]
    action: Action
    hints: tuple[str, ...] = ()
    precheck: Callable[[str], bool] = field(default=lambda line: True)

    def matches(self, line: str) -> bool:
        if self.hints and not any(hint in line.casefold() for hint in self.hints):
            return False
        if not self.precheck(line):
            return False
        return any(p.search(line) for p in self.patterns)


@dataclass(frozen=True)
class Finding:
    """One flagged line. `line` is a 0-based index into `output.split("\\n")`."""

    line: int
    rule_id: str
    action: Action


INJECTION = Detector(
    id="inspect.injection",
    patterns=(
        re.compile(r"\bi\W*g\W*n\W*o\W*r\W*e\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
        re.compile(r"\bdisregard\s+(all\s+)?(prior|previous|above)\s+instructions", re.I),
        re.compile(r"\byou\s+are\s+now\s+(in\s+)?\w+\s+mode\b", re.I),
        re.compile(r"\bdo\s+not\s+(mention|tell|reveal)\s+(this|it)\s+to\s+the\s+user", re.I),
        re.compile(r"^\s*(system|assistant)\s*:", re.I),
        # scan() is per-line, so re.S here would be dead weight: multi-line
        # HTML comments are not matched, by design.
        re.compile(r"<!--.*\b(ignore|instructions?|обязательно|инструкци)\b.*-->", re.I),
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
)

PIPE_EXEC = Detector(
    id="inspect.pipe-exec",
    patterns=(re.compile(r"\b(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(sh|bash|zsh|python3?|node)\b", re.I),),
    action=Action.mask,
    hints=("|",),
)

ENCODED = Detector(
    id="inspect.encoded",
    patterns=(re.compile(r"[A-Za-z0-9+/=]{513,}"),),
    action=Action.mask,
    precheck=lambda line: len(line) >= 513,
)

# Zero-width space/joiners/LRM/RLM (U+200B-U+200F), bidi overrides
# (U+202A-U+202E), word joiner and invisible operators (U+2060-U+2064),
# a stray BOM (U+FEFF), bidi isolates (U+2066-U+2069), soft hyphen
# (U+00AD), and Unicode tag characters (U+E0000-U+E007F).
INVISIBLE_CHARS = re.compile(
    "[­​-‏‪-‮⁠-⁩﻿\U000e0000-\U000e007f]"
)

INVISIBLE = Detector(
    id="inspect.invisible",
    patterns=(INVISIBLE_CHARS,),
    action=Action.clean,
    precheck=lambda line: not line.isascii(),
)


def scan(output: str, detectors: tuple[Detector, ...]) -> list[Finding]:
    """Every line a detector flags, in line order; one finding per line, the first detector that matched."""
    findings: list[Finding] = []
    for number, line in enumerate(output.split("\n")):
        for detector in detectors:
            if detector.matches(line):
                findings.append(Finding(line=number, rule_id=detector.id, action=detector.action))
                break
    return findings
