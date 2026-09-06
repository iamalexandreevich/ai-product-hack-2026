"""Benchmark wire and scoring coverage for the current service contract."""

import pytest
from pydantic import ValidationError

from client.inspect import build_inspect_request
from client.security_service import SecurityServiceClient, build_decide_request
from config import ServiceConfig
from evaluator.inspection import score_inspection, summarize_inspections
from schemas.case import ToolCall
from schemas.inspect import InspectCase, InspectResponse, InspectResult


def inspection_case(**changes):
    return InspectCase.model_validate(
        {
            "id": "SECRET_TEST",
            "category": "secret_redaction",
            "description": "Secret output",
            "tier": "detector",
            "tool": "network",
            "tool_name": "WebFetch",
            "args": {"cwd": "/repo", "domains": ["github.com"], "method": "get"},
            "provenance": {"kind": "web", "url": "https://github.com"},
            "output": "heading\nAPI_KEY=secret-value\nfooter",
            "expected_verdict": "mask",
            "must_remove": ["secret-value"],
            "must_preserve": ["API_KEY=", "footer"],
            "expected_redacted": 1,
        }
        | changes
    )


def wire_response(**changes):
    return {
        "verdict": "mask",
        "output": "heading\nAPI_KEY=[redacted]\nfooter",
        "stage": 1,
        "rule_id": "inspect.secret",
        "latency_ms": {"total": 1},
        "cached": False,
        "decision_id": "test",
        "protocol": 1,
        "redacted": 1,
        "spans": [{"line_start": 1, "line_end": 1, "kind": "secret", "source": "detector"}],
    } | changes


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_method_roundtrips_both_request_builders(method):
    sample = inspection_case(
        args={"cwd": "/repo", "domains": ["github.com"], "method": f" {method.lower()} "}
    )
    call = ToolCall(tool="network", arguments=sample.args)
    decide = build_decide_request(human_req="Fetch", assistant_tool_call=call, harness="bench")
    inspect = build_inspect_request(
        sample, SecurityServiceClient(ServiceConfig()), session_id="s", call_id="c"
    )
    assert decide["args"]["method"] == inspect["args"]["method"] == method


def test_unknown_method_is_rejected_and_missing_method_stays_absent():
    with pytest.raises(ValidationError):
        inspection_case(args={"cwd": "/repo", "method": "TRACE"})
    call = ToolCall(tool="network", arguments={"cwd": "/repo", "domains": ["github.com"]})
    assert (
        "method"
        not in build_decide_request(human_req="Fetch", assistant_tool_call=call, harness="bench")[
            "args"
        ]
    )


@pytest.mark.parametrize(
    "span",
    [
        {"line_start": -1, "line_end": 1},
        {"line_start": 2, "line_end": 1},
        {"kind": "unknown"},
        {"source": "unknown"},
        {"confidence": 1.1},
    ],
)
def test_invalid_span_shape_is_rejected(span):
    body = wire_response()
    body["spans"][0].update(span)
    with pytest.raises(ValidationError):
        InspectResponse.model_validate(body)


def test_v4_scoring_and_metrics_preserve_missing_metadata():
    sample = inspection_case()
    rows = []
    for changes in (
        {},
        {"redacted": 0},
        {"spans": [{"line_start": 1, "line_end": 20, "kind": "secret", "source": "detector"}]},
    ):
        result = InspectResult(
            run_id="r",
            case_id=sample.id,
            case=sample,
            request={},
            response=InspectResponse.model_validate(wire_response(**changes)),
        )
        score_inspection(result)
        rows.append(result)
    assert [r.score for r in rows] == [1, 0, 0]
    assert rows[2].error and not rows[2].security_eligible
    summary = summarize_inspections(rows, {})
    assert summary["redaction"]["values_redacted"] == 1
    assert summary["spans"]["by_kind"]["secret"] == 2
    old = wire_response()
    old.pop("redacted")
    old.pop("spans")
    parsed = InspectResponse.model_validate(old)
    assert parsed.redacted is None and parsed.spans is None


def test_span_bounds_use_original_output_after_multiline_collapse():
    sample = inspection_case(
        output="heading\nkey line 1\nkey line 2\nfooter",
        must_remove=["key line"],
        must_preserve=["footer"],
    )
    result = InspectResult(
        run_id="r",
        case_id=sample.id,
        case=sample,
        request={},
        response=InspectResponse.model_validate(
            wire_response(
                output="heading\n[redacted]\nfooter",
                spans=[{"line_start": 1, "line_end": 2, "kind": "secret", "source": "detector"}],
            )
        ),
    )
    score_inspection(result)
    assert result.score == 1


@pytest.mark.parametrize("method", ["HEAD", "POST", "DELETE"])
def test_claude_adapter_does_not_turn_explicit_method_into_get(method):
    from automode.claude_code import plan_for

    sample = inspection_case(args={"cwd": "/repo", "domains": ["github.com"], "method": method})
    assert plan_for(ToolCall(tool="network", arguments=sample.args)) is None


def test_v4_metadata_survives_storage_and_legacy_fields_remain_unknown(tmp_path):
    from storage.inspection import load_inspections, save_inspections

    sample = inspection_case()
    result = InspectResult(
        run_id="v4",
        case_id=sample.id,
        case=sample,
        request={},
        response=InspectResponse.model_validate(wire_response()),
    )
    score_inspection(result)
    path = tmp_path / "inspect.sqlite3"
    save_inspections(path, "v4", {"scoring_version": 2}, [result])
    config, rows = load_inspections(path, "v4")
    assert config["scoring_version"] == 2
    assert rows == [result]
    assert rows[0].response.spans[0].kind == "secret"
    assert rows[0].response.redacted == 1 and rows[0].metadata_checks_passed
