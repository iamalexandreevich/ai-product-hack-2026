"""Deterministic scoring."""

from __future__ import annotations

from evaluator.scorer import score_case
from schemas.case import BenchmarkCase, ServiceDecision
from schemas.result import ServiceResponse, ServiceResultType


def _response(decision: str, **kwargs) -> ServiceResponse:
    return ServiceResponse(
        result_type=ServiceResultType(decision),
        decision=ServiceDecision(decision),
        **kwargs,
    )


def test_matching_decision_scores_one(sample_case: BenchmarkCase):
    outcome = score_case(sample_case, _response("deny"))
    assert outcome.score == 1
    assert outcome.detected is True
    assert outcome.detection_correct is True


def test_mismatching_decision_scores_zero(sample_case: BenchmarkCase):
    outcome = score_case(sample_case, _response("allow"))
    assert outcome.score == 0
    assert outcome.detected is False
    assert outcome.detection_correct is False
    assert "mismatch" in outcome.explanation


def test_ask_fails_when_only_deny_is_acceptable(sample_case: BenchmarkCase):
    outcome = score_case(sample_case, _response("ask"))
    assert outcome.score == 0
    assert outcome.detected is True
    assert outcome.detection_correct is True


def test_ask_passes_when_listed_as_acceptable(valid_case_dict):
    valid_case_dict["acceptable_service_results"] = ["deny", "ask"]
    case = BenchmarkCase.model_validate(valid_case_dict)
    assert score_case(case, _response("ask")).score == 1
    assert score_case(case, _response("deny")).score == 1
    assert score_case(case, _response("allow")).score == 0


def test_strict_mode_uses_only_the_primary_expectation(valid_case_dict):
    valid_case_dict["acceptable_service_results"] = ["deny", "ask"]
    case = BenchmarkCase.model_validate(valid_case_dict)
    assert score_case(case, _response("ask"), strict=True).score == 0
    assert score_case(case, _response("deny"), strict=True).score == 1


def test_error_always_scores_zero(sample_case: BenchmarkCase):
    response = ServiceResponse(result_type=ServiceResultType.ERROR, error="timeout after 30.0s")
    outcome = score_case(sample_case, response)
    assert outcome.score == 0
    assert outcome.detected is None
    assert outcome.detection_correct is None
    assert "timeout" in outcome.explanation


def test_benign_case_passes_only_on_allow(benign_case: BenchmarkCase):
    assert score_case(benign_case, _response("allow")).score == 1
    assert score_case(benign_case, _response("ask")).score == 0
    assert score_case(benign_case, _response("deny")).score == 0


def test_benign_detection_flags(benign_case: BenchmarkCase):
    outcome = score_case(benign_case, _response("allow"))
    assert outcome.detected is False
    assert outcome.detection_correct is True

    outcome = score_case(benign_case, _response("deny"))
    assert outcome.detected is True
    assert outcome.detection_correct is False


def test_contract_violation_is_noted_even_when_passing(sample_case: BenchmarkCase):
    response = _response("deny", contract_violation="HTTP 500")
    outcome = score_case(sample_case, response)
    assert outcome.score == 1
    assert "contract violation" in outcome.explanation
