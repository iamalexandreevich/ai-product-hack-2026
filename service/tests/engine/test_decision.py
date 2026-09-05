from datetime import datetime, timezone

import pytest

from agentgate.api.schemas import PROTOCOL, DecisionKind
from agentgate.domain.dialogue import Dialogue
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision, WrongRecordKind
from agentgate.engine.timings import Latency
from tests.factories import decide_request, dialogue, rule_set, session_state, turn


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
    assert response.latency_ms.stage1 == 1
    assert response.latency_ms.stage2 is None


def test_record_exposes_both_id_and_decision_id():
    dumped = decision().to_record().model_dump(mode="json")
    assert dumped["id"] == "01J0"
    assert dumped["decision_id"] == "01J0"


def test_record_normalized_is_empty_when_nothing_was_normalized():
    assert decision(action=None).to_record().normalized == {}


def test_record_does_not_smuggle_the_cache_key_into_normalized():
    record = decision(cache_key="k" * 64).to_record()
    assert "cache_key" not in record.normalized


def test_record_ts_serializes_as_an_iso_string():
    dumped = decision().to_record().model_dump(mode="json")
    assert isinstance(dumped["ts"], str)
    assert dumped["ts"].startswith(str(datetime.now(timezone.utc).year))


def test_record_carries_protocol_history_digest_and_idempotency_key():
    record = decision(history_digest="d" * 64, idempotency_key="k1").to_record()
    assert record.protocol == PROTOCOL
    assert record.history_digest == "d" * 64
    assert record.idempotency_key == "k1"


def test_record_history_is_the_fitted_dialogue_the_model_saw():
    seen = dialogue(turn(content="x"), turn(role="toolresult", author="system", content="y", tool="bash", call_id="c1"))
    record = decision(dialogue=seen).to_record()
    assert [t.content for t in record.history] == ["x", "y"]
    assert record.history[1].call_id == "c1"


def test_record_history_is_empty_when_stage_two_did_not_run():
    assert decision(dialogue=None).to_record().history == []


def test_record_history_omitted_is_the_dialogues_omitted_count():
    seen = Dialogue(turns=(turn(content="x"),), omitted=3)
    record = decision(dialogue=seen).to_record()
    assert record.history_omitted == 3


def test_record_history_omitted_is_zero_when_stage_two_did_not_run():
    assert decision(dialogue=None).to_record().history_omitted == 0


def test_response_is_built_from_the_verdict_and_the_latency():
    d = decision(verdict=Verdict.deny("hard-deny.exfil", "leak", "use env", stage=1, hard=True), cached=False)
    response = d.to_response()
    assert (response.decision, response.rule_id, response.reason, response.suggest) == (
        DecisionKind.deny, "hard-deny.exfil", "leak", "use env",
    )
    assert (response.stage, response.latency_ms.stage1, response.latency_ms.total, response.decision_id) == (1, 1, 3, "01J0")


def test_response_carries_the_protocol():
    assert decision().to_response().protocol == PROTOCOL


def test_record_kind_defaults_to_decide_and_carries_call_id_and_rules():
    record = decision(request=decide_request("ls -la", call_id="c9", rules=rule_set().model_dump())).to_record()
    assert record.kind == "decide"
    assert record.call_id == "c9"
    assert record.rules_level == "medium"
    assert len(record.rules_digest) == 64
    plain = decision().to_record()
    assert plain.rules_level is None
    assert plain.rules_digest is None
    assert plain.provenance is None
    assert plain.replacement is None


def test_to_inspect_response_on_a_decide_record_raises_wrong_record_kind():
    record = decision().to_record()
    assert record.kind == "decide"
    with pytest.raises(WrongRecordKind):
        record.to_inspect_response()


def test_allow_cache_entry_is_the_session_and_key_of_a_fresh_allow_only():
    assert decision(state=session_state(), cache_key="k" * 64).allow_cache_entry() == ("s1", "k" * 64)
    assert decision(state=session_state(), cache_key="k" * 64, cached=True).allow_cache_entry() is None
    assert decision(state=None, cache_key="k" * 64).allow_cache_entry() is None
    assert decision(
        state=session_state(), cache_key="k" * 64, verdict=Verdict.deny("x", "r")
    ).allow_cache_entry() is None
