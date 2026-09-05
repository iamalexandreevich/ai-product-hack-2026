"""V3 inspect wire transport, using the same authenticated HTTP client as decide."""

from typing import Any

import httpx

from client.security_service import SecurityServiceClient
from schemas.inspect import InspectCase, InspectResponse


def build_inspect_request(
    case: InspectCase,
    client: SecurityServiceClient,
    *,
    session_id: str,
    call_id: str,
    send_history: bool = True,
) -> dict[str, Any]:
    body = {
        "session_id": session_id,
        "call_id": call_id,
        "harness": client.config.harness,
        "tool": case.tool.value,
        "tool_name": case.tool_name,
        "status": case.status,
        "output": case.output,
        "provenance": case.provenance.model_dump(mode="json"),
        "args": case.args.model_dump(mode="json"),
        "user_request": case.user_request,
        "profile_id": client.config.profile_id,
        "protocol": 1,
        "history": [t.model_dump(mode="json") for t in case.history] if send_history else [],
    }
    body.update(case.request_overrides)
    return body


async def inspect(
    client: SecurityServiceClient,
    body: dict[str, Any],
) -> tuple[InspectResponse | None, dict[str, Any], str | None]:
    raw: dict[str, Any] = {}
    try:
        http = await client.client.post(client.config.inspect_url, json=body)
        payload = http.json()
        raw = payload if isinstance(payload, dict) else {}
        if http.status_code != 200:
            return None, raw, f"HTTP {http.status_code}"
        return InspectResponse.model_validate(payload), raw, None
    except (httpx.HTTPError, ValueError) as exc:
        return None, raw, f"inspect transport/contract error: {exc}"
