"""Offline integration check of benchmark requests and policy cases against service code.

Run: uv run --with-editable ../service python tools/check_service.py
No HTTP, model, database, or recorded command is executed.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent / "service")]


def check() -> dict[str, int]:
    from agentgate.api.schemas import DecideRequest, InspectRequest
    from agentgate.domain.client_rules import ClientRules
    from agentgate.domain.policy import Policy
    from agentgate.normalize import normalize
    from agentgate.profiles.loader import load_profiles
    from agentgate.rules.chain import STAGE1

    from client.inspect import build_inspect_request
    from client.security_service import SecurityServiceClient, build_decide_request
    from config import ServiceConfig
    from dataset.inspect_loader import load_inspections
    from dataset.loader import load_dataset

    profile = load_profiles(ROOT.parent / "service" / "profiles")["default"]
    counts = {"decide_requests": 0, "inspect_requests": 0, "policy_cases": 0}
    for suite in ("cases", "policy"):
        for case in load_dataset(ROOT / "attacks" / suite):
            request = DecideRequest.model_validate(
                build_decide_request(
                    human_req=case.human_req,
                    assistant_tool_call=case.assistant_tool_call,
                    harness="bench",
                    history=case.history,
                    rules=case.rules,
                    call_id=case.call_id,
                )
            )
            counts["decide_requests"] += 1
            if suite != "policy":
                continue
            policy = Policy.bind(profile, request.args.cwd, ClientRules.of(request.rules))
            verdict = STAGE1.run(normalize(request), policy).settled()
            if verdict is None or (
                verdict.decision.value != case.expected_service_result.value
                or not (verdict.rule_id or "").startswith(case.expected_rule_id_prefix or "")
            ):
                raise AssertionError(f"{case.id}: policy regression: {verdict!r}")
            counts["policy_cases"] += 1
    client = SecurityServiceClient(ServiceConfig(url="http://127.0.0.1:8400"))
    for case in load_inspections(ROOT / "attacks" / "inspect"):
        if not case.api_refusal:
            InspectRequest.model_validate(
                build_inspect_request(case, client, session_id="check", call_id=case.id)
            )
            counts["inspect_requests"] += 1
    return counts


if __name__ == "__main__":
    import json

    print(json.dumps(check(), indent=2))
