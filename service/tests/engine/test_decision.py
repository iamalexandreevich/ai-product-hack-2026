from datetime import datetime, timezone

from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Latency
from tests.factories import decide_request


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


def test_view_exposes_both_id_and_decision_id():
    dumped = decision().to_view().model_dump(mode="json")
    assert dumped["id"] == "01J0" and dumped["decision_id"] == "01J0"


def test_view_normalized_is_empty_when_nothing_was_normalized():
    assert decision(action=None).to_view().normalized == {}


def test_view_does_not_smuggle_the_cache_key_into_normalized():
    view = decision(cache_key="k" * 64).to_view()
    assert "cache_key" not in view.normalized


def test_view_ts_serializes_as_an_iso_string():
    dumped = decision().to_view().model_dump(mode="json")
    assert isinstance(dumped["ts"], str) and dumped["ts"].startswith(str(datetime.now(timezone.utc).year))
