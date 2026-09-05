from agentgate.api.schemas import InspectVerdict
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
    assert record.normalized == {"tool_name": "bash", "status": "completed", "provenance": record.provenance}


def test_pass_has_no_output_and_empty_reason():
    response = inspection().to_response()
    assert response.verdict is InspectVerdict.pass_
    assert response.output is None
    assert response.reason == ""
