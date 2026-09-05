import json

import httpx
import pytest

from agentgate.profiles.schema import ModelConfig
from agentgate.classify.client import LLMClient, Stage2Error
from agentgate.classify.schema import DECIDE_STRUCTURED_OUTPUT
from agentgate.domain.usage import Usage


def make_client(handler, structured=True, timeout_ms=1000):
    cfg = ModelConfig(base_url="http://llm/v1", model="q", api_key_env="TEST_KEY", timeout_ms=timeout_ms, structured_output=structured)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LLMClient("q", cfg, http, DECIDE_STRUCTURED_OUTPUT)


def ok_body(content: str, usage: dict | None = None) -> dict:
    body = {"id": "x", "choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return body


async def test_structured_request_and_parse(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "k")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body(json.dumps({"decision": "D", "risk": "supply_chain", "reason": "r", "suggest": "s"})))

    out, raw, usage = await make_client(handler).classify("sys", "usr")
    assert out.decision == "D" and out.risk == "supply_chain"
    assert seen["url"] == "http://llm/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "q"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["name"] == "agentgate_decision"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["body"]["messages"][1] == {"role": "user", "content": "usr"}
    assert raw["id"] == "x"
    assert usage is None


async def test_usage_is_parsed_from_the_response():
    def handler(request):
        return httpx.Response(200, json=ok_body(
            json.dumps({"decision": "A"}), usage={"prompt_tokens": 812, "completion_tokens": 41},
        ))

    _out, _raw, usage = await make_client(handler).classify("s", "u")
    assert usage == Usage(input_tokens=812, output_tokens=41, reasoning_tokens=0)


async def test_reasoning_tokens_are_parsed_when_present():
    def handler(request):
        return httpx.Response(200, json=ok_body(
            json.dumps({"decision": "A"}),
            usage={
                "prompt_tokens": 10, "completion_tokens": 5,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        ))

    _out, _raw, usage = await make_client(handler).classify("s", "u")
    assert usage.reasoning_tokens == 3


async def test_missing_usage_is_none_not_zeros():
    def handler(request):
        return httpx.Response(200, json=ok_body(json.dumps({"decision": "A"})))

    _out, _raw, usage = await make_client(handler).classify("s", "u")
    assert usage is None


async def test_text_mode_has_no_response_format():
    def handler(request):
        body = json.loads(request.content)
        assert "response_format" not in body
        return httpx.Response(200, json=ok_body('{"decision":"A"}'))

    out, _, _usage = await make_client(handler, structured=False).classify("s", "u")
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


async def test_structured_output_max_tokens_reaches_the_request_body():
    from agentgate.classify.client import StructuredOutput

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body(json.dumps({"decision": "A", "risk": "none", "reason": "", "suggest": ""})))

    cfg = ModelConfig(base_url="http://llm/v1", model="q")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    so = StructuredOutput(name="t", schema=DECIDE_STRUCTURED_OUTPUT.schema, model=DECIDE_STRUCTURED_OUTPUT.model, max_tokens=777)
    await LLMClient("m", cfg, http, so).classify("s", "u")
    assert seen["body"]["max_tokens"] == 777


def test_structured_output_max_tokens_defaults_to_300():
    assert DECIDE_STRUCTURED_OUTPUT.max_tokens == 300
