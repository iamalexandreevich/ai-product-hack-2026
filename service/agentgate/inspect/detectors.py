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
    id: str
    patterns: tuple[re.Pattern[str], ...]
    action: Action
    precheck: Callable[[str], bool] = field(default=lambda line: True)
    """Cheap test run before the regex table. Must return True whenever the
    regexes could possibly match -- it may only rule out lines the regexes
    provably cannot match, never the reverse."""

    def matches(self, line: str) -> bool:
        if not self.precheck(line):
            return False
        return any(p.search(line) for p in self.patterns)


@dataclass(frozen=True)
class Finding:
    line: int
    rule_id: str
    action: Action


_INJECTION_HINTS = (
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
)


def _has_injection_hint(line: str) -> bool:
    lowered = line.lower()
    return any(hint in lowered for hint in _INJECTION_HINTS)


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
    precheck=_has_injection_hint,
)

PIPE_EXEC = Detector(
    id="inspect.pipe-exec",
    patterns=(re.compile(r"\b(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(sh|bash|zsh|python3?|node)\b", re.I),),
    action=Action.mask,
    precheck=lambda line: "|" in line,
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
INVISIBLE = Detector(
    id="inspect.invisible",
    patterns=(
        re.compile(
            "[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\U000e0000-\U000e007f]"
        ),
    ),
    action=Action.clean,
    precheck=lambda line: not line.isascii(),
)

INVISIBLE_CHARS = INVISIBLE.patterns[0]


def scan(output: str, detectors: tuple[Detector, ...]) -> list[Finding]:
    """Every line a detector flags, in line order; one finding per line, the first detector that matched."""
    findings: list[Finding] = []
    for number, line in enumerate(output.split("\n")):
        for detector in detectors:
            if detector.matches(line):
                findings.append(Finding(line=number, rule_id=detector.id, action=detector.action))
                break
    return findings
