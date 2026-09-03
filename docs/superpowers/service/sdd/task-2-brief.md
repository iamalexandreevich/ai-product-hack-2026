### Task 2: Схемы API и `contracts/`

**Files:**
- Create: `service/agentgate/api/__init__.py`, `service/agentgate/api/schemas.py`, `service/scripts/export_contracts.py`, `contracts/decide_request.schema.json`, `contracts/decide_response.schema.json`, `contracts/deny_message_template.md`
- Test: `service/tests/test_schemas.py`, `service/tests/test_contracts.py`

**Interfaces:**
- Produces (`agentgate.api.schemas`):
  - `class Tool(str, Enum)`: `shell`, `file_write`, `file_read`, `network`, `mcp_call`.
  - `class DecisionKind(str, Enum)`: `allow`, `deny`, `ask`.
  - `class McpArgs(BaseModel)`: `server: str`, `tool: str`, `arguments: dict[str, Any] = {}`.
  - `class ActionArgs(BaseModel)`: `cwd: str`, `paths: list[str] = []`, `domains: list[str] = []`, `mcp: McpArgs | None = None`.
  - `class DecideRequest(BaseModel)`: `session_id: str | None` (≤128), `harness: str` (1..64), `tool: Tool`, `raw: str = ""` (≤32768 байт), `args: ActionArgs`, `user_request: str`, `profile_id: str | None = None`, `model: str | None = None`, `metadata: dict[str, Any] = {}` (≤16384 байт JSON). Валидатор: для `tool == shell` поле `raw` непустое. `user_request` обрезается до 2048 символов с сохранением хвоста.
  - `class LatencyMs(BaseModel)`: `stage1: int | None`, `stage2: int | None`, `total: int`.
  - `class DecideResponse(BaseModel)`: `decision: DecisionKind`, `reason: str = ""`, `suggest: str = ""`, `stage: int`, `rule_id: str | None = None`, `model: str | None = None`, `latency_ms: LatencyMs`, `cached: bool = False`, `decision_id: str`.
  - `USER_REQUEST_MAX_CHARS = 2048`, `RAW_MAX_BYTES = 32768`, `METADATA_MAX_BYTES = 16384`.

- [ ] **Step 1: Failing tests**

`service/tests/test_schemas.py`:

```python
import json

import pytest
from pydantic import ValidationError

from agentgate.api.schemas import (
    METADATA_MAX_BYTES,
    USER_REQUEST_MAX_CHARS,
    DecideRequest,
    DecideResponse,
    DecisionKind,
    LatencyMs,
    Tool,
)


def _req(**over):
    base = dict(
        harness="opencode",
        tool="shell",
        raw="ls -la",
        args={"cwd": "/home/u/repo"},
        user_request="покажи файлы",
    )
    base.update(over)
    return DecideRequest.model_validate(base)


def test_minimal_request_ok():
    r = _req()
    assert r.tool is Tool.shell
    assert r.session_id is None
    assert r.profile_id is None
    assert r.metadata == {}


def test_shell_requires_raw():
    with pytest.raises(ValidationError):
        _req(raw="")


def test_file_write_without_raw_ok():
    r = _req(tool="file_write", raw="", args={"cwd": "/r", "paths": ["/r/a.py"]})
    assert r.args.paths == ["/r/a.py"]


def test_user_request_truncated_keeps_tail():
    text = "x" * 3000 + "TAIL"
    r = _req(user_request=text)
    assert len(r.user_request) == USER_REQUEST_MAX_CHARS
    assert r.user_request.endswith("TAIL")


def test_metadata_size_limit():
    big = {"k": "v" * (METADATA_MAX_BYTES + 1)}
    with pytest.raises(ValidationError):
        _req(metadata=big)


def test_unknown_tool_rejected():
    with pytest.raises(ValidationError):
        _req(tool="browser")


def test_response_roundtrip():
    resp = DecideResponse(
        decision=DecisionKind.deny,
        reason="r",
        suggest="s",
        stage=1,
        rule_id="hard-deny.exfil",
        latency_ms=LatencyMs(stage1=1, stage2=None, total=1),
        decision_id="01J0000000000000000000000",
    )
    data = json.loads(resp.model_dump_json())
    assert data["decision"] == "deny"
    assert data["model"] is None
    assert data["cached"] is False
```

`service/tests/test_contracts.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_schemas.py tests/test_contracts.py -v`
Expected: FAIL, `ModuleNotFoundError: agentgate.api`.

- [ ] **Step 3: Реализация схем**

`service/agentgate/api/__init__.py`: пустой.

`service/agentgate/api/schemas.py`:

```python
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

USER_REQUEST_MAX_CHARS = 2048
RAW_MAX_BYTES = 32768
METADATA_MAX_BYTES = 16384


class Tool(str, Enum):
    shell = "shell"
    file_write = "file_write"
    file_read = "file_read"
    network = "network"
    mcp_call = "mcp_call"


class DecisionKind(str, Enum):
    allow = "allow"
    deny = "deny"
    ask = "ask"


class McpArgs(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ActionArgs(BaseModel):
    cwd: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    mcp: McpArgs | None = None


class DecideRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=128)
    harness: str = Field(min_length=1, max_length=64)
    tool: Tool
    raw: str = ""
    args: ActionArgs
    user_request: str
    profile_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("raw")
    @classmethod
    def _raw_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > RAW_MAX_BYTES:
            raise ValueError(f"raw exceeds {RAW_MAX_BYTES} bytes")
        return v

    @field_validator("user_request")
    @classmethod
    def _truncate_user_request(cls, v: str) -> str:
        if len(v) > USER_REQUEST_MAX_CHARS:
            return v[-USER_REQUEST_MAX_CHARS:]
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(v, ensure_ascii=False).encode("utf-8"))
        if size > METADATA_MAX_BYTES:
            raise ValueError(f"metadata exceeds {METADATA_MAX_BYTES} bytes")
        return v

    @model_validator(mode="after")
    def _shell_requires_raw(self) -> "DecideRequest":
        if self.tool is Tool.shell and not self.raw.strip():
            raise ValueError("raw is required for tool=shell")
        return self


class LatencyMs(BaseModel):
    stage1: int | None = None
    stage2: int | None = None
    total: int


class DecideResponse(BaseModel):
    decision: DecisionKind
    reason: str = ""
    suggest: str = ""
    stage: int
    rule_id: str | None = None
    model: str | None = None
    latency_ms: LatencyMs
    cached: bool = False
    decision_id: str
```

`service/scripts/export_contracts.py`:

```python
"""Regenerate JSON schemas in ../contracts from pydantic models."""
import json
from pathlib import Path

from agentgate.api.schemas import DecideRequest, DecideResponse

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def main() -> None:
    for name, model in (
        ("decide_request", DecideRequest),
        ("decide_response", DecideResponse),
    ):
        path = CONTRACTS / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
```

`contracts/deny_message_template.md`:

```markdown
# Шаблон сообщения агенту при `deny`

Адаптер подставляет поля ответа `/v1/decide` и отдаёт агенту как результат инструмента (tool error):

```
Действие заблокировано политикой AgentGate ({rule_id}): {reason}
Не пытайся выполнить то же самое обходным путём.
{suggest}
```

- `{rule_id}` — при `null` подставить `stage-2`.
- `{suggest}` — при пустой строке последняя строка опускается.
```

- [ ] **Step 4: Сгенерировать контракты и прогнать тесты**

Run: `cd service && uv run python scripts/export_contracts.py && uv run pytest tests/test_schemas.py tests/test_contracts.py -v`
Expected: два файла записаны, 9 passed.

- [ ] **Step 5: Commit**

```bash
git add service/agentgate/api service/scripts service/tests/test_schemas.py service/tests/test_contracts.py contracts/
git commit -m "feat(service): decide request/response schemas and contracts export

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

