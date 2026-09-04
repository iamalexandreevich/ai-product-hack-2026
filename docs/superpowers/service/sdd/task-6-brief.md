### Task 6: `Classifier`, `PersistentSessionStateStore`, `bootstrap.py`, типизированные границы

Закрывает: F3 (`SessionStateStore` разорван на две половины), F7 (`Gate` строит `LLMClient` сам), F12 (нетипизированные границы API), F13 (`DecisionRecord` — ручной маппинг в три стороны), F15 (два composition root), G3 (тесты с несколькими утверждениями), гайд 6.1 (три `monkeypatch.setattr`).

**Files:**
- Create: `service/agentgate/classify/__init__.py`, `service/agentgate/classify/base.py`, `service/agentgate/classify/llm.py`, `service/agentgate/session/persistent.py`, `service/agentgate/store/mapper.py`, `service/agentgate/bootstrap.py`, `service/tests/classify/__init__.py`, `service/tests/classify/test_llm.py`, `service/tests/session/__init__.py`, `service/tests/session/test_persistent.py`, `service/tests/test_bootstrap.py`
- Move: `service/agentgate/stage2/` → `service/agentgate/classify/` (`client.py`, `prompt.py`, `schema.py`; `run.py` растворяется в `llm.py`); `service/agentgate/session/state.py` → `service/agentgate/domain/session.py`
- Modify: `service/agentgate/engine/gate.py`, `service/agentgate/api/app.py`, `service/agentgate/api/deps.py`, `service/agentgate/api/schemas.py`, `service/agentgate/store/repo.py`, `service/agentgate/cli.py`, `service/agentgate/__main__.py`
- Move tests: `test_stage2_client.py` → `tests/classify/test_client.py`, `test_stage2_prompt.py` → `tests/classify/test_prompt.py`, `test_stage2_run.py` → `tests/classify/test_llm.py`, `test_session.py` → `tests/session/test_memory.py` + `tests/domain/test_session.py`, `test_store.py` → `tests/store/test_repo.py`, `test_keys.py` → `tests/store/test_keys.py`, `test_deps_keys.py` → `tests/api/test_deps.py`, `test_api.py` → `tests/api/test_app.py`, `test_log.py` → `tests/log/test_jsonl.py`, `test_main.py` → `tests/test_bootstrap.py`

**Interfaces:**
- Produces: `agentgate.classify.base.Classifier` (Protocol): `name: str`, `async def classify(self, action, user_request, policy, stage1_note) -> Verdict`. Никогда не бросает — любая ошибка становится `Verdict.ask(..., stage=2, error=...)`.
- Produces: `agentgate.classify.llm.LLMClassifier(name, model_config, http)` — реализация поверх нынешних `LLMClient` + `build_system_prompt` + `build_user_message`.
- Produces: `agentgate.classify.llm.build_classifiers(profile, http) -> dict[str, Classifier]`.
- Produces: `agentgate.session.persistent.PersistentSessionStateStore(inner, sessions)` — реализация `SessionStateStore` с write-through и `async def restore(self) -> None`.
- Produces: `agentgate.store.mapper.row_from_view`, `view_from_row` — единственное место, знающее про `metadata_`.
- Produces: `agentgate.bootstrap.build_service(settings, *, http=None, writer=None, state_store=None) -> Service` — `Service` — frozen dataclass с полями `app`, `gate`, `writer`, `engine`, `settings`.
- Produces: `agentgate.api.schemas.DecisionsPage`, `agentgate.api.schemas.Health`.

- [ ] **Step 1: `Classifier` — протокол и фейк вместо `MockTransport`**

`service/agentgate/classify/base.py`:

```python
"""Stage 2: what the classifier is, from the engine's point of view.

`classify` never raises. Every failure -- a timeout, a malformed reply, a
bug in the client -- comes back as an `ask` verdict carrying `error`, so
`allow` on a broken classifier is not expressible.
"""

from typing import Protocol

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


class Classifier(Protocol):
    name: str

    async def classify(
        self, action: NormalizedAction, user_request: str, policy: Policy, stage1_note: str
    ) -> Verdict: ...
```

В `tests/factories.py` добавить фейк, реализующий этот протокол (гайд 6.1 — фейк вместо подмены транспорта):

```python
class FakeClassifier:
    """A Classifier that answers what it was told to, and counts calls."""

    def __init__(self, verdict: Verdict | None = None, name: str = "m") -> None:
        self.name = name
        self.calls = 0
        self._verdict = verdict or Verdict(
            decision=DecisionKind.allow, stage=2, model=name, raw_response={"choices": []}
        )

    async def classify(self, action, user_request, policy, stage1_note) -> Verdict:
        self.calls += 1
        return self._verdict
```

`FakeLLM` (транспорт) остаётся только в `tests/classify/test_client.py`, где предметом проверки и является HTTP-клиент.

- [ ] **Step 2: `LLMClassifier` — `run_stage2` становится методом**

`service/agentgate/classify/llm.py` — объединяет нынешние `stage2/run.py` и конструирование `LLMClient`:

```python
class LLMClassifier:
    def __init__(self, name: str, model_config: ModelConfig, http: httpx.AsyncClient) -> None:
        self.name = name
        self._client = LLMClient(name, model_config, http)

    async def classify(self, action, user_request, policy, stage1_note) -> Verdict:
        system = build_system_prompt(policy)
        user = build_user_message(action, user_request, stage1_note)
        try:
            output, raw = await self._client.classify(system, user)
        except Stage2Error as exc:
            return self._unavailable(exc.kind, f"classifier unavailable: {exc.kind}")
        except Exception as exc:  # noqa: BLE001 - fail closed on anything
            log.warning("classifier raised an unexpected error", exc_info=True)
            return self._unavailable(
                "unexpected", f"classifier unavailable: unexpected ({type(exc).__name__})"
            )
        return self._verdict_from(output, raw)


def build_classifiers(profile: Profile, http: httpx.AsyncClient) -> dict[str, Classifier]:
    """One classifier per model the profile declares, built once at startup."""
    return {
        name: LLMClassifier(name, config, http)
        for name, config in profile.models.configs.items()
    }
```

`_verdict_from` и `_unavailable` — приватные методы, тела переносятся из `run_stage2` без изменений. Модули `stage2/client.py`, `stage2/prompt.py`, `stage2/schema.py` переезжают в `classify/` как есть (`git mv`), с правкой импортов.

`service/tests/classify/test_llm.py` — перенос `tests/test_stage2_run.py`; вызовы `run_stage2(action, task, P, "m", client, note)` → `LLMClassifier("m", config, http).classify(action, task, policy, note)`. Все ассерты на `res.decision/.model/.error/.raw_response/.reason/.suggest` остаются буквально: `Verdict` даёт те же имена.

- [ ] **Step 3: `Gate` получает реестр классификаторов**

`service/agentgate/engine/gate.py`:
- `__init__(self, profiles, classifiers, rules, state_store, allow_cache_ttl_seconds)`. `http` уходит — его держит классификатор (F7, гайд 2.1).
- `classifiers: Mapping[str, Mapping[str, Classifier]]` — по профилю, потом по имени модели, потому что конфиг модели живёт в профиле.
- Проверка «unknown model» перестаёт быть `try/except KeyError` вокруг `model_config_for` и становится отсутствием ключа:

```python
        classifier = self._classifiers[profile_id].get(request.model or profile.models.default)
        if classifier is None:
            return Verdict.ask("api.unknown-model", f"unknown model '{request.model}'", stage=0)
```

- `_evaluate` вызывает `await context.classifier.classify(action, request.user_request, context.policy, STAGE1_PASSED)`.

`tests/engine/test_gate.py` переходит с `FakeLLM` + `MockTransport` на `FakeClassifier` — исчезает единственная причина, по которой тест пайплайна знал про httpx.

- [ ] **Step 4: `PersistentSessionStateStore`**

`service/tests/session/test_persistent.py`:

```python
async def test_get_or_create_returns_the_restored_state():
    sessions = FakeSessionRepo(states=[state("s1", deny_total=5)])
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.restore()
    assert (await store.get_or_create("s1", "t", "default", "/w")).deny_total == 5


async def test_save_writes_through_to_the_repository():
    sessions = FakeSessionRepo()
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.save(state("s1"))
    assert sessions.upserts == ["s1"]


async def test_restore_reloads_the_valid_allow_cache():
    sessions = FakeSessionRepo(cache=[("s1", "k", "d1", _in_an_hour())])
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.restore()
    assert await store.cache_get("s1", "k") == "d1"


async def test_restore_ignores_an_expired_cache_row():
    sessions = FakeSessionRepo(cache=[("s1", "k", "d1", _an_hour_ago())])
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.restore()
    assert await store.cache_get("s1", "k") is None


async def test_cache_put_writes_through():
    sessions = FakeSessionRepo()
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.cache_put("s1", "k", "d1", 60)
    assert len(sessions.cache_puts) == 1
```

`service/agentgate/session/persistent.py`:

```python
"""In-memory session state with write-through to Postgres.

The spec keeps counters and the allow cache in memory behind
SessionStateStore, writes them through after every decision, and restores
them at startup. That was three pieces of glue in two modules; it is one
implementation of the protocol here, which is also what makes a Redis
store a single new class.
"""

from datetime import datetime, timezone

from agentgate.domain.session import SessionState, SessionStateStore


class PersistentSessionStateStore:
    def __init__(self, inner: SessionStateStore, sessions) -> None:
        self._inner = inner
        self._sessions = sessions

    async def restore(self) -> None:
        self._inner.preload(await self._sessions.load_all())
        now = datetime.now(timezone.utc)
        for session_id, action_hash, decision_id, expires_at in await self._sessions.cache_load_valid():
            ttl_seconds = int((expires_at - now).total_seconds())
            if ttl_seconds > 0:
                await self._inner.cache_put(session_id, action_hash, decision_id, ttl_seconds)

    async def get_or_create(self, session_id, harness, profile_id, workspace) -> SessionState:
        return await self._inner.get_or_create(session_id, harness, profile_id, workspace)

    async def save(self, state: SessionState) -> None:
        await self._inner.save(state)
        await self._sessions.upsert(state)

    async def cache_get(self, session_id: str, key: str) -> str | None:
        return await self._inner.cache_get(session_id, key)

    async def cache_put(self, session_id: str, key: str, decision_id: str, ttl_seconds: int) -> None:
        await self._inner.cache_put(session_id, key, decision_id, ttl_seconds)
```

Заметить: `save` и `cache_put` теперь пишут в Postgres сами, поэтому `PostgresDecisionWriter` перестаёт делать `sessions.upsert` и `sessions.cache_put` — иначе запись случится дважды. Перенести обязанность: writer пишет **только** строку решения, состояние сессии пишет store. FK-порядок сохраняется, потому что `Gate._settle_session` вызывает `save` до того, как решение уходит в writer. Обновить `tests/store/test_writer.py`: тесты `test_postgres_writer_upserts_...`, `test_postgres_writer_caches_an_allow` и соседние переезжают в `tests/session/test_persistent.py`, а у writer остаётся один тест — «вставляет строку решения».

**Проверить порядок явным тестом** в `tests/api/test_app.py`: сессия должна быть записана до строки решения, иначе `IntegrityError` по FK. Тест с реальной БД (`requires_db`), который делает один `POST /v1/decide` в новой сессии и убеждается, что строка решения появилась.

- [ ] **Step 5: `DecisionRecord` уходит, остаётся один маппер**

`service/agentgate/store/mapper.py`:

```python
"""The only place that knows a decision row calls its metadata column
`metadata_` -- SQLAlchemy reserves `metadata` on the declarative base.
"""

from agentgate.engine.decision import DecisionView
from agentgate.store.models import DecisionRow


def row_from_view(view: DecisionView) -> DecisionRow:
    data = view.model_dump(exclude={"decision_id"})
    data["metadata_"] = data.pop("metadata")
    return DecisionRow(**data)


def view_from_row(row: DecisionRow) -> DecisionView:
    return DecisionView.model_validate(
        {c.name: getattr(row, c.name) for c in DecisionRow.__table__.columns}
        | {"metadata": row.metadata_}
    )
```

`service/agentgate/store/repo.py`: удалить `DecisionRecord` целиком (`to_row`, `from_row`, `to_dict`). `DecisionRepo.insert(decision: Decision)` использует `row_from_view(decision.to_view())`; `DecisionRepo.list(...) -> list[DecisionView]` использует `view_from_row`. Докстринги про FK-порядок остаются, ссылки на «Task 10»/«Task 11» вычищаются (гайд 5.3).

`tests/store/test_repo.py` (перенос `test_store.py`): `rec(**over)` строит `Decision` через `tests.factories`; `test_decision_record_to_dict` переезжает в `tests/engine/test_decision.py` (уже написан как `test_view_exposes_both_id_and_decision_id` и соседние) и здесь удаляется.

- [ ] **Step 6: Типизированные границы API**

`service/agentgate/api/schemas.py` — добавить:

```python
class DecisionsPage(BaseModel):
    items: list[DecisionView]
    next_before: str | None = None


class Health(BaseModel):
    status: str
    db: bool
    llm: bool | None = None
```

`service/agentgate/api/app.py`:

```python
    @app.get("/v1/decisions", response_model=DecisionsPage, dependencies=[auth])
    async def decisions(
        session_id: str | None = None,
        model: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        before: str | None = None,
    ) -> DecisionsPage:
        rows = await decision_repo.list(session_id=session_id, model=model, limit=limit, before=before)
        next_before = rows[-1].id if len(rows) == limit else None
        return DecisionsPage(items=rows, next_before=next_before)
```

`dict(r.to_dict(), decision_id=r.id)` исчезает из обоих мест (F13): `DecisionView` уже несёт оба ключа.

`create_app` — типизировать параметры (F12):

```python
def create_app(
    settings: Settings,
    gate: Gate,
    writer: DecisionWriter,
    decision_repo: DecisionRepo,
    profiles: Mapping[str, Profile],
    db_probe: Callable[[], Awaitable[bool]] | None = None,
    key_repo: ApiKeyRepo | None = None,
) -> FastAPI:
```

Шесть параметров вместо восьми и все с типами. `decision_repo=None`/`session_repo=None` ветки удаляются — приложение без хранилища не собирается ни в одном сценарии, а мёртвая ветка `if decision_repo is None: return {"items": []}` маскировала бы ошибку сборки.

`service/agentgate/api/deps.py`: `key_repo: ApiKeyRepo | None`, `now_fn: Callable[[], float]`, `background: BackgroundTasks` без `# type: ignore` — FastAPI всегда инжектит настоящий экземпляр, поэтому `= None` заменить на честный `BackgroundTasks` без дефолта. Если это ломает прямой вызов `require_token` в `tests/api/test_deps.py`, тест передаёт `BackgroundTasks()` явно.

- [ ] **Step 7: `bootstrap.py` — единственный composition root**

`service/agentgate/bootstrap.py`:

```python
"""Where the service is assembled.

Everything above this module depends on protocols; this is the one place
that knows which implementations are used in production. Tests and the
CLI call it with substitutes rather than assembling their own variants.
"""

from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from agentgate.api.app import create_app
from agentgate.classify.llm import build_classifiers
from agentgate.config import Settings
from agentgate.engine.gate import Gate
from agentgate.log.jsonl import JsonlLogger
from agentgate.profiles.loader import load_profiles
from agentgate.rules.chain import STAGE1
from agentgate.session.memory import InMemorySessionStateStore
from agentgate.session.persistent import PersistentSessionStateStore
from agentgate.store.db import make_engine, make_session_factory
from agentgate.store.keys import ApiKeyRepo
from agentgate.store.repo import DecisionRepo, SessionRepo
from agentgate.store.writer import CompositeDecisionWriter, JsonlDecisionWriter, PostgresDecisionWriter


@dataclass(frozen=True)
class Service:
    app: FastAPI
    gate: Gate
    engine: AsyncEngine
    settings: Settings


async def build_service(
    settings: Settings,
    *,
    http: httpx.AsyncClient | None = None,
    state_store=None,
    writer=None,
) -> Service:
    """Assemble the service. Every collaborator can be substituted, so a
    test never has to reproduce this wiring to change one piece of it.

    Raises SystemExit if the configured default profile does not exist,
    and whatever `validate_token_for_bind` raises for an unsafe
    token/bind combination -- both are startup failures.
    """
    settings.validate_token_for_bind()
    profiles = load_profiles(settings.profiles_dir)
    if settings.default_profile not in profiles:
        raise SystemExit(
            f"default profile '{settings.default_profile}' not found in {settings.profiles_dir}"
        )

    engine = make_engine(settings.db_url)
    session_factory = make_session_factory(engine)
    decisions, sessions = DecisionRepo(session_factory), SessionRepo(session_factory)

    store = state_store or PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    if hasattr(store, "restore"):
        await store.restore()

    http = http or httpx.AsyncClient()
    classifiers = {name: build_classifiers(profile, http) for name, profile in profiles.items()}
    gate = Gate(profiles, classifiers, STAGE1, store, settings.allow_cache_ttl_seconds)

    writer = writer or CompositeDecisionWriter([
        JsonlDecisionWriter(JsonlLogger(settings.log_path)),
        PostgresDecisionWriter(decisions),
    ])
    app = create_app(
        settings, gate, writer, decisions, profiles,
        db_probe=_make_db_probe(engine), key_repo=ApiKeyRepo(session_factory),
    )
    return Service(app=app, gate=gate, engine=engine, settings=settings)
```

`service/agentgate/__main__.py` сокращается до разбора аргументов и `uvicorn.run`:

```python
def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "keys":
        from agentgate.cli import run_keys_cli

        sys.exit(run_keys_cli(sys.argv[2:]))

    logging.basicConfig(level=logging.INFO)
    service = asyncio.run(build_service(get_settings()))
    uvicorn.run(service.app, host=service.settings.bind_host, port=service.settings.bind_port)
```

`service/agentgate/cli.py`: `_build_repo` удаляется, CLI берёт движок из `build_service`… но CLI не должен поднимать весь сервис ради выпуска ключа. Вместо этого вынести в `bootstrap.py` вторую, меньшую функцию:

```python
def build_key_repo(settings: Settings) -> tuple[ApiKeyRepo, AsyncEngine]:
    """The store the keys CLI needs, without assembling the HTTP service."""
    engine = make_engine(settings.db_url)
    return ApiKeyRepo(make_session_factory(engine)), engine
```

Знание «как построить движок и фабрику сессий» остаётся в одном модуле (F15, гайд 1.3), а CLI не тянет за собой профили и HTTP-клиент.

- [ ] **Step 8: Убрать три `monkeypatch.setattr` (гайд 6.1)**

- `tests/test_main.py:77` подменял `InMemorySessionStateStore` в модуле, чтобы проверить восстановление состояния. Теперь `build_service(settings, state_store=CapturingStore(...))` — подстановка через параметр. Файл переезжает в `tests/test_bootstrap.py`.
- `tests/test_cli_keys.py:176-179` подменял `build_app`, `uvicorn.run` и `sys.argv`. `sys.argv` — граница процесса, её подмена допустима (гайд 7.1), но `run_keys_cli(["create", "--label", "smoke"])` вызывается напрямую с аргументами, и подменять `sys.argv` больше не нужно. Проверку «ветка keys не поднимает сервер» переписать на утверждение о том, что `run_keys_cli` не обращается к профилям и HTTP: достаточно вызвать его с фейковым репозиторием ключей и убедиться в результате.
- `tests/test_session.py:61-74` подменял `time.monotonic`. Ввести часы как зависимость: `InMemorySessionStateStore(now=time.monotonic)`, тест передаёт `FakeClock` из `tests/factories.py`:

```python
class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds
```

Оба TTL-теста (`..._roundtrip_and_cache_ttl`, `..._cache_ttl_exact_boundary_expires`) переписать на `FakeClock`, разделив первый на два (гайд 6.5): «состояние переживает round-trip» и «запись истекает по TTL» — разные факты.

Проверка:
```bash
cd service && grep -rn "monkeypatch.setattr" tests/
```
Expected: пусто. `monkeypatch.setenv`/`delenv` остаются — это граница окружения.

- [ ] **Step 9: Прогон, контракт, коммит**

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: всё зелёное, включая e2e (`tests/e2e/test_e2e.py` — обновить сборку приложения на `build_service`).

Run:
```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff ../contracts
```
Expected: `decide_request`/`decide_response` без изменений. `openapi.yaml` может измениться, если ручная схема `/v1/decisions` расходится с `DecisionsPage` — **это и есть проверка**: расхождение означает, что модель написана неверно (контракт — источник истины), а не что yaml устарел. Привести модель к yaml, не наоборот.

```bash
git add service/agentgate service/tests service/../contracts
git commit -m "refactor(service): four protocols and one composition root

Classifier replaces Gate's inline LLMClient construction and the
model-lookup KeyError; PersistentSessionStateStore absorbs the
write-through and restore glue that lived in app.py and __main__.py;
DecisionRecord's three hand-written mappers collapse into store/mapper.py
over DecisionView; bootstrap.build_service is the only place that names
production implementations. API boundaries are typed and the three
monkeypatch.setattr call sites become injected fakes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

