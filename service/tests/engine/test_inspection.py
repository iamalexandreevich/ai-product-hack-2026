from agentgate.api.schemas import Cost, InspectVerdict, Span
from agentgate.engine.timings import Latency
from tests.factories import inspect_request, inspection


def test_inspection_response_and_record_share_one_source():
    i = inspection(verdict=InspectVerdict.mask, replacement="x", rule_id="inspect.injection", reason="r")
    response = i.to_response()
    assert response.verdict is InspectVerdict.mask
    assert response.output == "x"
    assert response.decision_id == i.id
    record = i.to_record()
    assert record.kind == "inspect"
    assert record.decision is InspectVerdict.mask
    assert record.replacement == "x"
    assert record.raw == i.request.output
    assert record.call_id == "c1"
    assert record.provenance == {"kind": "shell", "command": "git status"}
    assert record.normalized == {"tool_name": "bash", "status": "completed"}


def test_pass_has_no_output_and_empty_reason():
    response = inspection().to_response()
    assert response.verdict is InspectVerdict.pass_
    assert response.output is None
    assert response.reason == ""


def test_response_cost_is_absent_when_stage2_did_not_run():
    response = inspection().to_response()
    assert "cost" not in response.model_dump()


def test_record_and_response_carry_the_cost_when_stage2_ran():
    cost = Cost(input_tokens=100, output_tokens=20)
    i = inspection(stage=2, model="m", cost=cost)
    assert i.to_record().cost.input_tokens == 100
    assert i.to_response().model_dump()["cost"]["input_tokens"] == 100


def test_a_cache_hit_carries_no_cost():
    original = inspection(stage=2, model="m", cost=Cost(input_tokens=100, output_tokens=20))
    hit = original.as_cached("01J1", inspect_request(), Latency(total_ms=0), "/w")
    assert hit.cost is None
    assert "cost" not in hit.to_response().model_dump()


def test_record_and_response_carry_spans_and_redaction_count():
    spans = (Span(line_start=1, line_end=1, kind="secret", source="detector"),)
    i = inspection(verdict=InspectVerdict.mask, replacement="K=[gate: secret redacted]\n", spans=spans, redacted=1, spans_rejected=2)
    record = i.to_record()
    assert record.spans == list(spans)
    assert (record.redacted, record.spans_rejected) == (1, 2)
    response = i.to_response()
    assert response.spans == list(spans)
    assert response.redacted == 1
    assert "spans_rejected" not in response.model_dump()


def test_raw_is_the_redacted_text_when_a_secret_was_found():
    i = inspection(request=inspect_request("K=hunter2hunter2\n"), verdict=InspectVerdict.mask, replacement="K=[gate: secret redacted]\n", redacted=1, redacted_output="K=[gate: secret redacted]\n")
    record = i.to_record()
    assert record.raw == "K=[gate: secret redacted]\n"
    assert "hunter2" not in record.model_dump_json()


def test_raw_is_the_original_when_nothing_was_redacted():
    assert inspection().to_record().raw == inspection().request.output


def test_a_cache_hit_keeps_spans_redaction_and_redacted_text_but_not_rejections():
    spans = (Span(line_start=0, line_end=0, kind="secret", source="detector"),)
    original = inspection(verdict=InspectVerdict.mask, replacement="r", spans=spans, redacted=1, spans_rejected=3, redacted_output="r")
    hit = original.as_cached("01J1", inspect_request(), Latency(total_ms=0), "/w")
    assert hit.spans == spans
    assert hit.redacted == 1
    assert hit.redacted_output == "r"
    assert hit.spans_rejected == 0


def test_an_inspection_carries_its_key_id_into_the_record():
    assert inspection(key_id="01HZKEY").to_record().key_id == "01HZKEY"


def test_a_cache_hit_does_not_inherit_the_key_id_of_the_call_that_filled_it():
    hit = inspection(key_id="01HZKEY").as_cached(
        "01HZNEW", inspect_request(), Latency(total_ms=1), "/w"
    )

    assert hit.key_id is None
