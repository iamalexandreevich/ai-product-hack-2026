### Task 7: Ступень 2 — LLM-классификатор

**Files:**
- Create: `service/agentgate/stage2/__init__.py`, `service/agentgate/stage2/schema.py`, `service/agentgate/stage2/prompt.py`, `service/agentgate/stage2/client.py`, `service/agentgate/stage2/run.py`
- Test: `service/tests/test_stage2_prompt.py`, `service/tests/test_stage2_client.py`, `service/tests/test_stage2_run.py`

**Interfaces:**
- Produces (`agentgate.stage2.schema`): `class ClassifierOutput(BaseModel)`: `decision: Literal["A","D","U"]`, `risk: Literal["exfiltration","destructive","privilege","supply_chain","injection","config","network","none"] = "none"`, `reason: str = ""`, `suggest: str = ""`; `RESPONSE_JSON_SCHEMA: dict` (`ClassifierOutput.model_json_schema()` с `additionalProperties: false`).
- Produces (`agentgate.stage2.prompt`): `build_system_prompt(profile: Profile) -> str`; `build_user_message(action: NormalizedAction, user_request: str, stage1_note: str) -> str`.
- Produces (`agentgate.stage2.client`): `class Stage2Error(Exception)` с полем `kind: str` (`timeout | http | invalid_json | invalid_schema | empty`); `class LLMClient`: `__init__(self, name: str, config: ModelConfig, http: httpx.AsyncClient)`; `async classify(self, system: str, user: str) -> tuple[ClassifierOutput, dict]` (возвращает разобранный ответ и сырой JSON ответа провайдера); бросает `Stage2Error`. Один запрос, без ретраев, `timeout = config.timeout_ms / 1000`. При `structured_output=True` шлёт `response_format: {"type":"json_schema","json_schema":{"name":"agentgate_decision","strict":true,"schema":RESPONSE_JSON_SCHEMA}}`; иначе схема добавляется текстом в системный промпт. Ключ — `os.environ[config.api_key_env]`, если задан; заголовок `Authorization: Bearer …`.
- Produces (`agentgate.stage2.run`): `@dataclass class Stage2Result`: `decision: DecisionKind`, `reason: str`, `suggest: str`, `model: str`, `raw_response: dict | None`, `error: str | None`; `async run_stage2(action, user_request, profile, model_name: str, client: LLMClient, stage1_note: str) -> Stage2Result`: `A→allow`, `D→deny`, `U→ask`; любая `Stage2Error` или неожиданное исключение → `ask`, `reason = "classifier unavailable: <kind>"`, `error` заполнен.

- [ ] **Step 1: Failing tests для промпта**

`service/tests/test_stage2_prompt.py`:

```python
from agentgate.api.schemas import DecideRequest
from agentgate.normalize import normalize
from agentgate.profiles.loader import with_workspace
from agentgate.profiles.schema import Profile
from agentgate.stage2.prompt import build_system_prompt, build_user_message

WS = "/home/u/repo"
P = with_workspace(Profile.model_validate({
    "id": "t", "allowed_paths": ["${WORKSPACE}"], "protected_paths": [".env*", ".git/hooks/**"],
    "network": {"mode": "allowlist", "allowed_domains": ["pypi.org"]},
    "models": {"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "q"}}},
    "prose": {"environment": "TS monorepo", "allow": "pnpm ok", "soft_deny": "no infra/"},
}), WS)


def test_system_prompt_contains_profile_and_prose():
    s = build_system_prompt(P)
    assert "workspace=/home/u/repo" in s
    assert "network=allowlist(pypi.org)" in s
    assert "protected=.env*,.git/hooks/**" in s
    assert "TS monorepo" in s and "pnpm ok" in s and "no infra/" in s
    assert '"decision"' in s  # response schema described


def test_user_message_layout_and_blindness():
    a = normalize(DecideRequest(harness="t", tool="shell", raw="npm install lodahs && rm -rf ./dist",
                                args={"cwd": WS}, user_request="x", metadata={"secret": "LEAK"}))
    m = build_user_message(a, "почини сборку", "passed: no hard-deny match, not in allowlist")
    assert m.startswith("[TASK] почини сборку\n")
    assert "[ACTION] tool=shell cwd=/home/u/repo" in m
    assert 'argv=[["npm","install","lodahs"],["rm","-rf","./dist"]]' in m
    assert "paths=[/home/u/repo/dist]" in m
    assert "[FLAGS] unparseable=false has_eval=false has_subst=false" in m
    assert m.rstrip().endswith("[STAGE1] passed: no hard-deny match, not in allowlist")
    assert "LEAK" not in m


def test_system_prompt_is_stable_across_actions():
    assert build_system_prompt(P) == build_system_prompt(P)
```

- [ ] **Step 2: Failing tests для клиента**

`service/tests/test_stage2_client.py`:

```python
import json

import httpx
import pytest

from agentgate.profiles.schema import ModelConfig
from agentgate.stage2.client import LLMClient, Stage2Error


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
```

- [ ] **Step 3: Failing tests для run_stage2**

`service/tests/test_stage2_run.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_stage2_*.py -v`
Expected: FAIL, `ModuleNotFoundError: agentgate.stage2`.

- [ ] **Step 5: schema.py и prompt.py**

`service/agentgate/stage2/__init__.py`: пустой.

`service/agentgate/stage2/schema.py`:

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict

Risk = Literal["exfiltration", "destructive", "privilege", "supply_chain", "injection", "config", "network", "none"]


class ClassifierOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["A", "D", "U"]
    risk: Risk = "none"
    reason: str = ""
    suggest: str = ""


def _strict_schema() -> dict:
    schema = ClassifierOutput.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = ["decision", "risk", "reason", "suggest"]
    return schema


RESPONSE_JSON_SCHEMA: dict = _strict_schema()
```

`service/agentgate/stage2/prompt.py`:

```python
import json

from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.stage2.schema import RESPONSE_JSON_SCHEMA

_ROLE = (
    "You are the action reviewer for an autonomous coding agent. "
    "You see only the user's latest request, the policy profile and one proposed action. "
    "You never see the agent's reasoning or tool outputs. "
    "Decide whether the action is safe to execute without asking the user.\n"
    "Answer A (allow) when the action is consistent with the task and cannot cause irreversible harm, "
    "data exfiltration, privilege escalation, or supply-chain risk.\n"
    "Answer D (deny) when the action is clearly harmful, exfiltrates data, installs suspicious packages, "
    "or contradicts the policy. Give a one-sentence reason and a safe alternative in 'suggest'.\n"
    "Answer U (uncertain) when a human should confirm. Give a one-sentence reason.\n"
    "Respond with a single JSON object matching this schema and nothing else:\n"
)


def _profile_line(profile: Profile) -> str:
    domains = ",".join(profile.network.allowed_domains)
    return (
        f"[PROFILE] workspace={profile.workspace or ''} "
        f"network={profile.network.mode.value}({domains}) "
        f"protected={','.join(profile.protected_paths)}"
    )


def build_system_prompt(profile: Profile) -> str:
    parts = [_ROLE + json.dumps(RESPONSE_JSON_SCHEMA, separators=(",", ":")), "", _profile_line(profile)]
    if profile.prose.environment:
        parts.append(f"[ENVIRONMENT] {profile.prose.environment}")
    if profile.prose.allow:
        parts.append(f"[ALLOWED BY USER] {profile.prose.allow}")
    if profile.prose.soft_deny:
        parts.append(f"[AVOID] {profile.prose.soft_deny}")
    return "\n".join(parts)


def build_user_message(action: NormalizedAction, user_request: str, stage1_note: str) -> str:
    f = action.flags
    argv = json.dumps([c.argv for c in action.commands], separators=(",", ":"), ensure_ascii=False)
    lines = [
        f"[TASK] {user_request}",
        f"[ACTION] tool={action.tool.value} cwd={action.cwd}",
    ]
    if action.tool.value == "shell":
        lines.append(f"raw={action.raw}" if f.unparseable else f"argv={argv}")
    if action.mcp is not None:
        lines.append(f"mcp={json.dumps(action.mcp.model_dump(), ensure_ascii=False)}")
    lines.append(f"paths=[{','.join(action.paths)}] domains=[{','.join(action.domains)}]")
    lines.append(
        f"[FLAGS] unparseable={str(f.unparseable).lower()} has_eval={str(f.has_eval).lower()} "
        f"has_subst={str(f.has_subst).lower()} has_env_assign={str(f.has_env_assign).lower()}"
    )
    lines.append(f"[STAGE1] {stage1_note}")
    return "\n".join(lines)
```

- [ ] **Step 6: client.py и run.py**

`service/agentgate/stage2/client.py`:

```python
import json
import os

import httpx
from pydantic import ValidationError

from agentgate.profiles.schema import ModelConfig
from agentgate.stage2.schema import RESPONSE_JSON_SCHEMA, ClassifierOutput


class Stage2Error(Exception):
    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


class LLMClient:
    def __init__(self, name: str, config: ModelConfig, http: httpx.AsyncClient) -> None:
        self.name = name
        self.config = config
        self._http = http

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.config.api_key_env:
            key = os.environ.get(self.config.api_key_env, "")
            if key:
                headers["authorization"] = f"Bearer {key}"
        return headers

    def _body(self, system: str, user: str) -> dict:
        body = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": 300,
        }
        if self.config.structured_output:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "agentgate_decision", "strict": True, "schema": RESPONSE_JSON_SCHEMA},
            }
        return body

    async def classify(self, system: str, user: str) -> tuple[ClassifierOutput, dict]:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        try:
            resp = await self._http.post(url, json=self._body(system, user), headers=self._headers(),
                                         timeout=self.config.timeout_ms / 1000)
        except httpx.TimeoutException as exc:
            raise Stage2Error("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise Stage2Error("http", str(exc)) from exc
        if resp.status_code >= 400:
            raise Stage2Error("http", f"status {resp.status_code}")
        try:
            raw = resp.json()
        except ValueError as exc:
            raise Stage2Error("invalid_json", "response body is not JSON") from exc
        content = ""
        try:
            content = raw["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise Stage2Error("empty", "no choices in response")
        if not content.strip():
            raise Stage2Error("empty", "empty content")
        try:
            data = json.loads(_strip_fences(content))
        except ValueError as exc:
            raise Stage2Error("invalid_json", content[:200]) from exc
        try:
            return ClassifierOutput.model_validate(data), raw
        except ValidationError as exc:
            raise Stage2Error("invalid_schema", str(exc)[:200]) from exc


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()
```

`service/agentgate/stage2/run.py`:

```python
from dataclasses import dataclass

from agentgate.api.schemas import DecisionKind
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.stage2.client import LLMClient, Stage2Error
from agentgate.stage2.prompt import build_system_prompt, build_user_message

_MAP = {"A": DecisionKind.allow, "D": DecisionKind.deny, "U": DecisionKind.ask}


@dataclass
class Stage2Result:
    decision: DecisionKind
    reason: str
    suggest: str
    model: str
    raw_response: dict | None
    error: str | None


async def run_stage2(action: NormalizedAction, user_request: str, profile: Profile, model_name: str,
                     client: LLMClient, stage1_note: str) -> Stage2Result:
    system = build_system_prompt(profile)
    user = build_user_message(action, user_request, stage1_note)
    try:
        out, raw = await client.classify(system, user)
    except Stage2Error as exc:
        return Stage2Result(DecisionKind.ask, f"classifier unavailable: {exc.kind}", "", model_name, None, exc.kind)
    except Exception as exc:  # noqa: BLE001 - fail closed on anything
        return Stage2Result(DecisionKind.ask, f"classifier unavailable: unexpected ({type(exc).__name__})", "", model_name, None, "unexpected")
    decision = _MAP[out.decision]
    reason = "" if decision is DecisionKind.allow else out.reason
    suggest = "" if decision is DecisionKind.allow else out.suggest
    return Stage2Result(decision, reason, suggest, model_name, raw, None)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_stage2_*.py -v`
Expected: все passed.

- [ ] **Step 8: Commit**

```bash
git add service/agentgate/stage2 service/tests/test_stage2_*.py
git commit -m "feat(service): stage 2 LLM classifier with structured output and fail-closed

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

