from pathlib import Path

from dataset.validator import validate_dataset


def test_policy_suite_is_separate_and_asserts_routes():
    root = Path(__file__).resolve().parents[1]
    report = validate_dataset(root / "attacks/policy", require_full_categories=False)
    assert report.ok, report.errors
    assert len(report.cases) >= 12
    assert all(c.rules is not None and c.enforce_pipeline for c in report.cases)
    assert all(c.expected_stage == 1 and c.expected_rule_id_prefix for c in report.cases)
    assert {c.expected_service_result.value for c in report.cases} == {"allow", "ask", "deny"}
    assert all(c.dataset_source.value == "team" for c in report.cases)
