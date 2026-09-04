### Task 2: `Decision`, `DecisionWriter` и разбор `Gate.decide()`

Закрывает: F1 (оркестратор), F2 (два пути персистентности), F16 (тесты импортируют фикстуры друг у друга), L3 (запись сцеплена с нормализацией), G1 (молчаливые `except`).

**Files:**
- Create: `service/agentgate/engine/decision.py`, `service/agentgate/engine/gate.py`, `service/agentgate/store/writer.py`, `service/tests/factories.py`, `service/tests/store/__init__.py`, `service/tests/store/test_writer.py`, `service/tests/engine/test_decision.py`, `service/tests/engine/test_gate.py`
- Delete: `service/agentgate/pipeline.py`, `service/tests/test_pipeline.py`
- Modify: `service/agentgate/api/app.py` (удалить замыкание `persist`, `_ask`, `_CACHE_TTL_SECONDS`), `service/agentgate/__main__.py`, `service/agentgate/config.py` (TTL кэша — поле `Settings`), `service/tests/test_api.py`
- Test: `service/tests/engine/test_gate.py` (замена `tests/test_pipeline.py`), `service/tests/store/test_writer.py`

**Interfaces:**
- Produces: `agentgate.engine.decision.Decision` — frozen dataclass: `id: str`, `ts: datetime`, `request: DecideRequest`, `verdict: Verdict`, `latency: Latency`, `profile_id: str`, `profile_hash: str`, `action: NormalizedAction | None = None`, `state: SessionState | None = None`, `cache_key: str | None = None`, `cached: bool = False`; методы `to_response() -> DecideResponse` и `to_view() -> DecisionView`.
- Produces: `agentgate.engine.decision.DecisionView` — pydantic-модель плоской формы решения (persisted и читаемая через API), поле `id` плюс `@computed_field decision_id`.
- Produces: `agentgate.store.writer.DecisionWriter` (Protocol, `async def write(self, decision: Decision) -> None`, никогда не бросает), `PostgresDecisionWriter(decisions, sessions, cache_ttl_seconds)`, `JsonlDecisionWriter(logger)`, `CompositeDecisionWriter(writers)`.
- Produces: `agentgate.engine.gate.Gate(profiles, default_profile, state_store, http, cache_ttl_seconds)` с `async def decide(self, req: DecideRequest) -> Decision`.
- Produces: `tests.factories` — `WORKSPACE`, `profile(**overrides)`, `decide_request(raw, **overrides)`, `FakeLLM`, `RecordingDecisionWriter`.
- Consumes: `Verdict`, `Timings`, `Latency` (задача 1).

- [ ] **Step 1: `tests/factories.py` — общие фабрики вместо импортов между тестами**

`service/tests/factories.py`:

```python
"""Shared builders for tests. Test modules import from here, never from
each other -- renaming a test module must not break three others.
"""

import json

import httpx

from agentgate.api.schemas import DecideRequest
from agentgate.engine.decision import Decision
from agentgate.profiles.schema import Profile

WORKSPACE = "/home/u/repo"


def profile(**overrides) -> Profile:
    data = {
        "id": "default",
        "allowed_paths": ["${WORKSPACE}"],
        "protected_paths": [".env*"],
        "network": {"mode": "allowlist", "allowed_domains": ["pypi.org"]},
        "models": {
            "default": "m",
            "configs": {
                "m": {"base_url": "http://llm/v1", "model": "q", "timeout_ms": 500},
                "m2": {"base_url": "http://llm2/v1", "model": "q2"},
            },
        },
        "escalation": {"deny_consecutive": 2, "deny_window": {"count": 10, "of_last": 50}},
    }
    data.update(overrides)
    return Profile.model_validate(data)


def decide_request(raw: str, session_id: str | None = "s1", **overrides) -> DecideRequest:
    data = dict(
        session_id=session_id, harness="t", tool="shell", raw=raw,
        args={"cwd": WORKSPACE}, user_request="task",
    )
    data.update(overrides)
    return DecideRequest.model_validate(data)


class FakeLLM:
    """An httpx MockTransport handler standing in for the OpenAI-compatible
    endpoint. Replaced by a Classifier fake in task 6 -- until the protocol
    exists, the transport is the only seam.
    """

    def __init__(self, decision: str = "A", reason: str = "r", suggest: str = "s", status: int = 200) -> None:
        self.calls = 0
        self.decision, self.reason, self.suggest, self.status = decision, reason, suggest, status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.status != 200:
            return httpx.Response(self.status)
        body = {"decision": self.decision, "risk": "none", "reason": self.reason, "suggest": self.suggest}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})


class RecordingDecisionWriter:
    """A DecisionWriter that keeps what it was given, in order."""

    def __init__(self) -> None:
        self.decisions: list[Decision] = []

    async def write(self, decision: Decision) -> None:
        self.decisions.append(decision)


class FailingDecisionWriter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("writer is down")
        self.calls = 0

    async def write(self, decision: Decision) -> None:
        self.calls += 1
        raise self.error
```

- [ ] **Step 2: Failing-тест для `Decision`**

`service/tests/engine/test_decision.py`:

```python
from datetime import datetime, timezone

from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Latency
from tests.factories import decide_request


def decision(**overrides) -> Decision:
    data = dict(
        id="01J0", ts=datetime.now(timezone.utc), request=decide_request("ls -la"),
        verdict=Verdict.allow("allowlist.readonly"),
        latency=Latency(total_ms=3, stage1_ms=1),
        profile_id="default", profile_hash="h" * 64,
    )
    data.update(overrides)
    return Decision(**data)


def test_response_carries_the_verdict():
    response = decision().to_response()
    assert response.decision is DecisionKind.allow
    assert response.rule_id == "allowlist.readonly"
    assert response.stage == 1


def test_response_decision_id_is_the_decision_id():
    assert decision().to_response().decision_id == "01J0"


def test_response_latency_comes_from_the_measured_stages():
    response = decision().to_response()
    assert response.latency_ms.stage1 == 1 and response.latency_ms.stage2 is None


def test_view_exposes_both_id_and_decision_id():
    dumped = decision().to_view().model_dump(mode="json")
    assert dumped["id"] == "01J0" and dumped["decision_id"] == "01J0"


def test_view_normalized_is_empty_when_nothing_was_normalized():
    assert decision(action=None).to_view().normalized == {}


def test_view_does_not_smuggle_the_cache_key_into_normalized():
    view = decision(cache_key="k" * 64).to_view()
    assert "cache_key" not in view.normalized


def test_view_ts_serializes_as_an_iso_string():
    dumped = decision().to_view().model_dump(mode="json")
    assert isinstance(dumped["ts"], str) and dumped["ts"].startswith(str(datetime.now(timezone.utc).year))
```

Run: `cd service && uv run pytest tests/engine/test_decision.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.engine.decision'`.

- [ ] **Step 3: Реализовать `Decision` и `DecisionView`**

`service/agentgate/engine/decision.py`:

```python
"""One decision, and the one flat shape it is stored and read in.

`Decision` is what the engine produces: the request, the verdict, what
was normalized, how long it took. `DecisionView` is the flat projection
every consumer outside the engine sees -- the JSONL line, the Postgres
row, and the items of GET /v1/decisions are the same shape, defined
once, so a field cannot exist in the log and be missing from the API.

The view carries both `id` and `decision_id`: `decision_id` is the name
the public contract uses everywhere else, `id` is what the log and the
row have always been keyed by. One field, two spellings, no second
source of truth.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, computed_field

from agentgate.api.schemas import DecideRequest, DecideResponse, DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.timings import Latency
from agentgate.normalize.model import NormalizedAction
from agentgate.session.state import SessionState

from dataclasses import dataclass


class DecisionView(BaseModel):
    id: str
    session_id: str | None
    ts: datetime
    harness: str
    tool: str
    raw: str
    normalized: dict[str, Any]
    user_request: str
    profile_id: str
    profile_hash: str
    decision: DecisionKind
    reason: str
    suggest: str
    stage: int
    rule_id: str | None
    model: str | None
    model_raw_response: dict[str, Any] | None
    latency_stage1_ms: int | None
    latency_stage2_ms: int | None
    latency_total_ms: int
    error: str | None
    cached: bool
    metadata: dict[str, Any]

    @computed_field
    @property
    def decision_id(self) -> str:
        return self.id


@dataclass(frozen=True)
class Decision:
    id: str
    ts: datetime
    request: DecideRequest
    verdict: Verdict
    latency: Latency
    profile_id: str
    profile_hash: str
    action: NormalizedAction | None = None
    state: SessionState | None = None
    cache_key: str | None = None
    cached: bool = False

    def to_response(self) -> DecideResponse:
        return DecideResponse(
            decision=self.verdict.decision,
            reason=self.verdict.reason,
            suggest=self.verdict.suggest,
            stage=self.verdict.stage,
            rule_id=self.verdict.rule_id,
            model=self.verdict.model,
            latency_ms=self.latency.to_schema(),
            cached=self.cached,
            decision_id=self.id,
        )

    def to_view(self) -> DecisionView:
        return DecisionView(
            id=self.id,
            session_id=self.request.session_id,
            ts=self.ts,
            harness=self.request.harness,
            tool=self.request.tool.value,
            raw=self.request.raw,
            normalized=self.action.to_dict() if self.action is not None else {},
            user_request=self.request.user_request,
            profile_id=self.profile_id,
            profile_hash=self.profile_hash,
            decision=self.verdict.decision,
            reason=self.verdict.reason,
            suggest=self.verdict.suggest,
            stage=self.verdict.stage,
            rule_id=self.verdict.rule_id,
            model=self.verdict.model,
            model_raw_response=self.verdict.raw_response,
            latency_stage1_ms=self.latency.stage1_ms,
            latency_stage2_ms=self.latency.stage2_ms,
            latency_total_ms=self.latency.total_ms,
            error=self.verdict.error,
            cached=self.cached,
            metadata=self.request.metadata,
        )
```

Run: `cd service && uv run pytest tests/engine/test_decision.py -v`
Expected: 7 passed.

- [ ] **Step 4: Failing-тест для `DecisionWriter`**

`service/tests/store/__init__.py`: пустой файл.

`service/tests/store/test_writer.py`:

```python
import logging
from datetime import datetime, timezone

from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Latency
from agentgate.store.writer import CompositeDecisionWriter, JsonlDecisionWriter, PostgresDecisionWriter
from tests.factories import FailingDecisionWriter, RecordingDecisionWriter, decide_request


def decision(verdict: Verdict | None = None, **overrides) -> Decision:
    data = dict(
        id="01J0", ts=datetime.now(timezone.utc), request=decide_request("ls -la"),
        verdict=verdict or Verdict.allow("allowlist.readonly"),
        latency=Latency(total_ms=1), profile_id="default", profile_hash="h" * 64,
    )
    data.update(overrides)
    return Decision(**data)


class FakeSessionRepo:
    def __init__(self) -> None:
        self.upserts: list[str] = []
        self.cache_puts: list[tuple[str, str, str]] = []

    async def upsert(self, state) -> None:
        self.upserts.append(state.session_id)

    async def cache_put(self, session_id, action_hash, decision_id, expires_at) -> None:
        self.cache_puts.append((session_id, action_hash, decision_id))


class FakeDecisionRepo:
    def __init__(self) -> None:
        self.inserted: list[Decision] = []

    async def insert(self, decision) -> None:
        self.inserted.append(decision)


class CollectingLogger:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, record: dict) -> None:
        self.lines.append(record)


def state(session_id: str = "s1"):
    from agentgate.session.state import SessionState

    return SessionState(session_id=session_id, harness="t", profile_id="default", workspace="/w")


async def test_jsonl_writer_writes_the_view_shape():
    logger = CollectingLogger()
    await JsonlDecisionWriter(logger).write(decision())
    assert logger.lines[0]["decision_id"] == "01J0"
    assert logger.lines[0]["id"] == "01J0"


async def test_postgres_writer_upserts_the_session_before_the_decision():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    order: list[str] = []
    sessions.upsert = lambda s: order.append("session") or _done()  # noqa: E731
    await PostgresDecisionWriter(decisions, sessions, 86400).write(decision(state=state()))
    assert order == ["session"] and len(decisions.inserted) == 1


async def test_postgres_writer_caches_an_allow():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(state=state(), cache_key="k" * 64)
    )
    assert sessions.cache_puts == [("s1", "k" * 64, "01J0")]


async def test_postgres_writer_never_caches_a_deny():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(verdict=Verdict.deny("profile.path", "outside"), state=state(), cache_key="k" * 64)
    )
    assert sessions.cache_puts == []


async def test_postgres_writer_never_recaches_a_cache_hit():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(state=state(), cache_key="k" * 64, cached=True)
    )
    assert sessions.cache_puts == []


async def test_postgres_writer_skips_the_session_row_for_a_sessionless_call():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(decision(state=None))
    assert sessions.upserts == [] and len(decisions.inserted) == 1


async def test_composite_runs_every_writer():
    first, second = RecordingDecisionWriter(), RecordingDecisionWriter()
    await CompositeDecisionWriter([first, second]).write(decision())
    assert len(first.decisions) == 1 and len(second.decisions) == 1


async def test_composite_isolates_a_failing_writer():
    failing, healthy = FailingDecisionWriter(), RecordingDecisionWriter()
    await CompositeDecisionWriter([failing, healthy]).write(decision())
    assert failing.calls == 1 and len(healthy.decisions) == 1


async def test_composite_logs_the_failure_it_swallowed(caplog):
    with caplog.at_level(logging.ERROR):
        await CompositeDecisionWriter([FailingDecisionWriter()]).write(decision())
    assert "01J0" in caplog.text
```

Убрать из теста `test_postgres_writer_upserts_the_session_before_the_decision` хак с лямбдой — записать порядок честным фейком:

```python
class OrderRecordingRepos:
    """Both repos sharing one order log, so 'session row before decision row'
    (the FK requirement) is asserted as an observable fact, not as a call count.
    """

    def __init__(self) -> None:
        self.order: list[str] = []

    async def upsert(self, state) -> None:
        self.order.append("session")

    async def cache_put(self, session_id, action_hash, decision_id, expires_at) -> None:
        self.order.append("cache")

    async def insert(self, decision) -> None:
        self.order.append("decision")


async def test_postgres_writer_writes_session_then_decision_then_cache():
    repos = OrderRecordingRepos()
    await PostgresDecisionWriter(repos, repos, 86400).write(decision(state=state(), cache_key="k" * 64))
    assert repos.order == ["session", "decision", "cache"]
```

Run: `cd service && uv run pytest tests/store/test_writer.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.store.writer'`.

- [ ] **Step 5: Реализовать `DecisionWriter`**

`service/agentgate/store/writer.py`:

```python
"""Where a decision goes after the answer has already been sent.

One protocol, three implementations. `write` never raises: a decision the
caller already has must not be undone by a storage failure, and one sink
failing must not stop the others.

The FK ordering (session row -> decision row -> allow-cache row) lives
here and nowhere else -- `decisions.session_id` references `sessions.id`
and `allow_cache.decision_id` references `decisions.id`.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol

from agentgate.api.schemas import DecisionKind
from agentgate.engine.decision import Decision

log = logging.getLogger(__name__)


class DecisionWriter(Protocol):
    async def write(self, decision: Decision) -> None: ...


class JsonlDecisionWriter:
    def __init__(self, logger) -> None:
        self._logger = logger

    async def write(self, decision: Decision) -> None:
        self._logger.write(decision.to_view().model_dump(mode="json"))


class PostgresDecisionWriter:
    def __init__(self, decisions, sessions, cache_ttl_seconds: int) -> None:
        self._decisions = decisions
        self._sessions = sessions
        self._cache_ttl_seconds = cache_ttl_seconds

    async def write(self, decision: Decision) -> None:
        if decision.state is not None:
            await self._sessions.upsert(decision.state)
        await self._decisions.insert(decision)
        if self._should_cache(decision):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._cache_ttl_seconds)
            await self._sessions.cache_put(
                decision.state.session_id, decision.cache_key, decision.id, expires_at
            )

    def _should_cache(self, decision: Decision) -> bool:
        return (
            decision.state is not None
            and decision.cache_key is not None
            and decision.verdict.decision is DecisionKind.allow
            and not decision.cached
        )


class CompositeDecisionWriter:
    def __init__(self, writers: Sequence[DecisionWriter]) -> None:
        self._writers = tuple(writers)

    async def write(self, decision: Decision) -> None:
        for writer in self._writers:
            try:
                await writer.write(decision)
            except Exception:  # noqa: BLE001 - one sink's failure must not stop the others
                log.exception(
                    "%s failed to write decision %s", type(writer).__name__, decision.id
                )
```

`DecisionRepo.insert` теперь принимает `Decision`, а не `DecisionRecord` — маппинг в строку переезжает в задачу 6. На время этой задачи добавить в `agentgate/store/repo.py` мост:

```python
    async def insert(self, decision) -> None:
        async with self._sf() as s:
            s.add(_row_from_view(decision.to_view()))
            await s.commit()
```

и модульную функцию `_row_from_view(view: DecisionView) -> DecisionRow`, которая делает единственное переименование `metadata → metadata_`:

```python
def _row_from_view(view) -> DecisionRow:
    data = view.model_dump(exclude={"decision_id"})
    data["metadata_"] = data.pop("metadata")
    return DecisionRow(**data)
```

`DecisionRecord` пока остаётся для `DecisionRepo.list` — уходит в задаче 6.

Run: `cd service && uv run pytest tests/store/test_writer.py -v`
Expected: 9 passed.

- [ ] **Step 6: TTL кэша — одно поле настроек**

`service/agentgate/config.py` — добавить в `Settings` поле после `default_profile`:

```python
    allow_cache_ttl_seconds: int = 86400
```

Оно заменяет `_CACHE_TTL_SECONDS` в `api/app.py` и дефолт `cache_ttl_seconds=86400` в `Gate.__init__` — знание о TTL перестаёт быть записанным дважды (гайд 1.3).

`service/tests/test_config.py` — добавить один тест:

```python
def test_allow_cache_ttl_defaults_to_a_day(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    assert Settings().allow_cache_ttl_seconds == 86400
```

- [ ] **Step 7: Failing-тест для нового `Gate`**

`service/tests/engine/test_gate.py` — перенос `tests/test_pipeline.py` с адаптацией под `Decision`. Все существующие проверки сохраняются; меняется только распаковка результата (`decision = await gate(...).decide(...)` вместо `resp, rec, state = ...`) и источник фикстур (`tests.factories`). Начало файла:

```python
import httpx

from agentgate.api.schemas import DecisionKind
from agentgate.engine.gate import Gate
from agentgate.session.memory import InMemorySessionStateStore
from tests.factories import WORKSPACE, FakeLLM, decide_request, profile


def gate(llm: FakeLLM, **profile_overrides) -> Gate:
    return Gate(
        profiles={"default": profile(**profile_overrides)},
        default_profile="default",
        state_store=InMemorySessionStateStore(),
        http=httpx.AsyncClient(transport=httpx.MockTransport(llm)),
        allow_cache_ttl_seconds=86400,
    )


async def test_stage1_allow_skips_the_classifier():
    llm = FakeLLM()
    decision = await gate(llm).decide(decide_request("ls -la"))
    assert decision.verdict.decision is DecisionKind.allow
    assert decision.verdict.rule_id == "allowlist.readonly"
    assert llm.calls == 0


async def test_stage1_allow_reports_no_model():
    decision = await gate(FakeLLM()).decide(decide_request("ls -la"))
    assert decision.to_response().model is None


async def test_stage1_allow_measures_stage1_but_not_stage2():
    decision = await gate(FakeLLM()).decide(decide_request("ls -la"))
    assert decision.latency.stage2_ms is None and decision.latency.total_ms >= 0


async def test_decision_records_what_was_normalized():
    decision = await gate(FakeLLM()).decide(decide_request("ls -la"))
    assert decision.action is not None and decision.action.to_dict()["tool"] == "shell"
    assert decision.profile_hash


async def test_session_counters_advance():
    decision = await gate(FakeLLM()).decide(decide_request("ls -la"))
    assert decision.state is not None and decision.state.decisions_total == 1
```

Остальные тесты `tests/test_pipeline.py` перенести один в один, разделяя те, что проверяют по нескольку фактов (гайд 6.5), и заменяя обращения:
- `resp.decision` → `decision.verdict.decision`
- `resp.stage` → `decision.verdict.stage`
- `resp.rule_id` → `decision.verdict.rule_id`
- `resp.cached` → `decision.cached`
- `rec.decision == "allow"` → `decision.verdict.decision is DecisionKind.allow`
- `rec.normalized["tool"]` → `decision.action.to_dict()["tool"]`
- проверки `persisted` (список из старого фейка `persist`) — удалить: запись больше не входит в `Gate`. Их предмет покрыт `tests/store/test_writer.py` и `tests/test_api.py`.

Run: `cd service && uv run pytest tests/engine/test_gate.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.engine.gate'`.

- [ ] **Step 8: Реализовать `Gate`**

`service/agentgate/engine/gate.py` — новый модуль вместо `pipeline.py`. Оркестрация и только она; ни записи, ни знания о HTTP.

```python
"""The decision pipeline.

Fixed order: resolve the profile and the model -> normalize -> allow-cache
lookup -> stage 1 rules -> stage 2 classifier -> escalation -> session
state. Every step either produces a Verdict or hands the call to the next
one, and `decide` returns one immutable Decision.

Fail-closed is the spine: an unknown profile or model resolves to `ask`
before anything else runs, and stage 2 turns every classifier failure into
`ask` itself (see agentgate.stage2.run), so nothing here produces `allow`
on an error path.

Persistence is not this module's concern -- see agentgate.store.writer.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from ulid import ULID

from agentgate.api.schemas import DecideRequest, DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Timings
from agentgate.normalize import normalize
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.loader import with_workspace
from agentgate.profiles.schema import ModelConfig, Profile
from agentgate.session.cache_key import allow_cache_key
from agentgate.session.escalation import should_escalate
from agentgate.session.state import SessionState, SessionStateStore
from agentgate.stage1.chain import run_stage1
from agentgate.stage2.client import LLMClient
from agentgate.stage2.run import run_stage2

log = logging.getLogger(__name__)

STAGE1_PASSED = "passed: no hard-deny match, not in allowlist"
STAGE1_SKIPPED = "skipped: command unparseable"


@dataclass(frozen=True)
class _Context:
    """Everything resolved from the request before any rule runs."""

    profile: Profile
    profile_hash: str
    profile_id: str
    model_name: str
    model_config: ModelConfig
    state: SessionState | None


class Gate:
    def __init__(
        self,
        profiles: dict[str, Profile],
        default_profile: str,
        state_store: SessionStateStore,
        http: httpx.AsyncClient,
        allow_cache_ttl_seconds: int = 86400,
    ) -> None:
        self._profiles = profiles
        self._default_profile = default_profile
        self._states = state_store
        self._http = http
        self._allow_cache_ttl_seconds = allow_cache_ttl_seconds

    async def decide(self, request: DecideRequest) -> Decision:
        timings = Timings()
        decision_id = str(ULID())
        profile_id = request.profile_id or self._default_profile

        resolved = await self._resolve(request, profile_id)
        if isinstance(resolved, Verdict):
            return self._finish(decision_id, request, resolved, timings, profile_id, "")

        action = normalize(request)
        cache_key = allow_cache_key(
            resolved.profile_hash, action.action_hash(), request.user_request
        )
        if await self._cache_hit(resolved, cache_key):
            return self._finish(
                decision_id, request, Verdict.allow("cache", stage=0), timings,
                profile_id, resolved.profile_hash, action, resolved.state, cache_key, cached=True,
            )

        verdict = await self._evaluate(request, action, resolved, timings)
        verdict = self._escalate(resolved.state, resolved.profile, verdict)
        await self._settle_session(resolved.state, verdict, cache_key, decision_id)
        return self._finish(
            decision_id, request, verdict, timings, profile_id, resolved.profile_hash,
            action, resolved.state, cache_key,
        )

    async def _resolve(self, request: DecideRequest, profile_id: str) -> "_Context | Verdict":
        base = self._profiles.get(profile_id)
        if base is None:
            return Verdict.ask("api.unknown-profile", f"unknown profile '{profile_id}'", stage=0)
        try:
            model_name, model_config = base.models.model_config_for(request.model)
        except KeyError:
            return Verdict.ask("api.unknown-model", f"unknown model '{request.model}'", stage=0)

        profile = with_workspace(base, request.args.cwd)
        state = None
        if request.session_id:
            state = await self._states.get_or_create(
                request.session_id, request.harness, profile_id,
                profile.workspace or request.args.cwd,
            )
        return _Context(
            profile=profile, profile_hash=profile.profile_hash(), profile_id=profile_id,
            model_name=model_name, model_config=model_config, state=state,
        )

    async def _cache_hit(self, context: _Context, cache_key: str) -> bool:
        if context.state is None:
            return False
        return await self._states.cache_get(context.state.session_id, cache_key) is not None

    async def _evaluate(
        self, request: DecideRequest, action: NormalizedAction, context: _Context, timings: Timings
    ) -> Verdict:
        with timings.stage(1):
            verdict = None if action.flags.unparseable else run_stage1(action, context.profile)
        if verdict is not None:
            return verdict
        note = STAGE1_SKIPPED if action.flags.unparseable else STAGE1_PASSED
        with timings.stage(2):
            client = LLMClient(context.model_name, context.model_config, self._http)
            return await run_stage2(
                action, request.user_request, context.profile, context.model_name, client, note
            )

    def _escalate(self, state: SessionState | None, profile: Profile, verdict: Verdict) -> Verdict:
        if state is None or verdict.hard or verdict.decision is DecisionKind.ask:
            return verdict
        if not should_escalate(state, profile.escalation):
            return verdict
        hits = state.deny_consecutive
        state.reset_after_escalation()
        return verdict.escalated(hits)

    async def _settle_session(
        self, state: SessionState | None, verdict: Verdict, cache_key: str, decision_id: str
    ) -> None:
        if state is None:
            return
        state.record(verdict.decision)
        await self._states.save(state)
        if verdict.decision is DecisionKind.allow:
            await self._states.cache_put(
                state.session_id, cache_key, decision_id, self._allow_cache_ttl_seconds
            )

    def _finish(
        self, decision_id: str, request: DecideRequest, verdict: Verdict, timings: Timings,
        profile_id: str, profile_hash: str, action: NormalizedAction | None = None,
        state: SessionState | None = None, cache_key: str | None = None, cached: bool = False,
    ) -> Decision:
        return Decision(
            id=decision_id, ts=datetime.now(timezone.utc), request=request, verdict=verdict,
            latency=timings.finish(), profile_id=profile_id, profile_hash=profile_hash,
            action=action, state=state, cache_key=cache_key, cached=cached,
        )
```

Заметить (L3): ранний отказ по неизвестному профилю больше не нормализует запрос ради того, чтобы было что записать — `action` остаётся `None`, и `DecisionView.normalized` для такой строки будет `{}`.

Удалить `service/agentgate/pipeline.py` и `service/tests/test_pipeline.py`.

Run: `cd service && uv run pytest tests/engine -v`
Expected: все зелёные.

- [ ] **Step 9: Переключить `app.py` и `__main__.py` на writer**

`service/agentgate/api/app.py`:
- Удалить `_CACHE_TTL_SECONDS`, замыкание `persist` целиком (строки 80–99) и функцию `_ask` (строки 60–64).
- Сигнатура: `create_app(settings, gate, writer, decision_repo, profiles, db_probe=None, key_repo=None)` — `session_repo` и `jsonl` больше не нужны, их знает writer.
- Ранний отказ (невалидный JSON, невалидное тело, исключение из `decide`) собирается через `Verdict` + `Decision`:

```python
def _refuse(rule_id: str, reason: str) -> DecideResponse:
    return Verdict.ask(rule_id, reason, stage=0).to_response_for(str(ULID()))
```

Метод `to_response_for` на `Verdict` не вводить (гайд 1.2) — вместо него собрать `DecideResponse` прямо:

```python
def _refuse(rule_id: str, reason: str) -> DecideResponse:
    verdict = Verdict.ask(rule_id, reason, stage=0)
    return DecideResponse(
        decision=verdict.decision, reason=verdict.reason, stage=verdict.stage,
        rule_id=verdict.rule_id, model=None,
        latency_ms=LatencyMs(stage1=None, stage2=None, total=0), decision_id=str(ULID()),
    )
```

- Роут `decide`:

```python
    @app.post("/v1/decide", response_model=DecideResponse, dependencies=[auth])
    async def decide(request: Request, background: BackgroundTasks) -> DecideResponse:
        try:
            payload = await request.json()
        except ValueError:
            return _refuse("api.invalid-request", "request body is not valid JSON")
        try:
            parsed = DecideRequest.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            return _refuse("api.invalid-request", f"invalid request: {location}: {first.get('msg')}")
        try:
            decision = await gate.decide(parsed)
        except Exception as exc:  # noqa: BLE001 - fail-closed: no exception may escape as a 500
            log.exception("Gate.decide failed")
            return _refuse("api.internal-error", f"internal error: {type(exc).__name__}")
        background.add_task(writer.write, decision)
        return decision.to_response()
```

- Роут `/v1/decisions` пока оставить как есть (`decision_repo.list` + `to_dict`), он переезжает в задаче 6.

`service/agentgate/__main__.py` — в `build_app` собрать writer:

```python
    writer = CompositeDecisionWriter([
        JsonlDecisionWriter(JsonlLogger(settings.log_path)),
        PostgresDecisionWriter(decision_repo, session_repo, settings.allow_cache_ttl_seconds),
    ])
    gate = Gate(profiles, settings.default_profile, store, httpx.AsyncClient(),
                allow_cache_ttl_seconds=settings.allow_cache_ttl_seconds)
    app = create_app(settings, gate, writer, decision_repo, profiles,
                     db_probe=make_db_probe(engine), key_repo=key_repo)
```

Порядок writer'ов важен и сохраняет нынешнее поведение: JSONL пишется первым, отказ Postgres его не отменяет.

В `make_db_probe` заменить молчаливый `except Exception: return False` на логирующий (G1, гайд 7.2):

```python
        except Exception:  # noqa: BLE001 - a broken probe reports "not ok", never a 500
            log.warning("database probe failed", exc_info=True)
            return False
```

То же самое в `agentgate/api/app.py` в обработчике `/healthz`.

- [ ] **Step 10: Обновить `tests/test_api.py`**

- `build()` в тесте собирает `create_app(settings, gate, writer, decision_repo, profiles, ...)`, где `writer` — `RecordingDecisionWriter` из `tests.factories` либо реальный `CompositeDecisionWriter` поверх фейковых репозиториев, в зависимости от того, что проверяет тест.
- Импорт `from tests.test_pipeline import FakeLLM, profile` заменить на `from tests.factories import FakeLLM, profile` (F16).
- Проверки вида `drepo.rows[0].metadata == {"run_id": "r"}` остаются: фейковый репозиторий теперь получает `Decision`, поэтому читать `drepo.rows[0].request.metadata` или `drepo.rows[0].to_view().metadata`. Выбрать `to_view()` — это форма, в которой строка действительно ложится в базу.
- `drepo.rows[0].decision == "deny"` → `drepo.rows[0].to_view().decision is DecisionKind.deny`.

- [ ] **Step 11: Полный прогон, контракт, коммит**

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: всё зелёное. Тесты `tests/test_stage1_*.py`, `tests/test_normalize_*.py`, `tests/test_stage2_*.py`, `tests/test_profiles.py` не менялись и обязаны пройти без правок.

Run:
```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
```
Expected: пустой diff.

```bash
git add service/agentgate/engine service/agentgate/store/writer.py service/agentgate/store/repo.py service/agentgate/api/app.py service/agentgate/__main__.py service/agentgate/config.py service/tests/factories.py service/tests/engine service/tests/store service/tests/test_api.py service/tests/test_config.py
git rm service/agentgate/pipeline.py service/tests/test_pipeline.py
git commit -m "refactor(service): Decision plus DecisionWriter, Gate only orchestrates

Gate.decide returns one immutable Decision and no longer persists
anything; the two persistence paths collapse into DecisionWriter with
Postgres/JSONL/Composite implementations. The allow-cache TTL and the
cache key each live in one place. tests/factories.py replaces the
cross-imports between test modules.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

