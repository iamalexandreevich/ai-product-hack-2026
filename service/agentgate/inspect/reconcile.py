"""What stage 2's answer is allowed to change about stage 1's verdict.

The caps, in the order they are applied (spec 4.7):

- a stage-1 `drop` stands whatever the model says;
- `pass` lifts every `mask` finding at once -- the model answers about
  the result as a whole -- but never a `clean` or a `redact`;
- `mask` may only *add*: the model's validated spans join stage 1's
  findings, and `mask.apply` decides the text and the drop threshold from
  the merged set; a `mask` with no usable span is a contradiction and
  reads as a stage-2 error;
- `unredact` releases an entropy candidate and nothing else.

Everything here is a pure function of the output, the findings and the
answer; the engine only decides whether to ask.
"""

from dataclasses import dataclass, replace

from agentgate.api.schemas import InspectVerdict, Span
from agentgate.inspect.classify import InspectOutcome
from agentgate.inspect.detectors import Action, Finding
from agentgate.inspect.mask import Stage1Outcome, apply
from agentgate.inspect.segments import Segments
from agentgate.inspect.spans import validate
from agentgate.profiles.schema import ModelBudget

EMPTY_SPANS = "empty-spans"


@dataclass(frozen=True)
class Reconciled:
    verdict: InspectVerdict
    replacement: str | None
    reason: str
    rule_id: str | None
    spans: tuple[Span, ...]
    redacted: int
    spans_rejected: int
    error: str | None = None


def reconcile(
    output: str, findings: list[Finding], stage1: Stage1Outcome, answer: InspectOutcome,
    segments: Segments, budget: ModelBudget,
) -> Reconciled:
    kept = _without_released_candidates(findings, answer.unredact)
    if stage1.verdict is InspectVerdict.drop and answer.verdict is InspectVerdict.pass_:
        return _drop(stage1, f"stage 1 drop threshold stands despite model disagreement ({answer.reason}): {stage1.reason}")
    if answer.verdict is InspectVerdict.drop:
        return Reconciled(InspectVerdict.drop, None, answer.reason, stage1.rule_id, (), 0, 0)
    if answer.verdict is InspectVerdict.pass_:
        return _from_findings(output, [f for f in kept if f.action is not Action.mask], answer.reason, rejected=0)
    validated = validate(answer.spans, len(output.split("\n")), segments, budget)
    if not validated.findings:
        outcome = _from_findings(output, kept, stage1.reason, rejected=validated.rejected)
        return replace(outcome, error=EMPTY_SPANS)
    return _from_findings(output, kept + list(validated.findings), answer.reason, rejected=validated.rejected)


def _without_released_candidates(findings: list[Finding], unredact: tuple[int, ...]) -> list[Finding]:
    released = set(unredact)
    return [f for f in findings if not (f.candidate_key is not None and f.line in released)]


def _from_findings(output: str, findings: list[Finding], reason: str, rejected: int) -> Reconciled:
    outcome = apply(output, findings)
    if outcome.verdict is InspectVerdict.pass_:
        return Reconciled(InspectVerdict.pass_, None, reason, None, (), 0, rejected)
    if outcome.verdict is InspectVerdict.drop:
        return Reconciled(InspectVerdict.drop, None, outcome.reason, outcome.rule_id, (), 0, rejected)
    return Reconciled(
        InspectVerdict.mask, outcome.replacement, reason if reason else outcome.reason, outcome.rule_id,
        outcome.spans, outcome.redacted, rejected,
    )


def _drop(stage1: Stage1Outcome, reason: str) -> Reconciled:
    return Reconciled(InspectVerdict.drop, None, reason, stage1.rule_id, (), 0, 0)
