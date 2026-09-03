import json

import httpx

from agentgate.api.schemas import DecideRequest, DecisionKind
from agentgate.normalize import normalize
from agentgate.stage2.client import LLMClient
from agentgate.stage2.run import run_stage2
from tests.test_stage2_prompt import P, WS


def action():
    return normalize(DecideRequest(harness="t", tool="shell", raw="npm install lodahs", args={"cwd": WS}, user_request="x"))


def unparseable_action():
    # A single unterminated double-quote: bashlex cannot structure this at
    # all, so normalize_shell sets flags.unparseable and leaves
    # commands/paths/domains empty.
    a = normalize(DecideRequest(harness="t", tool="shell", raw='echo "unterminated', args={"cwd": WS}, user_request="x"))
    assert a.flags.unparseable is True  # sanity: this really is the unparseable path
    return a


def counting_client():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"decision":"A"}'}}]})

    name, cfg = P.models.model_config_for(None)
    return LLMClient(name, cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))), calls


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


async def test_unparseable_action_short_circuits_without_calling_llm():
    stage2_client, calls = counting_client()
    res = await run_stage2(unparseable_action(), "task", P, "m", stage2_client, "note")
    assert res.decision is DecisionKind.ask
    assert calls["n"] == 0  # no HTTP request was made — the action was refused before any prompt was built
    assert res.model == "m"
    assert res.raw_response is None
    assert res.error is None  # not a Stage2Error: a policy refusal, not a classifier failure
    assert "unparse" in res.reason.lower() or "not verified" in res.reason.lower() or "never verified" in res.reason.lower()
