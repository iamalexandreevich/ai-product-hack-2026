"""Validate real benchmark payloads against the service's generated JSON Schemas."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from client.inspect import build_inspect_request
from client.security_service import SecurityServiceClient, build_decide_request
from config import ServiceConfig
from dataset.inspect_loader import load_inspections
from dataset.loader import load_dataset
from schemas.case import ToolCall

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT.parent / "contracts"


def validator(name):
    schema = json.loads((CONTRACTS / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("suite", ["cases", "policy"])
def test_decide_datasets_match_generated_contract(suite):
    contract = validator("decide_request")
    cases = load_dataset(ROOT / "attacks" / suite)
    assert cases
    for case in cases:
        body = build_decide_request(
            human_req=case.human_req,
            assistant_tool_call=case.assistant_tool_call,
            harness="bench",
            history=case.history,
            rules=case.rules,
            call_id=case.call_id,
        )
        contract.validate(body)


def test_inspect_dataset_matches_generated_contract():
    contract = validator("inspect_request")
    client = SecurityServiceClient(ServiceConfig(url="http://127.0.0.1:8400"))
    for case in load_inspections(ROOT / "attacks" / "inspect"):
        if not case.api_refusal:
            contract.validate(
                build_inspect_request(case, client, session_id="test", call_id=case.id)
            )


def test_contract_check_detects_missing_required_fields():
    # Exercise the same validators used above, rather than trusting sample fixtures.
    assert list(validator("decide_request").iter_errors({"tool": "shell"}))
    assert list(validator("inspect_request").iter_errors({"verdict": "pass"}))


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "DELETE", None])
def test_network_methods_match_generated_contract(method):
    call = ToolCall(
        tool="network", arguments={"cwd": "/repo", "domains": ["github.com"], "method": method}
    )
    body = build_decide_request(
        human_req="Read the project", assistant_tool_call=call, harness="bench"
    )
    validator("decide_request").validate(body)
