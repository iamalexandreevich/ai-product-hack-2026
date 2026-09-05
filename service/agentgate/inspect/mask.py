"""From findings to a verdict: what the model may see of a flagged result.

`mask` rewrites the flagged lines and keeps the rest; `drop` withholds the
whole result when more than half of it is flagged, or when the result is
one blob with nothing worth keeping. The thresholds are constants, not
profile settings: they describe what a rewrite can still salvage, and
that does not vary by operator. A masked line is replaced wholesale, so a
CRLF line comes back ending in a plain ``\n`` -- the rewrite normalizes
flagged lines rather than preserving their original line ending.
"""

from dataclasses import dataclass

from agentgate.api.schemas import InspectVerdict
from agentgate.inspect.detectors import INVISIBLE_CHARS, Action, Finding

REPLACEMENT_LINE = "[gate: instruction-like text removed]"
SECRET_REPLACEMENT = "[gate: secret redacted]"
PRIVATE_KEY_REPLACEMENT = "[gate: private key redacted]"
DROP_SHARE = 0.5


@dataclass(frozen=True)
class Stage1Outcome:
    verdict: InspectVerdict
    replacement: str | None
    rule_id: str | None
    reason: str


def apply(output: str, findings: list[Finding]) -> Stage1Outcome:
    if not findings:
        return Stage1Outcome(verdict=InspectVerdict.pass_, replacement=None, rule_id=None, reason="")
    flagged = {f.line: f for f in findings}
    lead = findings[0].rule_id
    line_count = _line_count(output)
    # Only `mask` findings remove content; a `clean` finding rewrites the
    # line in place and keeps its meaning, so it does not count toward drop.
    removable = {line for line, f in flagged.items() if f.action is Action.mask}
    if len(removable) > line_count * DROP_SHARE:
        return Stage1Outcome(
            verdict=InspectVerdict.drop,
            replacement=None,
            rule_id=lead,
            reason=f"prompt injection detected in {len(removable)} of {line_count} lines",
        )
    return Stage1Outcome(
        verdict=InspectVerdict.mask,
        replacement=_rewrite(output.split("\n"), flagged),
        rule_id=lead,
        reason=f"rewrote {len(flagged)} line(s) carrying instruction-like or invisible text",
    )


def _line_count(output: str) -> int:
    lines = output.split("\n")
    # A trailing "\n" produces an empty final element with nothing to flag;
    # counting it as a line would understate the share of flagged content.
    return len(lines) - 1 if output.endswith("\n") else len(lines)


def _rewrite(lines: list[str], flagged: dict[int, Finding]) -> str:
    rewritten = []
    for number, line in enumerate(lines):
        finding = flagged.get(number)
        if finding is None:
            rewritten.append(line)
        elif finding.action is Action.clean:
            rewritten.append(INVISIBLE_CHARS.sub("", line))
        else:
            rewritten.append(REPLACEMENT_LINE)
    return "\n".join(rewritten)
