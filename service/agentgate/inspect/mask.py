"""From findings to a verdict: what the model may see of a flagged result.

`mask` rewrites the flagged lines and keeps the rest; `drop` withholds the
whole result when more than half of it is flagged, or when the result is
one blob with nothing worth keeping. The thresholds are constants, not
profile settings: they describe what a rewrite can still salvage, and
that does not vary by operator.
"""

from dataclasses import dataclass

from agentgate.api.schemas import InspectVerdict
from agentgate.inspect.detectors import INVISIBLE_CHARS, Action, Finding

REPLACEMENT_LINE = "[gate: instruction-like text removed]"
DROP_SHARE = 0.5


@dataclass(frozen=True)
class Stage1Outcome:
    verdict: InspectVerdict
    replacement: str | None
    rule_id: str | None
    reason: str


def apply(output: str, findings: list[Finding]) -> Stage1Outcome:
    if not findings:
        return Stage1Outcome(InspectVerdict.pass_, None, None, "")
    lines = output.split("\n")
    # A trailing "\n" produces an empty final element with nothing to flag;
    # counting it as a line would understate the share of flagged content.
    line_count = len(lines) - 1 if output.endswith("\n") else len(lines)
    flagged = {f.line: f for f in findings}
    lead = findings[0].rule_id
    # Only `mask` findings remove content; a `clean` finding rewrites the
    # line in place and keeps its meaning, so it does not count toward drop.
    removable = {line for line, f in flagged.items() if f.action is Action.mask}
    if len(removable) > line_count * DROP_SHARE:
        return Stage1Outcome(
            InspectVerdict.drop, None, lead,
            f"prompt injection detected in {len(removable)} of {line_count} lines",
        )
    rewritten = []
    for number, line in enumerate(lines):
        finding = flagged.get(number)
        if finding is None:
            rewritten.append(line)
        elif finding.action is Action.clean:
            rewritten.append(INVISIBLE_CHARS.sub("", line))
        else:
            rewritten.append(REPLACEMENT_LINE)
    return Stage1Outcome(
        InspectVerdict.mask, "\n".join(rewritten), lead,
        f"rewrote {len(flagged)} line(s) that tried to instruct the model",
    )
