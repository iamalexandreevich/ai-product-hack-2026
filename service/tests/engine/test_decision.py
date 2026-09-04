from datetime import datetime, timezone

from agentgate.api.schemas import PROTOCOL, DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Latency
from tests.factories import decide_request, dialogue, turn


def decision(**overrides) -> Decision:
    data = dict(
        id="01J0", ts=datetime.now(timezone.utc), request=decide_request("ls -la"),
        verdict=Verdict.allow("allowlist.readonly"),
        latency=Latency(total_ms=3, stage1_ms=1),
        profile_id="default", profile_hash="h" * 64,
    )
    data.update(overrides)
    return Decision(**data)


def test_response_carries_the_verdict():
    response = decision().to_response()
    assert response.decision is DecisionKind.allow
    assert response.rule_id == "allowlist.readonly"
    assert response.stage == 1


def test_response_decision_id_is_the_decision_id():
    assert decision().to_response().decision_id == "01J0"


def test_response_latency_comes_from_the_measured_stages():
    response = decision().to_response()
    assert response.latency_ms.stage1 == 1 and response.latency_ms.stage2 is None


def test_record_exposes_both_id_and_decision_id():
    dumped = decision().to_record().model_dump(mode="json")
    assert dumped["id"] == "01J0" and dumped["decision_id"] == "01J0"


def test_record_normalized_is_empty_when_nothing_was_normalized():
    assert decision(action=None).to_record().normalized == {}


def test_record_does_not_smuggle_the_cache_key_into_normalized():
    record = decision(cache_key="k" * 64).to_record()
    assert "cache_key" not in record.normalized


def test_record_ts_serializes_as_an_iso_string():
    dumped = decision().to_record().model_dump(mode="json")
    assert isinstance(dumped["ts"], str) and dumped["ts"].startswith(str(datetime.now(timezone.utc).year))


def test_record_carries_protocol_history_digest_and_idempotency_key():
    record = decision(history_digest="d" * 64, idempotency_key="k1").to_record()
    assert record.protocol == PROTOCOL and record.history_digest == "d" * 64 and record.idempotency_key == "k1"


def test_record_history_is_the_fitted_dialogue_the_model_saw():
    seen = dialogue(turn(content="x"), turn(role="toolresult", author="system", content="y", tool="bash", call_id="c1"))
    record = decision(dialogue=seen).to_record()
    assert [t.content for t in record.history] == ["x", "y"]
    assert record.history[1].call_id == "c1"


def test_record_history_is_empty_when_stage_two_did_not_run():
    assert decision(dialogue=None).to_record().history == []


def test_record_to_response_and_decision_to_response_agree():
    d = decision(cached=True)
    assert d.to_record().to_response() == d.to_response()


def test_response_carries_the_protocol():
    assert decision().to_response().protocol == PROTOCOL
