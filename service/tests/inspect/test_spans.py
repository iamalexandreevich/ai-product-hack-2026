from agentgate.inspect.classify import ModelSpan
from agentgate.inspect.detectors import Action
from agentgate.inspect.segments import Segment, Segments
from agentgate.inspect.spans import validate
from agentgate.profiles.schema import ModelBudget
from tests.factories import model_span

ALL = Segments(items=(Segment(start=0, end=99, lines=tuple("x" for _ in range(100))),))
BUDGET = ModelBudget(segment_max_lines=200)


def test_a_valid_span_becomes_a_semantic_mask_finding():
    v = validate([model_span(3, 5, "pipe-exec", 0.6)], 100, ALL, BUDGET)
    assert v.rejected == 0
    f = v.findings[0]
    assert (f.line, f.last, f.rule_id, f.action, f.kind, f.confidence) == (3, 5, "inspect.semantic", Action.mask, "pipe-exec", 0.6)


def test_out_of_bounds_and_inverted_spans_are_rejected():
    v = validate([model_span(-1, 0), model_span(5, 3), model_span(99, 100)], 100, ALL, BUDGET)
    assert v.findings == ()
    assert v.rejected == 3


def test_a_span_wider_than_a_segment_is_rejected():
    v = validate([model_span(0, 10)], 100, ALL, ModelBudget(segment_max_lines=5))
    assert (v.findings, v.rejected) == ((), 1)


def test_overlapping_spans_of_one_kind_merge():
    v = validate([model_span(2, 5, confidence=0.5), model_span(4, 8, confidence=0.9)], 100, ALL, BUDGET)
    assert [(f.line, f.last, f.confidence) for f in v.findings] == [(2, 8, 0.9)]
    assert v.rejected == 0


def test_overlapping_spans_of_different_kinds_keep_the_earlier():
    v = validate([model_span(4, 8, "encoded"), model_span(2, 5, "instruction")], 100, ALL, BUDGET)
    assert [(f.line, f.last, f.kind) for f in v.findings] == [(2, 5, "instruction")]
    assert v.rejected == 1


def test_a_span_outside_the_segments_the_model_saw_is_rejected():
    seen = Segments(items=(Segment(start=10, end=20, lines=tuple("x" for _ in range(11))),))
    v = validate([model_span(15, 16), model_span(19, 21), model_span(0, 0)], 100, seen, BUDGET)
    assert [(f.line, f.last) for f in v.findings] == [(15, 16)]
    assert v.rejected == 2


def test_unknown_kinds_and_secret_are_rejected():
    v = validate([model_span(1, 1, "secret"), model_span(2, 2, "rude")], 100, ALL, BUDGET)
    assert (v.findings, v.rejected) == ((), 2)


def test_confidence_outside_the_unit_interval_is_rejected():
    v = validate([model_span(1, 1, confidence=1.5), model_span(2, 2, confidence=-0.1)], 100, ALL, BUDGET)
    assert (v.findings, v.rejected) == ((), 2)


def test_findings_come_back_in_line_order():
    v = validate([model_span(50, 50), model_span(3, 3)], 100, ALL, BUDGET)
    assert [f.line for f in v.findings] == [3, 50]
