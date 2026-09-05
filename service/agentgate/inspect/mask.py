"""From findings to a verdict: what the model may see of a flagged result.

`mask` rewrites the flagged lines and keeps the rest; `drop` withholds the
whole result when more than half of it is flagged, or when the result is
one blob with nothing worth keeping. The thresholds are constants, not
profile settings: they describe what a rewrite can still salvage, and
that does not vary by operator. A masked line is replaced wholesale, so a
CRLF line comes back ending in a plain ``\n`` -- the rewrite normalizes
flagged lines rather than preserving their original line ending.

Findings may come from detectors, the secret scanner and the model's
validated spans at once, and may overlap on a line. `resolve` keeps one
per line by precedence `redact > clean > mask`: a value stays hidden even
when the model asked to mask the whole line. Only `mask` lines remove
content, so only they count toward the drop threshold -- a `.env` of
thirty redacted values is exactly the result the agent must keep.
"""

from dataclasses import dataclass

from agentgate.api.schemas import InspectVerdict, Span, SpanKind
from agentgate.inspect.detectors import INVISIBLE_CHARS, Action, Finding

REPLACEMENT_LINE = "[gate: instruction-like text removed]"
SECRET_REPLACEMENT = "[gate: secret redacted]"
PRIVATE_KEY_REPLACEMENT = "[gate: private key redacted]"
DROP_SHARE = 0.5
SEMANTIC_RULE = "inspect.semantic"

_PRECEDENCE = {Action.redact: 0, Action.clean: 1, Action.mask: 2}
_KIND_BY_RULE: dict[str, SpanKind] = {
    "inspect.injection": "instruction",
    "inspect.pipe-exec": "pipe-exec",
    "inspect.encoded": "encoded",
    "inspect.invisible": "invisible",
    "inspect.secret": "secret",
}


@dataclass(frozen=True)
class Stage1Outcome:
    verdict: InspectVerdict
    replacement: str | None
    rule_id: str | None
    reason: str
    spans: tuple[Span, ...] = ()
    redacted: int = 0


def apply(output: str, findings: list[Finding]) -> Stage1Outcome:
    if not findings:
        return Stage1Outcome(verdict=InspectVerdict.pass_, replacement=None, rule_id=None, reason="")
    kept = resolve(findings)
    by_line = _by_line(kept)
    line_count = _line_count(output)
    removable = {line for line, f in by_line.items() if f.action is Action.mask}
    lead = _lead(kept)
    if len(removable) > line_count * DROP_SHARE:
        return Stage1Outcome(
            verdict=InspectVerdict.drop,
            replacement=None,
            rule_id=lead,
            reason=f"prompt injection detected in {len(removable)} of {line_count} lines",
        )
    redacted = sum(1 for f in kept if f.action is Action.redact)
    return Stage1Outcome(
        verdict=InspectVerdict.mask,
        replacement=_rewrite(output.split("\n"), by_line),
        rule_id=lead,
        reason=_reason(by_line, redacted),
        spans=tuple(_span(f) for f in kept),
        redacted=redacted,
    )


def resolve(findings: list[Finding]) -> list[Finding]:
    """One finding per line by precedence, in line order.

    A finding that loses every one of its lines is dropped; one that keeps
    at least one line is kept whole -- its span still reports the range it
    was asked for, and `_by_line` decides what each line actually gets.

    Winners are identified by `id()`, not by value: a detector and the
    model can report the same range with the same kind, and two equal
    findings must stay two findings rather than collapse into one.
    """
    by_line = _by_line(findings)
    winners = {id(f) for f in by_line.values()}
    return sorted(
        (f for f in findings if id(f) in winners),
        key=lambda f: (f.line, _PRECEDENCE[f.action]),
    )


def redacted_lines(lines: list[str], findings: list[Finding]) -> list[str]:
    """`lines` with only the `redact` findings applied, line count preserved.

    The first line of a redacted range carries the rewrite, the rest of
    the range become empty: the prompt and the stored `raw` keep the
    coordinates of `output.split("\\n")`, so spans point at the same lines
    in both. Collapsing the range is `apply`'s job, for the answer only.
    """
    by_line = _by_line([f for f in findings if f.action is Action.redact])
    result = []
    for number, line in enumerate(lines):
        finding = by_line.get(number)
        if finding is None:
            result.append(line)
        elif number == finding.line:
            result.append(finding.rewritten or "")
        else:
            result.append("")
    return result


def _by_line(findings: list[Finding]) -> dict[int, Finding]:
    by_line: dict[int, Finding] = {}
    for finding in findings:
        for number in range(finding.line, finding.last + 1):
            current = by_line.get(number)
            if current is None or _PRECEDENCE[finding.action] < _PRECEDENCE[current.action]:
                by_line[number] = finding
    return by_line


def _lead(kept: list[Finding]) -> str:
    masks = [f for f in kept if f.action is Action.mask]
    return (masks or kept)[0].rule_id


def _line_count(output: str) -> int:
    lines = output.split("\n")
    # A trailing "\n" produces an empty final element with nothing to flag;
    # counting it as a line would understate the share of flagged content.
    return len(lines) - 1 if output.endswith("\n") else len(lines)


def _rewrite(lines: list[str], by_line: dict[int, Finding]) -> str:
    rewritten = []
    for number, line in enumerate(lines):
        finding = by_line.get(number)
        if finding is None:
            rewritten.append(line)
        elif finding.action is Action.clean:
            rewritten.append(INVISIBLE_CHARS.sub("", line))
        elif finding.action is Action.redact:
            # A multi-line redaction (a PEM block) collapses to its first line.
            if number == finding.line:
                rewritten.append(finding.rewritten or "")
        else:
            rewritten.append(REPLACEMENT_LINE)
    return "\n".join(rewritten)


def _reason(by_line: dict[int, Finding], redacted: int) -> str:
    rewritten = sum(1 for f in by_line.values() if f.action is not Action.redact)
    parts = []
    if rewritten:
        parts.append(f"rewrote {rewritten} line(s) carrying instruction-like or invisible text")
    if redacted:
        parts.append(f"redacted {redacted} secret value(s)")
    return "; ".join(parts)


def _span(finding: Finding) -> Span:
    if finding.rule_id == SEMANTIC_RULE:
        return Span(
            line_start=finding.line,
            line_end=finding.last,
            kind=finding.kind or "instruction",
            source="model",
            confidence=finding.confidence,
        )
    return Span(
        line_start=finding.line,
        line_end=finding.last,
        kind=_KIND_BY_RULE.get(finding.rule_id, "instruction"),
        source="detector",
    )
