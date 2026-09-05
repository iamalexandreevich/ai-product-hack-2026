"""What stage 2's answer is allowed to change about stage 1's verdict.

The caps, in the order they are applied (spec 4.7):

- a stage-1 `drop` stands whatever the model says;
- `pass` lifts every `mask` finding at once -- the model answers about
  the result as a whole -- but never a `clean` or a `redact`, and when
  one of those survives, so does stage 1's reason for it: the model
  argued for `pass`, not for the mask that remains;
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
from agentgate.inspect.detectors import INJECTION, PIPE_EXEC, Finding
from agentgate.inspect.mask import SEMANTIC_RULE, Stage1Outcome, apply
from agentgate.inspect.segments import Segments
from agentgate.inspect.spans import validate
from agentgate.profiles.schema import ModelBudget

EMPTY_SPANS = "empty-spans"
LIFTABLE_RULES = (INJECTION.id, PIPE_EXEC.id)


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
        return _drop(stage1, (
            f"stage 1 drop threshold stands despite model disagreement "
            f"({answer.reason}): {stage1.reason}"
        ))
    if answer.verdict is InspectVerdict.drop:
        return Reconciled(InspectVerdict.drop, None, answer.reason, stage1.rule_id or SEMANTIC_RULE, (), 0, 0)
    if answer.verdict is InspectVerdict.pass_:
        return _lifted(output, kept, answer.reason)
    validated = validate(answer.spans, len(output.split("\n")), segments, budget)
    if not validated.findings:
        return Reconciled(
            stage1.verdict, stage1.replacement, stage1.reason, stage1.rule_id, stage1.spans, stage1.redacted,
            validated.rejected, error=EMPTY_SPANS,
        )
    return _from_findings(output, kept + list(validated.findings), answer.reason, rejected=validated.rejected)


def _lifted(output: str, kept: list[Finding], model_reason: str) -> Reconciled:
    """What survives a `pass`: everything but the findings the model is
    allowed to overrule -- instruction-like text and pipe-to-shell, the
    two calls a detector can get wrong. An encoded blob, a `clean` and a
    `redact` stay; they are described the way stage 1 described them.
    Only when nothing survives does the model's sentence become the
    reason -- it is then the reason the result passed."""
    outcome = _from_findings(output, [f for f in kept if f.rule_id not in LIFTABLE_RULES], "", rejected=0)
    if outcome.verdict is InspectVerdict.pass_:
        return replace(outcome, reason=model_reason)
    return outcome


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
