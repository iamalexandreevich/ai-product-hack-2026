import json
from pathlib import Path

from agentgate.api.schemas import DecideRequest, DecideResponse

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def test_request_schema_matches_contract():
    committed = json.loads((CONTRACTS / "decide_request.schema.json").read_text())
    assert committed == DecideRequest.model_json_schema()


def test_response_schema_matches_contract():
    committed = json.loads((CONTRACTS / "decide_response.schema.json").read_text())
    assert committed == DecideResponse.model_json_schema()
