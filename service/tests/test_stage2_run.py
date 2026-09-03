import json

import httpx

from agentgate.api.schemas import DecideRequest, DecisionKind
from agentgate.normalize import normalize
from agentgate.stage2.client import LLMClient
from agentgate.stage2.run import run_stage2
from tests.test_stage2_prompt import P, WS


def action():
    return normalize(DecideRequest(harness="t", tool="shell", raw="npm install lodahs", args={"cwd": WS}, user_request="x"))


def client(handler):
    name, cfg = P.models.model_config_for(None)
    return LLMClient(name, cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def reply(payload):
    return lambda r: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


async def test_mapping_A_D_U():
    for letter, expected in (("A", DecisionKind.allow), ("D", DecisionKind.deny), ("U", DecisionKind.ask)):
        res = await run_stage2(action(), "task", P, "m", client(reply({"decision": letter, "reason": "why", "suggest": "alt"})), "note")
        assert res.decision is expected
        assert res.model == "m" and res.error is None and res.raw_response is not None
        if letter != "A":
            assert res.reason == "why" and res.suggest == "alt"


async def test_failure_is_ask_with_error():
    res = await run_stage2(action(), "task", P, "m", client(lambda r: httpx.Response(500)), "note")
    assert res.decision is DecisionKind.ask
    assert res.error == "http"
    assert res.reason.startswith("classifier unavailable")
    assert res.raw_response is None


async def test_unexpected_exception_is_ask():
    def boom(r):
        raise RuntimeError("weird")

    res = await run_stage2(action(), "task", P, "m", client(boom), "note")
    assert res.decision is DecisionKind.ask and res.error == "unexpected"
