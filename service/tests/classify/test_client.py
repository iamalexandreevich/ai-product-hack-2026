import json

import httpx
import pytest

from agentgate.profiles.schema import ModelConfig
from agentgate.classify.client import LLMClient, Stage2Error


def make_client(handler, structured=True, timeout_ms=1000):
    cfg = ModelConfig(base_url="http://llm/v1", model="q", api_key_env="TEST_KEY", timeout_ms=timeout_ms, structured_output=structured)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LLMClient("q", cfg, http)


def ok_body(content: str) -> dict:
    return {"id": "x", "choices": [{"message": {"role": "assistant", "content": content}}]}


async def test_structured_request_and_parse(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "k")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body(json.dumps({"decision": "D", "risk": "supply_chain", "reason": "r", "suggest": "s"})))

    out, raw = await make_client(handler).classify("sys", "usr")
    assert out.decision == "D" and out.risk == "supply_chain"
    assert seen["url"] == "http://llm/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "q"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["body"]["messages"][1] == {"role": "user", "content": "usr"}
    assert raw["id"] == "x"


async def test_text_mode_has_no_response_format():
    def handler(request):
        body = json.loads(request.content)
        assert "response_format" not in body
        return httpx.Response(200, json=ok_body('{"decision":"A"}'))

    out, _ = await make_client(handler, structured=False).classify("s", "u")
    assert out.decision == "A" and out.risk == "none"


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
async def test_http_errors(status):
    def handler(request):
        return httpx.Response(status, json={"error": "x"})

    with pytest.raises(Stage2Error) as e:
        await make_client(handler).classify("s", "u")
    assert e.value.kind == "http"


async def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(Stage2Error) as e:
        await make_client(handler).classify("s", "u")
    assert e.value.kind == "timeout"


@pytest.mark.parametrize("content,kind", [
    ("not json", "invalid_json"),
    ('{"decision":"X"}', "invalid_schema"),
    ('{"reason":"no decision"}', "invalid_schema"),
    ("", "empty"),
])
async def test_bad_content(content, kind):
    def handler(request):
        return httpx.Response(200, json=ok_body(content))

    with pytest.raises(Stage2Error) as e:
        await make_client(handler).classify("s", "u")
    assert e.value.kind == kind


async def test_missing_choices_is_empty():
    def handler(request):
        return httpx.Response(200, json={"id": "x"})

    with pytest.raises(Stage2Error) as e:
        await make_client(handler).classify("s", "u")
    assert e.value.kind == "empty"


async def test_content_as_list_of_parts_is_invalid_schema_not_unexpected():
    # A real OpenAI-compatible shape some providers use: content as a list of
    # typed parts instead of a plain string. content.strip() on a list raises
    # AttributeError, which must not escape classify() as an "unexpected"
    # failure — it is a response that violates our schema expectation.
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": [{"type": "text", "text": "hi"}]}}]})

    with pytest.raises(Stage2Error) as e:
        await make_client(handler).classify("s", "u")
    assert e.value.kind == "invalid_schema"


async def test_redirect_status_is_http_not_invalid_json():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://elsewhere"})

    with pytest.raises(Stage2Error) as e:
        await make_client(handler).classify("s", "u")
    assert e.value.kind == "http"
