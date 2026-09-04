import json

import httpx
import pytest

from agentgate.api.schemas import DecideRequest, DecisionKind
from agentgate.classify.base import ReviewCase
from agentgate.classify.llm import LLMClassifier, build_classifiers
from agentgate.domain.dialogue import Dialogue
from agentgate.normalize import normalize
from tests.classify.test_prompt import P, WS


def action():
    return normalize(DecideRequest(harness="t", tool="shell", raw="npm install lodahs", args={"cwd": WS}, user_request="x"))


def case(dialogue: Dialogue = Dialogue()) -> ReviewCase:
    return ReviewCase.build(action(), "task", dialogue, P, "note")


def classifier(handler) -> LLMClassifier:
    name, cfg = P.profile.models.model_config_for(None)
    return LLMClassifier(name, cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def reply(payload):
    return lambda r: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})


@pytest.mark.parametrize(
    ("letter", "expected"),
    [("A", DecisionKind.allow), ("D", DecisionKind.deny), ("U", DecisionKind.ask)],
    ids=["allow", "deny", "uncertain"],
)
async def test_mapping_A_D_U(letter, expected):
    res = await classifier(reply({"decision": letter, "reason": "why", "suggest": "alt"})).classify(case())
    assert res.decision is expected
    assert res.model == "m" and res.error is None and res.raw_response is not None


@pytest.mark.parametrize("letter", ["D", "U"], ids=["deny", "uncertain"])
async def test_a_refusal_carries_the_models_reason_and_suggestion(letter):
    res = await classifier(reply({"decision": letter, "reason": "why", "suggest": "alt"})).classify(case())
    assert res.reason == "why" and res.suggest == "alt"


async def test_failure_is_ask_with_error():
    res = await classifier(lambda r: httpx.Response(500)).classify(case())
    assert res.decision is DecisionKind.ask
    assert res.error == "http"
    assert res.reason.startswith("classifier unavailable")
    assert res.raw_response is None


async def test_unexpected_exception_is_ask():
    def boom(r):
        raise RuntimeError("weird")

    res = await classifier(boom).classify(case())
    assert res.decision is DecisionKind.ask and res.error == "unexpected"


def test_build_classifiers_names_one_per_configured_model():
    http = httpx.AsyncClient()
    built = build_classifiers(P.profile, http)
    assert set(built) == set(P.profile.models.configs)
    assert [name for name, c in built.items() if c.name != name] == []
