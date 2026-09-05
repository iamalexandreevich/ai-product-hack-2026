"""What the model's spans must satisfy before the server applies them.

The model may only narrow what the agent sees, and only within what it
was shown: a span outside the segments it received has nothing to stand
on, a span wider than a segment could not have been read whole, `secret`
is not its call. A span that fails is discarded and counted, never
turned into an error -- the rest of the answer still holds. Overlaps of
one kind merge; of different kinds, the later one loses. The drop
threshold (spec 4.5 condition 7) is not checked here: validated spans
become `mask` findings and `mask.apply` counts them like any other.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from agentgate.inspect.classify import ModelSpan
from agentgate.inspect.detectors import Action, Finding
from agentgate.inspect.mask import SEMANTIC_RULE
from agentgate.inspect.segments import Segments
from agentgate.profiles.schema import ModelBudget

MODEL_SPAN_KINDS = ("instruction", "pipe-exec", "encoded", "invisible")


@dataclass(frozen=True)
class SpanValidation:
    findings: tuple[Finding, ...]
    rejected: int


def validate(spans: Sequence[ModelSpan], line_count: int, segments: Segments, budget: ModelBudget) -> SpanValidation:
    accepted = [s for s in spans if _well_formed(s, line_count, segments, budget)]
    rejected = len(spans) - len(accepted)
    merged, dropped = _merge(sorted(accepted, key=lambda s: (s.line_start, s.line_end)))
    findings = tuple(
        Finding(line=s.line_start, rule_id=SEMANTIC_RULE, action=Action.mask, line_end=s.line_end, kind=s.kind, confidence=s.confidence)
        for s in merged
    )
    return SpanValidation(findings=findings, rejected=rejected + dropped)


def _well_formed(span: ModelSpan, line_count: int, segments: Segments, budget: ModelBudget) -> bool:
    if not 0 <= span.line_start <= span.line_end < line_count:
        return False
    if span.line_end - span.line_start + 1 > budget.segment_max_lines:
        return False
    if span.kind not in MODEL_SPAN_KINDS:
        return False
    if not 0.0 <= span.confidence <= 1.0:
        return False
    return segments.covers(span.line_start, span.line_end)


def _merge(spans: list[ModelSpan]) -> tuple[list[ModelSpan], int]:
    kept: list[ModelSpan] = []
    dropped = 0
    for span in spans:
        if kept and span.line_start <= kept[-1].line_end:
            previous = kept[-1]
            if previous.kind == span.kind:
                kept[-1] = ModelSpan(
                    line_start=previous.line_start, line_end=max(previous.line_end, span.line_end),
                    kind=span.kind, confidence=max(previous.confidence, span.confidence),
                )
            else:
                dropped += 1
            continue
        kept.append(span)
    return kept, dropped
