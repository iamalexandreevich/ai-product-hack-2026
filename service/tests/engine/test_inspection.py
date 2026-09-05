from agentgate.api.schemas import Cost, InspectVerdict
from tests.factories import inspection


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
    from tests.factories import inspect_request
    from agentgate.engine.timings import Latency

    original = inspection(stage=2, model="m", cost=Cost(input_tokens=100, output_tokens=20))
    hit = original.as_cached("01J1", inspect_request(), Latency(total_ms=0), "/w")
    assert hit.cost is None
    assert "cost" not in hit.to_response().model_dump()
