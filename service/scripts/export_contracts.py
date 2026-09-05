"""Regenerate JSON schemas in ../contracts from pydantic models."""
import json
from pathlib import Path

from agentgate.api.schemas import DecideRequest, DecideResponse, InspectRequest, InspectResponse

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def main() -> None:
    for name, model in (
        ("decide_request", DecideRequest),
        ("decide_response", DecideResponse),
        ("inspect_request", InspectRequest),
        ("inspect_response", InspectResponse),
    ):
        path = CONTRACTS / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
