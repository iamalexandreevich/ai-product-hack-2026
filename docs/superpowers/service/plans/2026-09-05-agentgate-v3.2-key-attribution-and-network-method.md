# AgentGate v3.2 — атрибуция к ключу, `Idempotency-Key` в границах предъявителя, `method` у сетевого действия. План реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** решение становится атрибутируемым выданному API-ключу, повтор по `Idempotency-Key` перестаёт быть глобальным и живёт в границах предъявителя, а `tool: network` получает `method`, без которого доверенный домен по-прежнему ничего не разрешает.

**Architecture:** `key_id` не входит в движок: зависимость аутентификации возвращает его значением, а `api/app.py::_answer` навешивает его на исход тем же `dataclasses.replace`, которым уже навешивается `idempotency_key`. В базе появляется nullable `decisions.key_id` и **генерируемый** столбец `principal = coalesce(key_id, 'token')`, по которому и идёт уникальность повторов — обычные столбцы в `ON CONFLICT` вместо индекса по выражению и без второго записываемого поля, способного разъехаться с `key_id`. Ключ хранилища повторов собирает тип `ReplayKey`, так что протокол `ReplayStore` остаётся односоставным. `method` — необязательное поле `ActionArgs` с закрытым множеством значений; `ProfileDomainTrustedRule` получает вторую, короткую ветку для `tool: network`, где `None` не квалифицируется никогда.

**Tech Stack:** Python 3.12, uv, FastAPI, pydantic v2, SQLAlchemy 2 (async) + asyncpg, Alembic, bashlex, httpx, pytest + pytest-asyncio, Postgres 16.

**Spec:** `docs/superpowers/service/specs/2026-09-05-agentgate-v3.2-key-attribution-and-network-method-design.md`. Номера разделов ниже — оттуда. Спека закрыта, открытых вопросов владельцу нет.

**Сопутствующие документы:** `docs/superpowers/service/specs/api-keys.md` (раздел «Что попадает в решения и в логи» — источник требования по `key_id`); спека v3 `2026-09-04-agentgate-v3-rules-and-inspect-design.md` §4 (повтор, `DecisionRecord`); спека v3.1 §5 (одиннадцать условий доверенного домена); план v3.1 `2026-09-05-agentgate-v3.1-strictness-mcp-domains.md` — образец формата; `service/CLAUDE.md` — инварианты и карта модулей; корневой `CLAUDE.md` — «Известные ограничения», три пункта из которых закрываются здесь.

**Ветка:** `feat/v3.2-key-attribution` от `main` (`cbe3073`). Исполнителю в worktree: перед началом `git rev-parse HEAD` должен показать `cbe3073`; если база не та — остановиться и сообщить.

## Сверка с v4

v4 (Context Guard) слит в `main` и развёрнут на сервере — v3.2 строится поверх него, а не параллельно ему. Что это меняет по сравнению с первой редакцией плана:

- **Голова Alembic одна.** v4 занял `0006` (`migrations/versions/0006_v4_spans_and_redaction.py`, `revision = '0006'`), наша миграция — `0007_key_attribution.py`, `revision = "0007"`, `down_revision = "0006"`. Никаких двух голов, никакой merge-ревизии, никакого «сначала v3.2, потом v4»: `alembic upgrade head` проходит линейно.
- **Поля v4 уже в дереве.** `DecisionRecord` заканчивается на `spans`/`redacted`/`spans_rejected`; `DecisionRow` — тем же тремя столбцами; `Inspection` несёт `spans`, `redacted`, `spans_rejected`, `redacted_output`; `InspectResponse` — `spans` и `redacted`. Все «после какого поля» ниже указаны по этому дереву.
- **Структура плана не изменилась:** те же 7 задач в тех же 4 волнах. Ни одно решение спеки v4 не отменил — `as_cached` по-прежнему отбрасывает поля вызова (`key_id` встаёт в тот же перечень), `replace(outcome, key_id=…)` работает на обоих frozen-датаклассах, а форма ответа v3.2 не трогает ни `spans`, ни `redacted`.
- **Базовое число тестов — 1446** (`uv run pytest -q --collect-only` на `cbe3073`).

---

## Global Constraints

Действуют в каждой задаче, в шагах не повторяются.

**Поведение**

- Обратная совместимость (§7.1.9): запрос без `method`, вызов под статическим токеном и профиль с `trusted_allows: false` дают вердикты, идентичные v3.1, байт в байт. Ни одно существующее ожидание в `tests/rules/`, `tests/engine/`, `tests/classify/` не меняется по смыслу.
- Fail-closed: ошибка, таймаут, невалидный запрос, невалидный ответ модели → `ask`, HTTP 200. Ошибка проверки ключа → 401, никогда не пропуск. Ошибка хранилища повторов → «повтора нет», никогда 5xx. **Любой путь, возвращающий `allow`, имеет тест на путь отказа** — это касается новой ветки `profile.domain-trusted` для `tool: network`.
- Ни один вердикт не зависит от `key_id` (§7.1.1). `key_id` не попадает в `DecideResponse`/`InspectResponse` (§7.1.3). Сам ключ и его хэш не попадают никуда, включая лог-сообщения (§7.1.2).
- `method: None` не квалифицируется никогда (§7.1.7).
- Решение по сырой строке запрещено: метод для `tool: shell` по-прежнему читается из argv правилом, поле `args.method` для shell игнорируется.
- Бюджет ступени 1: p50 ≤ 1 мс, существующий `tests/rules/test_latency.py` остаётся зелёным.
- Только Postgres; тесты с БД под `requires_db`.

**Контракт**

- Провод меняется в задачах 2 (`args.method`) и 3 (`key_id` в `DecisionRecord`, query-параметр `key_id`). В этих двух задачах контракты перегенерируются:
  ```bash
  cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py
  ```
- В любой другой задаче контракт проверяется, а не перегенерируется:
  ```bash
  cd service && git diff --exit-code ../contracts
  ```
- `DecideResponse` и `InspectResponse` не меняются ни одним полем.
- Изменение JSON-схемы запроса — PR обязан упоминать три направления: service, adapters, benchmark (правило `contracts/README.md`).

**Процесс**

- TDD: сначала падающий тест, потом минимальная реализация. Тест, который не падал до реализации, не считается тестом.
- Код и комментарии — английский; документация — русский; идентификаторы API не переводятся. Комментарий — только неочевидное «почему»; ссылок на задачи, PR, даты в коде нет.
- Все команды — из `service/`, через `uv run`. Полный прогон перед каждым коммитом:
  ```bash
  cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q
  ```
  Ниже эта строка сокращается до `<FULL>`. Базовая сборка на `cbe3073` — **1446 тестов**; полный прогон в любой задаче должен собирать не меньше (`uv run pytest -q --collect-only | tail -1`), падение числа означает потерянный файл, а не «оптимизацию».
- Коммит только явных путей: `git commit --only <пути> -m "…"`, никогда `git add … && git commit`. Сообщение заканчивается строкой `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Зона записи: `service/`, `contracts/`, `docs/`, корневой `CLAUDE.md`. Ничего в `adapters/`, `benchmark/`, `frontend/` не меняется — бенчмарк только запускается.
- Отчёт по завершении: `docs/reports/task-25-v3.2-key-attribution-and-network-method.md` (24 занят веткой v4).

**Отношение к v4**

v4 уже в `main` (см. «Сверка с v4»), поэтому раздела про порядок слияния и конфликты больше нет: конфликтовать не с чем. Единственное следствие — миграция продолжает цепочку номером `0007`, а «после какого поля» в снипетах ниже отсчитывается от полей, которые добавил v4.

## Карта файлов

| Файл | Действие | Ответственность | Задача |
|---|---|---|---|
| `agentgate/api/deps.py` | изменить | `require_token` возвращает `key_id \| None` | 1 |
| `agentgate/engine/decision.py` | изменить | `DecisionRecord.key_id`, `Decision.key_id`, проброс в `to_record` | 1 |
| `agentgate/engine/inspection.py` | изменить | `Inspection.key_id`, проброс в `to_record`, отсутствие в `as_cached` | 1 |
| `agentgate/store/protocols.py` | изменить | `Stored.key_id` | 1 |
| `agentgate/api/app.py` | изменить | `Outcome.key_id`, параметр маршрутов, `replace(outcome, key_id=…)` (1); фильтр `?key_id=` (3); принципал в повторе (5) | 1 / 3 / 5 |
| `agentgate/api/schemas.py` | изменить | `HTTP_METHODS`, `ActionArgs.method` | 2 |
| `agentgate/normalize/__init__.py` | изменить | `method` в ветке `network` | 2 |
| `agentgate/normalize/model.py` | изменить | `NormalizedAction.method` | 2 |
| `agentgate/classify/prompt.py` | изменить | строка `method=` в `[ACTION]` | 2 |
| `agentgate/store/models.py` | изменить | `key_id`, генерируемый `principal`, два индекса | 3 |
| `migrations/versions/0007_key_attribution.py` | создать | колонки и перестановка уникального индекса | 3 |
| `agentgate/store/repo.py` | изменить | цель `ON CONFLICT`, фильтр `key_id` в `list` | 3 |
| `agentgate/store/mapper.py` | изменить | исключить генерируемый `principal` | 3 |
| `agentgate/rules/profile_domain_trusted.py` | изменить | ветка `tool: network` | 4 |
| `agentgate/domain/replay.py` | изменить | `STATIC_PRINCIPAL`, `principal_of`, `ReplayKey`, `Replay.principal`, `answers(request, principal)` | 5 |
| `agentgate/session/replay.py` | изменить | составной ключ при восстановлении | 5 |
| `tests/factories.py` | изменить | `network_action`, `key_id` в `decision()`/`inspection()` | 1, 2 |
| `tests/api/test_deps.py` | изменить | возвращаемое значение зависимости | 1 |
| `tests/api/test_app.py` | изменить | `key_id` в строке и JSONL, не в ответе (1); фильтр (3); чужой принципал (5) | 1 / 3 / 5 |
| `tests/engine/test_decision.py`, `tests/engine/test_inspection.py` | изменить | `key_id` в проекции | 1 |
| `tests/test_schemas.py`, `tests/normalize/test_init.py`, `tests/classify/test_prompt.py` | изменить | `method`: валидация, нормализация, промпт | 2 |
| `tests/store/test_repo.py` | изменить | два принципала — две строки, фильтр, `load_replayable` | 3, 5 |
| `tests/rules/test_profile_domain_trusted.py` | изменить | таблица ветки `network` | 4 |
| `tests/domain/test_replay.py` | создать | `principal_of`, `ReplayKey`, `answers` | 5 |
| `tests/session/test_replay.py` | изменить | восстановление под составным ключом | 5 |
| `contracts/decide_request.schema.json`, `contracts/openapi.yaml`, `contracts/README.md` | изменить | перегенерация и таблица полей | 2, 3, 6 |
| `docs/connect.md`, `CLAUDE.md`, `service/CLAUDE.md` | изменить | документация | 6 |
| `docs/reports/task-25-v3.2-key-attribution-and-network-method.md` | создать | отчёт | 7 |

---

## Волна 1 — фундамент

### Task 1: `key_id` доезжает до записи

Закрывает §3.1–§3.3 без базы: поле есть в исходе, в записи и в JSONL, столбца в Postgres пока нет (задача 3). Поведение решений не меняется ни на один вердикт.

**Files:**
- Modify: `service/agentgate/api/deps.py`, `service/agentgate/engine/decision.py`, `service/agentgate/engine/inspection.py`, `service/agentgate/store/protocols.py`, `service/agentgate/api/app.py`, `service/tests/factories.py`
- Test: `service/tests/api/test_deps.py`, `service/tests/api/test_app.py`, `service/tests/engine/test_decision.py`, `service/tests/engine/test_inspection.py`

- [ ] **Step 1: Падающий тест на зависимость**

Дописать в `service/tests/api/test_deps.py`. В файле уже есть `_settings(token=...)`, `_valid_record(key_id="k1")` (`ApiKeyRecord` без `key_hash`), `FakeKeyRepo(by_hash=...)`, `_authenticate(require_token, authorization)` и `_expect_401`. Существующий `_authenticate` возвращает `BackgroundTasks`, а не значение зависимости, — новые тесты зовут `require_token` напрямую, чтобы проверить именно возвращаемое значение, и `_authenticate` не трогают:

```python
# --- what the dependency returns: the key_id the call is attributed to ------


async def test_require_token_returns_the_key_id_of_a_valid_key():
    plaintext = "agk_" + "a" * 20
    repo = FakeKeyRepo(by_hash={hash_key(plaintext): _valid_record("key-7")})
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)

    key_id = await require_token(background=BackgroundTasks(), authorization=f"Bearer {plaintext}")

    assert key_id == "key-7"


async def test_require_token_returns_none_for_the_static_token():
    require_token = make_require_token(_settings(token="secret"), key_repo=FakeKeyRepo())

    result = await require_token(background=BackgroundTasks(), authorization="Bearer secret")

    assert result is None


async def test_require_token_returns_none_when_no_token_is_configured():
    require_token = make_require_token(_settings(token=None))

    result = await require_token(background=BackgroundTasks(), authorization=None)

    assert result is None


async def test_the_static_token_wins_over_a_key_with_the_same_bearer():
    # Losing the attribution is safer than attributing to the wrong holder.
    repo = FakeKeyRepo(by_hash={hash_key("secret"): _valid_record("key-7")})
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)

    result = await require_token(background=BackgroundTasks(), authorization="Bearer secret")

    assert result is None
```

Новых импортов файл не требует: `BackgroundTasks`, `hash_key`, `make_require_token` в нём уже есть.

- [ ] **Step 2: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/api/test_deps.py -q -k "returns or wins"`
Expected: FAIL у первого теста — `assert None == 'key-7'` (зависимость сейчас возвращает `None` всегда). Остальные три зелёные и до реализации: они фиксируют, что она их не сломает.

- [ ] **Step 3: Реализация в `deps.py`**

В `agentgate/api/deps.py` заменить тело `require_token` (сигнатура получает тип результата):

```python
    async def require_token(
        background: BackgroundTasks,
        authorization: str | None = Header(default=None, include_in_schema=False),
    ) -> str | None:
        """The `key_id` this call is attributed to, or None.

        None means "authenticated, but not by an issued key": the static
        `AGENTGATE_TOKEN`, or a localhost dev bind with no token at all.
        The static token is checked first, so a bearer that somehow matches
        both loses its attribution rather than gaining someone else's.
        """
        if expected is None:
            return None
        if authorization is not None and secrets.compare_digest(authorization, expected):
            return None
        if verifier is not None and authorization is not None and authorization.startswith(_BEARER_PREFIX):
            token = authorization[len(_BEARER_PREFIX):]
            key_id = await verifier.verify(token)
            if key_id is not None:
                background.add_task(_touch_last_used_safe, key_repo, key_id)
                return key_id
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
```

И дописать в docstring модуля абзац:

```python
The dependency's value is the resolved `key_id` (None for the static
token), which api/app.py attaches to the outcome so a decision can be
attributed to the credential that asked for it. The key itself and its
hash never leave this module.
```

- [ ] **Step 4: Прогнать**

Run: `cd service && uv run pytest tests/api/test_deps.py -q`
Expected: PASS, включая все прежние тесты 401/кэша.

- [ ] **Step 5: Падающие тесты на проекцию исхода**

В `service/tests/engine/test_decision.py`:

```python
def test_a_decision_carries_its_key_id_into_the_record():
    record = decision(key_id="01HZKEY").to_record()

    assert record.key_id == "01HZKEY"


def test_a_decision_without_a_key_records_none():
    assert decision().to_record().key_id is None
```

В `service/tests/engine/test_inspection.py`:

```python
def test_an_inspection_carries_its_key_id_into_the_record():
    assert inspection(key_id="01HZKEY").to_record().key_id == "01HZKEY"


def test_a_cache_hit_does_not_inherit_the_key_id_of_the_call_that_filled_it():
    hit = inspection(key_id="01HZKEY").as_cached(
        "01HZNEW", inspect_request(), Latency(total_ms=1), WORKSPACE
    )

    assert hit.key_id is None
```

(`inspection`, `inspect_request`, `Latency`, `WORKSPACE` уже импортируются в этих файлах; если нет — импортировать из `tests.factories` и `agentgate.engine.timings`.)

- [ ] **Step 6: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/engine/test_decision.py tests/engine/test_inspection.py -q -k key_id`
Expected: FAIL — `TypeError: Decision.__init__() got an unexpected keyword argument 'key_id'`.

- [ ] **Step 7: Реализация в `decision.py`**

В `DecisionRecord` — последним полем, после `spans_rejected` (последнего из трёх, что добавил v4):

```python
    key_id: str | None = Field(
        default=None,
        description=(
            "ULID of the issued API key this call was authenticated with; `null` for "
            "the static `AGENTGATE_TOKEN` and for a localhost bind with no token."
        ),
    )
```

В `Decision` — последним полем, после `idempotency_key`:

```python
    key_id: str | None = None
```

В `Decision.to_record()` — последним аргументом, после `cost=self.verdict.cost` (у `Decision` он и сегодня последний: спанов у решения нет, они только у инспекции):

```python
            key_id=self.key_id,
```

- [ ] **Step 8: Реализация в `inspection.py`**

В `Inspection` — сразу после `idempotency_key` (то есть перед `cost`; поля v4 `spans`, `redacted`, `spans_rejected`, `redacted_output` идут ниже и не двигаются):

```python
    key_id: str | None = None
```

В `Inspection.to_record()` — последним аргументом, после `spans_rejected=self.spans_rejected`:

```python
            key_id=self.key_id,
```

`as_cached` не трогать: он намеренно не переносит поля, принадлежащие вызову, а не содержимому, — `key_id` относится ровно к ним (и ровно поэтому же он ведёт себя как `spans_rejected`, а не как `spans`). В docstring `as_cached` дописать `key_id` в существующий перечень:

```
        judged and how. Everything specific to the call that produced it --
        `error`, `raw_response`, `idempotency_key`, `key_id` -- is dropped rather
        than copied, since the new call had none of those; carrying them forward
```

- [ ] **Step 9: Реализация в `protocols.py`**

В `Stored`:

```python
class Stored(Protocol):
    id: str
    idempotency_key: str | None
    key_id: str | None

    def to_record(self) -> DecisionRecord: ...
    def allow_cache_entry(self) -> tuple[str, str] | None: ...
    def session_state(self) -> SessionState | None: ...
    def session_ref(self) -> tuple[str, str] | None: ...
```

(остальные четыре метода уже там — приведены, чтобы `key_id` встал над ними, а не между ними.)

- [ ] **Step 10: Прогнать**

Run: `cd service && uv run pytest tests/engine -q`
Expected: PASS.

- [ ] **Step 11: Падающий тест на маршрут**

В `service/tests/api/test_app.py` (рядом с существующими тестами ключей, где уже есть `FakeKeyRepo` и `hash_key`):

```python
async def test_a_decision_made_with_a_key_records_the_key_id(tmp_path):
    from agentgate.store.keys import hash_key

    plaintext = "agk_" + "b" * 43
    app, drepo, _, _ = build(tmp_path, token="secret", key_repo=FakeKeyRepo({hash_key(plaintext): "key-9"}))

    response = await call(app, "POST", "/v1/decide", json=body(),
                          headers={"authorization": f"Bearer {plaintext}"})

    assert response.status_code == 200
    assert "key_id" not in response.json()
    assert drepo.rows[-1].to_record().key_id == "key-9"
    logged = json.loads((tmp_path / "d.jsonl").read_text().splitlines()[-1])
    assert logged["key_id"] == "key-9"


async def test_a_decision_made_with_the_static_token_records_no_key_id(tmp_path):
    app, drepo, _, _ = build(tmp_path, token="secret")

    await call(app, "POST", "/v1/decide", json=body(), headers={"authorization": "Bearer secret"})

    assert drepo.rows[-1].to_record().key_id is None


async def test_an_inspect_verdict_records_the_key_id_too(tmp_path):
    from agentgate.store.keys import hash_key

    plaintext = "agk_" + "c" * 43
    app, drepo, _, _ = build(tmp_path, token="secret", key_repo=FakeKeyRepo({hash_key(plaintext): "key-9"}))

    await call(app, "POST", "/v1/inspect", json=inspect_body(),
               headers={"authorization": f"Bearer {plaintext}"})

    assert drepo.rows[-1].to_record().key_id == "key-9"
```

- [ ] **Step 12: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/api/test_app.py -q -k key_id`
Expected: FAIL — `assert None == 'key-9'`.

- [ ] **Step 13: Реализация в `app.py`**

В протоколе `Outcome` (рядом с `idempotency_key`):

```python
class Outcome(Protocol):
    """What `/v1/decide` and `/v1/inspect` both produce: `Decision` and
    `Inspection` share this shape without a common base class."""

    idempotency_key: str | None
    key_id: str | None

    def to_record(self) -> Any: ...
    def to_response(self) -> Any: ...
```

В `_answer` — новый параметр и одна строка после успешного прогона движка:

```python
async def _answer(
    request: Request,
    background: BackgroundTasks,
    spec: RouteSpec,
    replay: ReplayStore,
    settings: Settings,
    writer: DecisionWriter,
    key_id: str | None,
) -> Any:
    ...
    try:
        outcome = await spec.run(parsed)
    except Exception as exc:  # noqa: BLE001 - fail-closed: no exception may escape as a 500
        log.exception("%s failed", spec.log_label)
        return spec.refuse("api.internal-error", f"internal error: {type(exc).__name__}")
    # Attribution is attached before the replay entry is built: a restored
    # replay must carry the same key_id the live decision did.
    outcome = replace(outcome, key_id=key_id)
    if key is not None:
        ...
```

В `create_app` — зависимость становится значением:

Сегодня это одна строка (`auth = Depends(make_require_token(...))`); она сохраняется, рядом добавляется алиас:

```python
    auth = Depends(make_require_token(settings, key_repo=key_repo, cache_ttl_seconds=settings.api_key_cache_ttl_seconds))
    # Routes that need the credential's identity take it as a parameter:
    # `dependencies=[auth]` runs the dependency but throws its value away.
    KeyId = Annotated[str | None, auth]
```

Маршруты `decide` и `inspect`: убрать `dependencies=[auth]` из декоратора, добавить параметр:

```python
    async def decide(request: Request, background: BackgroundTasks, key_id: KeyId) -> DecideResponse:
        ...
        return await _answer(request, background, decide_spec, replay, settings, writer, key_id)
```

```python
    async def inspect(request: Request, background: BackgroundTasks, key_id: KeyId) -> InspectResponse:
        ...
        return await _answer(request, background, inspect_spec, replay, settings, writer, key_id)
```

Маршруты `/v1/decisions`, `/v1/profiles/{id}` не трогать: они значение не используют и остаются на `dependencies=[auth]`.

- [ ] **Step 14: Фабрики**

В `service/tests/factories.py` — ничего не добавлять руками: `decision(**overrides)` и `inspection(**overrides)` уже прокидывают произвольные поля в конструктор датакласса, поэтому `decision(key_id="…")` работает сразу после Step 7. Проверить это прогоном Step 6 — если падает не на `key_id`, а на чём-то другом, фабрика требует правки, и она делается здесь.

- [ ] **Step 15: Прогнать всё**

Run: `cd service && <FULL>`
Expected: PASS. Отдельно убедиться, что 401-тесты и тесты OpenAPI не поехали: перенос авторизации из `dependencies` в параметр не должен менять документ (`Depends` тот же объект).

Run: `cd service && git diff --exit-code ../contracts`
Expected: пустой diff.

- [ ] **Step 16: Коммит**

```bash
git commit --only service/agentgate/api/deps.py service/agentgate/api/app.py service/agentgate/engine/decision.py service/agentgate/engine/inspection.py service/agentgate/store/protocols.py service/tests/api/test_deps.py service/tests/api/test_app.py service/tests/engine/test_decision.py service/tests/engine/test_inspection.py -m "feat(api): a decision is attributed to the key that asked for it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `method` в контракте, в действии и в промпте

Закрывает §5.1, §5.2 и §5.4. Правило пока не меняется — поле есть, никто по нему ничего не разрешает.

**Files:**
- Modify: `service/agentgate/api/schemas.py`, `service/agentgate/normalize/__init__.py`, `service/agentgate/normalize/model.py`, `service/agentgate/classify/prompt.py`, `service/tests/factories.py`
- Test: `service/tests/test_schemas.py`, `service/tests/normalize/test_init.py`, `service/tests/classify/test_prompt.py`

- [ ] **Step 1: Падающие тесты схемы и нормализации**

В `service/tests/test_schemas.py`:

```python
def test_network_method_is_uppercased():
    request = DecideRequest.model_validate(
        {"harness": "t", "tool": "network", "args": {"cwd": "/w", "domains": ["github.com"], "method": "get"},
         "user_request": "x"}
    )

    assert request.args.method == "GET"


def test_an_unknown_method_is_refused():
    with pytest.raises(ValidationError):
        DecideRequest.model_validate(
            {"harness": "t", "tool": "network", "args": {"cwd": "/w", "method": "TRACE"}, "user_request": "x"}
        )


def test_method_defaults_to_none():
    request = DecideRequest.model_validate(
        {"harness": "t", "tool": "network", "args": {"cwd": "/w"}, "user_request": "x"}
    )

    assert request.args.method is None
```

В `service/tests/normalize/test_init.py`:

```python
def test_a_network_action_carries_its_method():
    action = normalize(DecideRequest.model_validate(
        {"harness": "t", "tool": "network", "args": {"cwd": "/w", "domains": ["GitHub.com"], "method": "HEAD"},
         "user_request": "x"}
    ))

    assert action.method == "HEAD" and action.domains == ["github.com"]


def test_a_shell_action_has_no_method_even_when_the_request_carries_one():
    action = normalize(DecideRequest.model_validate(
        {"harness": "t", "tool": "shell", "raw": "curl -X DELETE https://github.com/o/r",
         "args": {"cwd": "/w", "method": "GET"}, "user_request": "x"}
    ))

    assert action.method is None
```

- [ ] **Step 2: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/test_schemas.py tests/normalize/test_init.py -q -k method`
Expected: FAIL — `AttributeError: 'ActionArgs' object has no attribute 'method'` и, у теста с `TRACE`, `DID NOT RAISE`.

- [ ] **Step 3: Реализация в `schemas.py`**

Рядом с прочими константами лимитов:

```python
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
```

В `ActionArgs`, после `domains`:

```python
    method: str | None = Field(
        default=None,
        description=(
            "HTTP method of a `network` action, uppercase: GET, HEAD, POST, PUT, PATCH, "
            "DELETE, OPTIONS. Optional — omitted means unknown, and unknown never earns "
            "a positive verdict. Ignored for tools other than `network`."
        ),
    )

    @field_validator("method")
    @classmethod
    def _known_method(cls, v: str | None) -> str | None:
        # A closed set, so a rule never has to argue that an arbitrary verb
        # is a read: the schema settles it once.
        if v is None:
            return None
        method = v.strip().upper()
        if method not in HTTP_METHODS:
            raise ValueError(f"unknown HTTP method {v!r}")
        return method
```

- [ ] **Step 4: Реализация в `normalize/model.py` и `normalize/__init__.py`**

В `NormalizedAction`, после `mcp`:

```python
    method: str | None = None
```

Дописать в docstring модуля:

```
`method` is the HTTP method of a `network` action, already validated
against a closed set by the schema. A shell command's method lives in
its argv and is read there by the rule -- there is no second source.
```

В `normalize()`:

```python
    if req.tool is Tool.network:
        domains = sorted({d.lower() for d in req.args.domains})
        return NormalizedAction(
            tool=req.tool, cwd=cwd, raw=req.raw, domains=domains, method=req.args.method
        )
```

- [ ] **Step 5: Прогнать**

Run: `cd service && uv run pytest tests/test_schemas.py tests/normalize -q`
Expected: PASS.

- [ ] **Step 6: Падающий тест на промпт**

В `service/tests/classify/test_prompt.py`:

```python
def test_the_action_block_shows_the_method_of_a_network_action():
    action = network_action(method="GET")

    message = build_user_message(action, "fetch it", Dialogue.of([]), "no rule matched")

    assert "\nmethod=GET\n" in message


def test_the_action_block_has_no_method_line_without_one():
    message = build_user_message(network_action(), "fetch it", Dialogue.of([]), "no rule matched")

    assert "method=" not in message
```

И новая фабрика в `service/tests/factories.py`, рядом с `mcp_action`:

```python
def network_action(
    domains: tuple[str, ...] = ("github.com",), method: str | None = None, cwd: str = WORKSPACE
) -> NormalizedAction:
    """What a `tool: network` request normalizes to: domains and a method, no commands."""
    args = {"cwd": cwd, "domains": list(domains)}
    if method is not None:
        args["method"] = method
    return normalize(DecideRequest(harness="t", tool="network", raw="", args=args, user_request="x"))
```

(в `tests/classify/test_prompt.py` импортировать `network_action` из `tests.factories`.)

- [ ] **Step 7: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/classify/test_prompt.py -q -k method`
Expected: FAIL — `assert '\nmethod=GET\n' in ...`.

- [ ] **Step 8: Реализация в `prompt.py`**

В `build_user_message`, сразу после строки с `argv` для shell и перед блоком `mcp`:

```python
    if action.method is not None:
        # Already constrained to a closed set of verbs by the schema, so it
        # cannot carry a newline and needs no escaping.
        lines.append(f"method={action.method}")
```

- [ ] **Step 9: Прогнать промпт целиком**

Run: `cd service && uv run pytest tests/classify -q`
Expected: PASS, включая `test_empty_dialogue_renders_the_v1_message_byte_for_byte` и `test_user_message_layout_and_blindness` — они про запросы без метода, где новая строка не появляется (§7.1.10).

- [ ] **Step 10: Перегенерация контрактов**

```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py
git diff --stat ../contracts
```
Expected: изменены `contracts/decide_request.schema.json` и `contracts/openapi.yaml`, оба — добавленным свойством `method` у `ActionArgs`. `decide_response.schema.json`, `inspect_request.schema.json`, `inspect_response.schema.json` не изменены.

- [ ] **Step 11: Прогнать всё**

Run: `cd service && <FULL>`
Expected: PASS, включая `tests/test_contracts.py`.

Отдельно проверить, что allow-кэш прогревается, а не ломается: `action_hash()` теперь включает `method`, поэтому старые ключи просто не совпадут.

Run: `cd service && uv run pytest tests/session/test_cache_key.py tests/engine/test_gate.py -q`
Expected: PASS.

- [ ] **Step 12: Коммит**

```bash
git commit --only service/agentgate/api/schemas.py service/agentgate/normalize/__init__.py service/agentgate/normalize/model.py service/agentgate/classify/prompt.py service/tests/factories.py service/tests/test_schemas.py service/tests/normalize/test_init.py service/tests/classify/test_prompt.py contracts/decide_request.schema.json contracts/openapi.yaml -m "feat(schemas,normalize): a network action can say its HTTP method

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Волна 2 — база и правило

### Task 3: столбец, генерируемый принципал, миграция, фильтр ленты

Закрывает §3.3 (строка и индекс), §3.4 (фильтр), §4.5 (уникальность) и §7.4 (миграция). Требует задачу 1: без `DecisionRecord.key_id` вставлять нечего.

**Files:**
- Modify: `service/agentgate/store/models.py`, `service/agentgate/store/repo.py`, `service/agentgate/store/mapper.py`, `service/agentgate/api/app.py`
- Create: `service/migrations/versions/0007_key_attribution.py`
- Test: `service/tests/store/test_repo.py`, `service/tests/api/test_app.py`

- [ ] **Step 1: Падающие тесты репозитория**

В `service/tests/store/test_repo.py` (файл целиком под `requires_db`; `rec(...)` — локальная фабрика решения, уже есть; сессию сеет уже существующий хелпер `_seed_session(session_factory)`, им и пользоваться вместо ручного `SessionRepo(...).upsert(...)`):

```python
async def test_two_principals_may_share_one_idempotency_key(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)

    first = replace(rec(), idempotency_key="same", key_id="01HZKEYA")
    second = replace(rec(), idempotency_key="same", key_id="01HZKEYB")

    assert await repo.insert(first) is True
    assert await repo.insert(second) is True


async def test_one_principal_may_not_use_one_idempotency_key_twice(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)

    assert await repo.insert(replace(rec(), idempotency_key="same", key_id="01HZKEYA")) is True
    assert await repo.insert(replace(rec(), idempotency_key="same", key_id="01HZKEYA")) is False


async def test_the_static_token_is_one_principal_too(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)

    assert await repo.insert(replace(rec(), idempotency_key="same", key_id=None)) is True
    assert await repo.insert(replace(rec(), idempotency_key="same", key_id=None)) is False


async def test_list_filters_by_key_id(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    await repo.insert(replace(rec(), key_id="01HZKEYA"))
    await repo.insert(replace(rec(), key_id="01HZKEYB"))

    rows = await repo.list(session_id=None, model=None, limit=100, before=None, key_id="01HZKEYA")

    assert [r.key_id for r in rows] == ["01HZKEYA"]
```

`replace` — `dataclasses.replace`, дописать импорт в файле (сегодня его там нет). `session_factory` — существующая фикстура файла, `_seed_session` — существующий хелпер над ней.

Третий тест — тот самый, ради которого нужен генерируемый столбец: с уникальным индексом по `(key_id, idempotency_key)` он бы упал, потому что `NULL <> NULL`.

- [ ] **Step 2: Прогнать, убедиться, что падает**

Run: `cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest tests/store/test_repo.py -q -k "principal or key_id"`
Expected: FAIL — первый тест возвращает `False` на второй вставке (сегодня ключ глобален), `list()` не принимает `key_id`.

- [ ] **Step 3: Реализация в `models.py`**

Импорт: добавить `Computed` в список из `sqlalchemy`.

В `DecisionRow`, последними столбцами — после `spans_rejected`, добавленного v4:

```python
    key_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    # Generated, not written: the replay uniqueness key must have no NULLs
    # (in Postgres NULL <> NULL, so a unique index over a nullable key_id
    # would let every static-token caller reuse one idempotency key), and a
    # generated column cannot drift from key_id the way a second writable
    # column could.
    principal: Mapped[str] = mapped_column(
        String(26), Computed("coalesce(key_id, 'token')", persisted=True)
    )
```

В `__table_args__`: убрать `ux_decisions_idempotency_key`, добавить два индекса:

```python
        Index("ix_decisions_key_id_ts", "key_id", "ts"),
        Index(
            "ux_decisions_principal_idempotency_key", "principal", "idempotency_key", unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
```

Из docstring `ApiKeyRow` убрать абзац `NOTE: the spec also calls for key_id flowing into DecisionRow…` целиком — долг закрыт — и заменить одной строкой:

```python
    ``id`` is what a decision row stores in its ``key_id`` column, so a
    decision can be attributed to the key that asked for it.
```

- [ ] **Step 4: Реализация в `repo.py`**

В `DecisionRepo.insert` — цель вывода конфликта. Было:

```python
        stmt = pg_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=[table.c.idempotency_key],
            index_where=table.c.idempotency_key.isnot(None),
        )
```

Стало:

```python
        stmt = pg_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=[table.c.principal, table.c.idempotency_key],
            index_where=table.c.idempotency_key.isnot(None),
        )
```

`values` строится как `stored.to_record().model_dump(exclude={"decision_id"})`, поэтому генерируемый `principal` в него не попадает сам собой: у `DecisionRecord` такого поля нет. Ничего исключать вручную не нужно, и `key_id` попадает туда тем же механизмом, что и поля v4.

В `DecisionRepo.list` — параметр и условие:

```python
    async def list(
        self, session_id: str | None, model: str | None, limit: int, before: str | None,
        kind: str | None = None, key_id: str | None = None,
    ) -> list[DecisionRecord]:
        ...
        if key_id is not None:
            stmt = stmt.where(DecisionRow.key_id == key_id)
```

Дописать в docstring `list`: «``key_id`` filters to the decisions one issued API key was charged with; rows taken under the static token carry no key and cannot be selected.»

- [ ] **Step 5: Реализация в `mapper.py`**

`principal` — генерируемый столбец, читать его в запись незачем:

```python
_METADATA = "metadata"
# Generated in the database from key_id; DecisionRecord has no such field.
_GENERATED = frozenset({"principal"})


def record_from_row(row: DecisionRow) -> DecisionRecord:
    data = {
        column.name: getattr(row, column.name)
        for column in DecisionRow.__table__.columns
        if column.name != _METADATA and column.name not in _GENERATED
    }
    return DecisionRecord.model_validate(data | {_METADATA: row.metadata_})
```

- [ ] **Step 6: Миграция**

Создать `service/migrations/versions/0007_key_attribution.py`:

```python
"""v3.2: attribute a decision to the API key, and scope replays to the principal

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('decisions', sa.Column('key_id', sa.String(length=26), nullable=True))
    op.add_column(
        'decisions',
        sa.Column(
            'principal', sa.String(length=26),
            sa.Computed("coalesce(key_id, 'token')", persisted=True), nullable=False,
        ),
    )
    op.create_index('ix_decisions_key_id_ts', 'decisions', ['key_id', 'ts'])
    op.drop_index('ux_decisions_idempotency_key', table_name='decisions')
    op.create_index(
        'ux_decisions_principal_idempotency_key', 'decisions', ['principal', 'idempotency_key'],
        unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ux_decisions_principal_idempotency_key', table_name='decisions')
    op.create_index(
        'ux_decisions_idempotency_key', 'decisions', ['idempotency_key'],
        unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )
    op.drop_index('ix_decisions_key_id_ts', table_name='decisions')
    op.drop_column('decisions', 'principal')
    op.drop_column('decisions', 'key_id')
```

Обратить внимание: `downgrade` восстанавливает уникальность по одному столбцу, а строки, вставленные после апгрейда двумя принципалами с одним ключом, ей противоречат. Это ожидаемо и записывается в отчёт: даунгрейд после реального разъезда принципалов потребует ручной чистки, как и любой сужающий индекс.

- [ ] **Step 7: Прогнать миграцию и тесты**

```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run alembic upgrade head
```
Expected: `Running upgrade 0006 -> 0007`. Голова одна: `uv run alembic heads` печатает ровно `0007 (head)`.

Run: `cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest tests/store -q`
Expected: PASS.

- [ ] **Step 8: Падающий тест на фильтр ленты**

В `service/tests/api/test_app.py`:

```python
async def test_the_feed_filters_by_key_id(tmp_path):
    from agentgate.store.keys import hash_key

    plaintext = "agk_" + "d" * 43
    app, _, _, _ = build(tmp_path, token="secret", key_repo=FakeKeyRepo({hash_key(plaintext): "key-9"}))
    await call(app, "POST", "/v1/decide", json=body(), headers={"authorization": f"Bearer {plaintext}"})
    await call(app, "POST", "/v1/decide", json=body(), headers={"authorization": "Bearer secret"})

    page = await call(app, "GET", "/v1/decisions?key_id=key-9", headers={"authorization": "Bearer secret"})

    assert [i["key_id"] for i in page.json()["items"]] == ["key-9"]
```

`FakeDecisionRepo.list` в этом файле — фейк; дописать ему параметр, иначе тест упадёт на сигнатуре:

```python
    async def list(self, session_id, model, limit, before, kind=None, key_id=None):
        rows = [
            r for r in self.rows
            if (session_id is None or r.request.session_id == session_id)
            and (model is None or r.verdict.model == model)
            and (kind is None or r.to_record().kind == kind)
            and (key_id is None or r.to_record().key_id == key_id)
        ]
```

- [ ] **Step 9: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/api/test_app.py -q -k feed_filters_by_key_id`
Expected: FAIL — в ответе обе строки (параметр ещё не объявлен, FastAPI его игнорирует).

- [ ] **Step 10: Реализация маршрута**

В `agentgate/api/app.py`, в маршруте `decisions`, после параметра `kind`:

```python
        key_id: Annotated[
            str | None,
            Query(
                description=(
                    "Return only decisions authenticated with this issued API key. "
                    "Decisions taken under the static token carry no key and are never returned."
                ),
            ),
        ] = None,
    ) -> DecisionListResponse:
        ...
        rows = await decision_repo.list(
            session_id=session_id, model=model, limit=limit, before=before, kind=kind, key_id=key_id
        )
```

- [ ] **Step 11: Перегенерация контрактов и полный прогон**

```bash
cd service && uv run python scripts/export_openapi.py && git diff --stat ../contracts
```
Expected: изменён только `contracts/openapi.yaml` — новый query-параметр `key_id` и новое свойство `key_id` у схемы `DecisionRecord`.

Run: `cd service && <FULL>`
Expected: PASS.

- [ ] **Step 12: Коммит**

```bash
git commit --only service/agentgate/store/models.py service/agentgate/store/repo.py service/agentgate/store/mapper.py service/agentgate/api/app.py service/migrations/versions/0007_key_attribution.py service/tests/store/test_repo.py service/tests/api/test_app.py contracts/openapi.yaml -m "feat(store): key_id on the decision row, and replay uniqueness per principal

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: доверенный домен для `tool: network`

Закрывает §5.3. Требует задачу 2 (`action.method`). Ни один существующий кейс shell-ветки не меняется.

**Files:**
- Modify: `service/agentgate/rules/profile_domain_trusted.py`
- Test: `service/tests/rules/test_profile_domain_trusted.py`

- [ ] **Step 1: Падающая таблица отказов и разрешений**

В `service/tests/rules/test_profile_domain_trusted.py`. В файле уже есть модульные `RULE = ProfileDomainTrustedRule()` и `TRUSTED = trusted_policy()`, импорт `DecisionKind` и строка `from tests.factories import mcp_action, shell_action, stage1_policy, trusted_policy, unparseable_action` — в неё дописывается только `network_action`. Ничего из этого заново не объявлять; дописать тесты:

```python
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_a_read_method_on_a_trusted_domain_is_allowed(method):
    verdict = RULE.evaluate(network_action(method=method), trusted_policy())

    assert verdict is not None and verdict.decision is DecisionKind.allow
    assert verdict.rule_id == "profile.domain-trusted"


@pytest.mark.parametrize(
    "method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    ids=["post", "put", "patch", "delete", "options"],
)
def test_a_writing_method_is_not_allowed(method):
    assert RULE.evaluate(network_action(method=method), trusted_policy()) is None


def test_an_unknown_method_is_not_allowed():
    assert RULE.evaluate(network_action(method=None), trusted_policy()) is None


def test_an_undeclared_domain_is_not_allowed():
    assert RULE.evaluate(network_action(domains=("evil.example",), method="GET"), trusted_policy()) is None


def test_no_domain_at_all_is_not_allowed():
    assert RULE.evaluate(network_action(domains=(), method="GET"), trusted_policy()) is None


def test_open_mode_never_allows():
    assert RULE.evaluate(network_action(method="GET"), trusted_policy(mode="open")) is None


def test_the_flag_off_never_allows():
    assert RULE.evaluate(network_action(method="GET"), stage1_policy()) is None


def test_ask_mode_allows_a_read_of_a_declared_domain():
    verdict = RULE.evaluate(network_action(method="GET"), trusted_policy(mode="ask"))

    assert verdict is not None and verdict.decision is DecisionKind.allow


def test_a_subdomain_of_a_declared_domain_is_allowed():
    verdict = RULE.evaluate(network_action(domains=("files.github.com",), method="GET"), trusted_policy())

    assert verdict is not None and verdict.decision is DecisionKind.allow
```

- [ ] **Step 2: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/rules/test_profile_domain_trusted.py -q -k "method or domain"`
Expected: FAIL — `assert None is not None` у обоих читающих методов (`_shape_is_readable` требует `Tool.shell`).

- [ ] **Step 3: Реализация**

В `agentgate/rules/profile_domain_trusted.py`:

```python
    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        if not self._network_trusts(action, policy):
            return None
        if action.tool is Tool.network:
            return self._network_tool_verdict(action)
        if not self._shape_is_readable(action):
            return None
        if not all(self._command_is_a_read(c, policy) for c in action.commands):
            return None
        if not self._paths_are_safe(action, policy):
            return None
        return Verdict.allow(self.id)

    def _network_tool_verdict(self, action: NormalizedAction) -> Verdict | None:
        """A `tool: network` action carries no argv and no paths, so the
        eleven shell conditions collapse to one: the method must itself be a
        read. An absent method is not a read -- the harness that did not say
        gets the classifier, not a permission.
        """
        if action.method not in _READ_ONLY_METHODS:
            return None
        return Verdict.allow(self.id)
```

И дописать в docstring модуля, после абзаца про условия 5 и 9:

```
A `tool: network` action takes a shorter path: it has no argv to read
flags from and no paths to check, so the conditions that survive are the
network ones -- the flag, a trusting mode, an explicitly declared domain --
plus `method in {GET, HEAD}`. `method: None` never qualifies: "the harness
did not say" is not "the method is safe".
```

- [ ] **Step 4: Прогнать**

Run: `cd service && uv run pytest tests/rules -q`
Expected: PASS — вся прежняя таблица shell-ветки в том числе.

- [ ] **Step 5: Латентность**

Run: `cd service && uv run pytest tests/rules/test_latency.py -q`
Expected: PASS, p50 ≤ 1 мс.

- [ ] **Step 6: Прогнать всё и коммит**

Run: `cd service && <FULL> && git diff --exit-code ../contracts`
Expected: PASS, пустой diff.

```bash
git commit --only service/agentgate/rules/profile_domain_trusted.py service/tests/rules/test_profile_domain_trusted.py -m "feat(rules): a trusted domain allows a GET or HEAD network action, never an unknown method

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Волна 3 — повтор в границах предъявителя

### Task 5: `principal` в ключе повтора

Закрывает §4.1–§4.4. Требует задачи 1 (`key_id` на исходе и в записи) и 3 (столбец, из которого восстанавливается принципал).

**Files:**
- Modify: `service/agentgate/domain/replay.py`, `service/agentgate/session/replay.py`, `service/agentgate/api/app.py`
- Create: `service/tests/domain/test_replay.py`
- Test: `service/tests/session/test_replay.py`, `service/tests/api/test_app.py`, `service/tests/store/test_repo.py`

- [ ] **Step 1: Падающий тест на тип**

Создать `service/tests/domain/test_replay.py`:

```python
from agentgate.domain.replay import STATIC_PRINCIPAL, Replay, ReplayKey, principal_of
from tests.factories import decide_request, decision


def record(key: str = "k", key_id: str | None = None):
    return decision(idempotency_key=key, key_id=key_id).to_record()


def test_a_key_id_is_its_own_principal():
    assert principal_of("01HZKEYA") == "01HZKEYA"


def test_the_static_token_is_one_named_principal():
    assert principal_of(None) == STATIC_PRINCIPAL == "token"


def test_the_storage_key_joins_principal_and_key():
    assert ReplayKey.of("01HZKEYA", "abc").storage_key() == "01HZKEYA:abc"


def test_the_storage_key_of_the_static_token_is_namespaced_too():
    assert ReplayKey.of(None, "abc").storage_key() == "token:abc"


def test_a_replay_takes_its_principal_from_the_record():
    assert Replay.of(record(key_id="01HZKEYA")).principal == "01HZKEYA"


def test_a_replay_answers_its_own_principal():
    stored = Replay.of(record(key_id="01HZKEYA"))

    assert stored.answers(decide_request("ls -la"), "01HZKEYA") is True


def test_a_replay_does_not_answer_another_principal():
    stored = Replay.of(record(key_id="01HZKEYA"))

    assert stored.answers(decide_request("ls -la"), "01HZKEYB") is False
```

`decision()` по умолчанию строит запрос `decide_request("ls -la")`, поэтому дайджест совпадает — тест проверяет ровно принципала.

- [ ] **Step 2: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/domain/test_replay.py -q`
Expected: FAIL — `ImportError: cannot import name 'STATIC_PRINCIPAL'`.

- [ ] **Step 3: Реализация в `domain/replay.py`**

```python
STATIC_PRINCIPAL = "token"


def principal_of(key_id: str | None) -> str:
    """Who a replay belongs to: the issued key's id, or the static token.

    A key id is a ULID -- 26 characters of uppercase Crockford base32 --
    so the literal below can never collide with one.
    """
    return key_id or STATIC_PRINCIPAL


@dataclass(frozen=True)
class ReplayKey:
    """The caller-supplied key, namespaced by whoever supplied it.

    The key is chosen by the caller and was global to the service until
    this type existed: two integrators picking the same string shared one
    entry, and one of them lost the audit row to the other's unique index.
    """

    principal: str
    key: str

    @classmethod
    def of(cls, key_id: str | None, key: str) -> "ReplayKey":
        return cls(principal_of(key_id), key)

    def storage_key(self) -> str:
        return f"{self.principal}:{self.key}"
```

В `Replay`: добавить поле, взять принципала в `of`, сверять его в `answers`:

```python
    request_digest: str
    response: DecideResponse | InspectResponse
    principal: str = STATIC_PRINCIPAL

    @classmethod
    def of(cls, record: DecisionRecord) -> "Replay":
        response = record.to_inspect_response() if record.kind == "inspect" else record.to_response()
        return cls(
            request_digest=record.request_digest, response=response,
            principal=principal_of(record.key_id),
        )

    def answers(self, request: DecideRequest | InspectRequest, principal: str) -> bool:
        return self.principal == principal and self.request_digest == request.identity_digest()
```

Дописать в docstring модуля:

```
The key alone was not enough for a second reason too: it is global to the
service. Two integrators may pick the same string, so the entry is stored
under `ReplayKey` -- the key namespaced by the principal that supplied it
(the issued key's id, or "token" for the static one) -- and `answers`
checks the principal again, so a store that flattened the namespace still
could not hand one caller another's verdict.
```

- [ ] **Step 4: Прогнать**

Run: `cd service && uv run pytest tests/domain/test_replay.py -q`
Expected: PASS.

- [ ] **Step 5: Падающий тест на восстановление**

В `service/tests/session/test_replay.py` дописать. Существующий `record(key, age_seconds)` про `key_id` не знает и таким остаётся (его читают тесты TTL); для новых тестов заводится вторая локальная фабрика — `datetime`, `timezone`, `decision`, `InMemoryReplayStore`, `PersistentReplayStore` и `FakeReplayRecords` в файле уже импортированы:

```python
def keyed_record(key: str = "k", key_id: str | None = None):
    return decision(id="01J000", idempotency_key=key, key_id=key_id,
                    ts=datetime.now(timezone.utc)).to_record()


async def test_restore_puts_an_entry_under_its_principals_key():
    inner = InMemoryReplayStore()
    store = PersistentReplayStore(inner, FakeReplayRecords([keyed_record("k", "01HZKEYA")]), 3600)

    await store.restore()

    assert await inner.get("01HZKEYA:k") is not None
    assert await inner.get("k") is None


async def test_restore_namespaces_a_static_token_entry_too():
    inner = InMemoryReplayStore()
    store = PersistentReplayStore(inner, FakeReplayRecords([keyed_record("k", None)]), 3600)

    await store.restore()

    assert await inner.get("token:k") is not None
```

- [ ] **Step 6: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/session/test_replay.py -q -k principal`
Expected: FAIL — `assert None is not None` (кладётся под голым `"k"`).

- [ ] **Step 7: Реализация в `session/replay.py`**

Импорт: `from agentgate.domain.replay import Replay, ReplayKey, ReplayStore`.

В `restore()` последняя строка цикла. Было:

```python
            await self._inner.put(record.idempotency_key, replay, remaining)
```

Стало:

```python
            await self._inner.put(
                ReplayKey.of(record.key_id, record.idempotency_key).storage_key(), replay, remaining
            )
```

**Три существующих теста файла поедут, и это ожидаемо** — они читают восстановленную запись по голому ключу: `test_restore_loads_keyed_rows_with_their_remaining_ttl` (`store.get("fresh")`, `store.get("stale")`), `test_restore_keeps_the_identity_the_row_was_decided_for` (`store.get("fresh")`) и `test_restore_skips_one_unprojectable_record_without_failing_the_others` (`"before"`, `"after"`, `"bad"`). Правка в каждом одна и та же: читать `f"token:{key}"`, потому что `record(...)` строит решение без `key_id`. Смысл ни одного из них не меняется — меняется только пространство имён ключа. `test_put_then_get_returns_the_same_entry` и `test_persistent_store_delegates_put_and_get` кладут запись сами и остаются на голом `"k"`: составной ключ собирает вызывающий, а не хранилище.

- [ ] **Step 8: Падающий тест на маршрут**

В `service/tests/api/test_app.py`:

```python
async def test_a_repeat_under_another_key_is_decided_afresh(tmp_path):
    from agentgate.store.keys import hash_key

    first_plain, second_plain = "agk_" + "e" * 43, "agk_" + "f" * 43
    keys = FakeKeyRepo({hash_key(first_plain): "key-A", hash_key(second_plain): "key-B"})
    app, _, _, _ = build(tmp_path, token="secret", key_repo=keys)
    headers_a = {"authorization": f"Bearer {first_plain}", "idempotency-key": "shared"}
    headers_b = {"authorization": f"Bearer {second_plain}", "idempotency-key": "shared"}

    first = await call(app, "POST", "/v1/decide", json=body(), headers=headers_a)
    second = await call(app, "POST", "/v1/decide", json=body(), headers=headers_b)

    assert first.json()["decision_id"] != second.json()["decision_id"]


async def test_a_repeat_under_the_same_key_is_still_replayed(tmp_path):
    from agentgate.store.keys import hash_key

    plaintext = "agk_" + "g" * 43
    app, _, _, _ = build(tmp_path, token="secret", key_repo=FakeKeyRepo({hash_key(plaintext): "key-A"}))
    headers = {"authorization": f"Bearer {plaintext}", "idempotency-key": "shared"}

    first = await call(app, "POST", "/v1/decide", json=body(), headers=headers)
    second = await call(app, "POST", "/v1/decide", json=body(), headers=headers)

    assert first.json()["decision_id"] == second.json()["decision_id"]
```

- [ ] **Step 9: Прогнать, убедиться, что падает**

Run: `cd service && uv run pytest tests/api/test_app.py -q -k repeat_under`
Expected: FAIL у первого теста — `decision_id` совпадают (ключ пока глобален); второй тест зелёный уже сейчас и остаётся зелёным после — это регресс-страховка.

- [ ] **Step 10: Реализация в `app.py`**

Импорт: `from agentgate.domain.replay import Replay, ReplayKey, ReplayStore`.

В `_answer`, обе точки работы с ключом:

```python
    key = _replay_key(request)
    storage_key = ReplayKey.of(key_id, key).storage_key() if key is not None else None
    if storage_key is not None:
        replayed = await _replayed(replay, storage_key)
        if replayed is not None and replayed.answers(parsed, principal_of(key_id)):
            return replayed.response
    try:
        outcome = await spec.run(parsed)
    except Exception as exc:  # noqa: BLE001 - fail-closed: no exception may escape as a 500
        log.exception("%s failed", spec.log_label)
        return spec.refuse("api.internal-error", f"internal error: {type(exc).__name__}")
    outcome = replace(outcome, key_id=key_id)
    if storage_key is not None:
        outcome = replace(outcome, idempotency_key=key)
        await _remember(
            replay, storage_key, Replay.of(outcome.to_record()), settings.allow_cache_ttl_seconds
        )
```

`principal_of` импортировать оттуда же. `outcome.idempotency_key` по-прежнему хранит клиентский ключ, а не составной: в строке базы должно лежать то, что прислал клиент, — уникальность обеспечивает генерируемый `principal`, а не форма ключа.

- [ ] **Step 11: Прогнать**

Run: `cd service && uv run pytest tests/api tests/session tests/domain -q`
Expected: PASS.

- [ ] **Step 12: Тест сквозного пути через базу**

В `service/tests/store/test_repo.py`:

```python
async def test_load_replayable_carries_the_key_id_back(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)
    await repo.insert(replace(rec(), idempotency_key="k", key_id="01HZKEYA"))

    records = await repo.load_replayable(datetime.now(timezone.utc) - timedelta(hours=1))

    assert [r.key_id for r in records] == ["01HZKEYA"]
```

Run: `cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest tests/store/test_repo.py -q -k load_replayable`
Expected: PASS (после задачи 3 столбец есть; тест фиксирует, что восстановление получает принципала).

- [ ] **Step 13: Полный прогон и коммит**

Run: `cd service && <FULL> && git diff --exit-code ../contracts`
Expected: PASS, пустой diff.

```bash
git commit --only service/agentgate/domain/replay.py service/agentgate/session/replay.py service/agentgate/api/app.py service/tests/domain/test_replay.py service/tests/session/test_replay.py service/tests/api/test_app.py service/tests/store/test_repo.py -m "feat(replay): an idempotency key belongs to the principal that sent it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Волна 4 — документация и приёмка

### Task 6: контракты и документация

Закрывает §6.

**Files:**
- Modify: `contracts/README.md`, `docs/connect.md`, `CLAUDE.md`, `service/CLAUDE.md`, `service/README.md`

- [ ] **Step 1: Проверить, что контракты уже перегенерированы**

```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
```
Expected: пустой diff — задачи 2 и 3 уже записали изменения. Непустой diff здесь означает, что одна из них перегенерацию забыла: закоммитить его отдельным шагом до продолжения.

- [ ] **Step 2: `contracts/README.md`**

В таблицу полей `DecideRequest` дописать строку:

```
| `args.method` | нет | HTTP-метод сетевого действия, заглавными: `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS`. Только для `tool: network`; у остальных инструментов игнорируется. Отсутствие означает «неизвестен» — неизвестный метод никогда не получает `allow`. |
```

В раздел о ленте — про `key_id`: «Элемент ленты несёт `key_id` — ULID выданного API-ключа, которым был аутентифицирован вызов, либо `null` для статического токена. `GET /v1/decisions?key_id=<ULID>` фильтрует по нему. В ответах `/v1/decide` и `/v1/inspect` поля нет».

- [ ] **Step 3: `docs/connect.md`**

В раздел про `tool: network` дописать: «Присылайте `args.method` — метод HTTP заглавными. Без него сервис считает метод неизвестным, и доверенный домен не даёт `allow`: вызов уходит на ступень 2, как и до v3.2. `GET` и `HEAD` при включённом `network.trusted_allows` и явно разрешённом домене решаются ступенью 1».

Туда же, в раздел про идемпотентность: «`Idempotency-Key` теперь живёт в границах вашего ключа: одинаковая строка у двух интеграторов больше не сталкивается. Требование остаётся прежним — ключ уникален на вызов».

- [ ] **Step 4: Корневой `CLAUDE.md`**

В «Что построено» — абзац про v3.2 после абзаца v3.1:

```
С v3.2 решение атрибутируется предъявителю: зависимость аутентификации отдаёт `key_id`, `api/app.py::_answer` навешивает его на исход, и он доезжает до `DecisionRow.key_id`, до JSONL и до ленты (`GET /v1/decisions?key_id=`). В ответ он не попадает. Повтор по `Idempotency-Key` перестал быть глобальным: ключ хранилища — `(principal, ключ)`, где принципал — `key_id` либо `"token"`, а уникальность в базе держит генерируемый столбец `decisions.principal = coalesce(key_id, 'token')`. `tool: network` научился присылать `method`, и `ProfileDomainTrustedRule` разрешает по нему `GET`/`HEAD` на явно разрешённом домене; `None` не разрешает никогда.
```

В «Известные ограничения» удалить три закрытых пункта — «Атрибуция решения к ключу не подключена», «Ключ `Idempotency-Key` глобален для сервиса», — и переписать пункт про повтор после TTL: уникальный индекс теперь по паре. Добавить два новых:

```
- **`downgrade` миграции `0007_key_attribution` сужает уникальность** обратно к одному столбцу: если после апгрейда два принципала успели записать один и тот же `Idempotency-Key`, откат потребует ручной чистки. Обычная цена сужающего индекса, не дефект миграции.
- **`action_hash()` изменился у всех действий** из-за нового поля `method` в `NormalizedAction`: allow-кэш прогревается заново, как при смене `profile_hash` в v3.1. Миграции данных это не требует.
```

- [ ] **Step 5: `service/CLAUDE.md`**

В карте модулей: `domain/` — дописать `replay.py` (`STATIC_PRINCIPAL`, `principal_of`, `ReplayKey`, `Replay.principal`); `store/` — про `key_id`, генерируемый `principal` и миграцию `0007_key_attribution`; `normalize/` — про `method`. В «Технические правила» — строку: «Повтор по `Idempotency-Key` обслуживается в API-слое до `Gate` и живёт в границах принципала; `key_id` навешивается на исход в `_answer` и не входит ни в один вердикт».

- [ ] **Step 6: `service/README.md`**

В раздел «Как добавить» — ничего (новых швов нет). В раздел про API-ключи дописать: «Решение, принятое под выданным ключом, несёт его `key_id` в строке и в JSONL; фильтр ленты — `GET /v1/decisions?key_id=<ULID>`».

- [ ] **Step 7: Прогон и коммит**

Run: `cd service && <FULL> && git diff --exit-code ../contracts`
Expected: PASS, пустой diff.

```bash
git commit --only contracts/README.md docs/connect.md CLAUDE.md service/CLAUDE.md service/README.md -m "docs: v3.2 in the contract README, connect page, module map and known limitations

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: приёмка и отчёт

Закрывает §7.3. Сервис поднимается локально с профилем, где `network.trusted_allows: true` и `allowed_domains` содержит `github.com`.

**Files:**
- Create: `docs/reports/task-25-v3.2-key-attribution-and-network-method.md`

- [ ] **Step 1: Поднять сервис и выпустить ключ**

```bash
cd service && uv run python -m agentgate keys create --label acceptance
```
Записать выданный ключ (показывается один раз) и его `key_id`.

```bash
cd service && uv run python -m agentgate  # или обычный способ запуска, порт 8400
```

- [ ] **Step 2: Критерий 1 — атрибуция**

```bash
curl -s localhost:8400/v1/decide -H "Authorization: Bearer $AGK" -H 'Content-Type: application/json' \
  -d '{"harness":"t","tool":"shell","raw":"git status","args":{"cwd":"/home/u/repo"},"user_request":"check"}' | jq -c '{decision,decision_id}'
curl -s localhost:8400/v1/decide -H "Authorization: Bearer $AGENTGATE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"harness":"t","tool":"shell","raw":"git status","args":{"cwd":"/home/u/repo"},"user_request":"check"}' | jq -c '{decision,decision_id}'
curl -s "localhost:8400/v1/decisions?limit=2" -H "Authorization: Bearer $AGENTGATE_TOKEN" | jq -c '[.items[] | {decision,key_id}]'
```
Expected: вердикты одинаковые; `key_id` у первой строки — ULID ключа, у второй `null`. В ответах `/v1/decide` поля `key_id` нет.

- [ ] **Step 3: Критерий 2 — фильтр**

```bash
curl -s "localhost:8400/v1/decisions?key_id=$KEY_ID" -H "Authorization: Bearer $AGENTGATE_TOKEN" | jq '[.items[].key_id] | unique'
```
Expected: `["<KEY_ID>"]`.

- [ ] **Step 4: Критерий 3 — повтор в границах предъявителя**

```bash
BODY='{"harness":"t","tool":"shell","raw":"git status","args":{"cwd":"/home/u/repo"},"user_request":"check"}'
curl -s localhost:8400/v1/decide -H "Authorization: Bearer $AGK" -H 'Idempotency-Key: shared' -H 'Content-Type: application/json' -d "$BODY" | jq -r .decision_id
curl -s localhost:8400/v1/decide -H "Authorization: Bearer $AGK" -H 'Idempotency-Key: shared' -H 'Content-Type: application/json' -d "$BODY" | jq -r .decision_id
curl -s localhost:8400/v1/decide -H "Authorization: Bearer $AGENTGATE_TOKEN" -H 'Idempotency-Key: shared' -H 'Content-Type: application/json' -d "$BODY" | jq -r .decision_id
```
Expected: первые два `decision_id` совпадают, третий отличается. Проверить, что в базе три вызова оставили две строки:

```bash
psql "postgresql://agentgate:agentgate@localhost:5433/agentgate" -c \
  "select principal, idempotency_key, count(*) from decisions where idempotency_key='shared' group by 1,2;"
```
Expected: две строки, у каждой `count = 1`.

- [ ] **Step 5: Критерий 4 — метод**

```bash
for M in GET DELETE; do
  curl -s localhost:8400/v1/decide -H "Authorization: Bearer $AGK" -H 'Content-Type: application/json' \
    -d "{\"harness\":\"t\",\"tool\":\"network\",\"args\":{\"cwd\":\"/home/u/repo\",\"domains\":[\"github.com\"],\"method\":\"$M\"},\"user_request\":\"fetch\"}" | jq -c '{decision,stage,rule_id}'
done
curl -s localhost:8400/v1/decide -H "Authorization: Bearer $AGK" -H 'Content-Type: application/json' \
  -d '{"harness":"t","tool":"network","args":{"cwd":"/home/u/repo","domains":["github.com"]},"user_request":"fetch"}' | jq -c '{decision,stage,rule_id}'
```
Expected: `GET` — `{"decision":"allow","stage":1,"rule_id":"profile.domain-trusted"}`; `DELETE` — `stage: 2`; без метода — `stage: 2`.

Плюс проверка невалидного метода:

```bash
curl -s localhost:8400/v1/decide -H "Authorization: Bearer $AGK" -H 'Content-Type: application/json' \
  -d '{"harness":"t","tool":"network","args":{"cwd":"/home/u/repo","domains":["github.com"],"method":"TRACE"},"user_request":"fetch"}' -o /dev/stdout -w ' HTTP %{http_code}\n' | jq -c '{decision,stage,rule_id}' 2>/dev/null || true
```
Expected: HTTP 200, `decision: "ask"`, `stage: 0`, `rule_id: "api.invalid-request"`.

- [ ] **Step 6: Критерий 5 — контрольная группа**

```bash
cd benchmark && export SECURITY_SERVICE_URL=http://127.0.0.1:8400
uv run python cli.py benchmark --path attacks/cases --out results/v32-off   # профиль без trusted_allows, запросы без method
uv run python cli.py compare <RUN_BEFORE_V32> <RUN_V32_OFF>
```
Expected: расхождений по вердиктам ноль. Фактическое число записать в отчёт.

- [ ] **Step 7: Критерий 6 — миграция на данных**

```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run alembic upgrade head
psql "postgresql://agentgate:agentgate@localhost:5433/agentgate_test" -c \
  "select principal, count(*) from decisions group by 1;"
```
Expected: строки, созданные до миграции, имеют `principal = 'token'`; уникальный индекс не нарушен.

- [ ] **Step 8: Отчёт**

Создать `docs/reports/task-25-v3.2-key-attribution-and-network-method.md` на русском, по образцу `docs/reports/task-23-v3.1-strictness-mcp-domains.md`. Обязательные разделы:

1. **Что построено** — по задачам 1–6, с именами файлов.
2. **Доказательства TDD** — по задаче: какой тест падал первым и с какой ошибкой, что сделало его зелёным.
3. **Числа приёмки** — шесть критериев §7.3 с фактическими значениями: два `key_id` из критерия 1, размер выборки фильтра, три `decision_id` и результат `group by` из критерия 3, три вердикта критерия 4, число расхождений бенчмарка, вывод `group by principal` после миграции.
4. **Решения и отступления** — генерируемый столбец вместо `coalesce` в `ON CONFLICT` и почему; атрибуция через `replace` после движка, а не параметром `Gate.decide`; поле на `ActionArgs`, потому что `NetworkArgs` в дереве нет; нумерация миграции (`0007` поверх занятого v4 `0006`) и почему двух голов нет.
5. **Что изменилось для интегратора** — `args.method`, `key_id` в ленте, границы `Idempotency-Key`, прогрев allow-кэша из-за нового `action_hash`.
6. **Отношение к v4** — v3.2 построен поверх слитого v4: одна голова Alembic (`0005 → 0006 → 0007`), поля `key_id`/`principal` встали после `spans`/`redacted`/`spans_rejected`, ни одно решение спеки v4 не отменил.
7. **Отложено** — всё из §7.5 спеки.
8. **Находки ревью и как закрыты** — заполняется по факту.

- [ ] **Step 9: Финальная проверка и коммит**

```bash
cd service && <FULL> && git diff --exit-code ../contracts
```
Expected: полный прогон зелёный дважды подряд, контракты без diff.

```bash
git commit --only docs/reports/task-25-v3.2-key-attribution-and-network-method.md -m "docs(report): v3.2 — key attribution, replays per principal, method on network actions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Порядок и волны

| Волна | Задачи | Почему вместе / почему после |
|---|---|---|
| 1 | 1 ‖ 2 | атрибуция и `method` не пересекаются ни одним файлом: 1 — `deps/app/decision/inspection/protocols`, 2 — `schemas/normalize/prompt`. Единственное общее место — `tests/factories.py`, и то по разным функциям; если задачи идут параллельными агентами, коммитить их фабричные правки по отдельным строкам файла |
| 2 | 3 ‖ 4 | 3 требует 1 (`DecisionRecord.key_id`), 4 требует 2 (`action.method`); между собой не пересекаются: `store/*` + `app.py` против `rules/profile_domain_trusted.py`. Внимание: `api/app.py` трогают 1, 3 и 5 — 3 и 5 в разных волнах именно поэтому |
| 3 | 5 | требует и 1 (`key_id` на исходе), и 3 (столбец, из которого восстанавливается принципал). Один исполнитель, потому что критический путь идёт через `app.py::_answer`, который уже правили в 1 и 3 |
| 4 | 6 → 7 | документация по слитому коду, затем прогон бенчмарка, приёмка и отчёт |

Ревью — соответствие спеке и качество — по слитому коду каждой волны; финальное ревью всей ветки перед слиянием. Параллельных веток, которых нужно уведомлять, нет: v4 уже в `main`, v3.2 идёт следующей.

## Что считать готовым

- Полный прогон зелёный дважды, собрано ≥ 1446 тестов (база `cbe3073`), `git diff --exit-code ../contracts` пустой.
- Вызов под выданным ключом пишет `key_id` в строку и в JSONL и не возвращает его в ответе; вызов под статическим токеном пишет `null`.
- `GET /v1/decisions?key_id=<ULID>` возвращает только строки этого ключа.
- Один `Idempotency-Key` и одно тело под двумя ключами — два `decision_id` и две строки; под одним ключом — один `decision_id` и одна строка; `select ... group by principal, idempotency_key` подтверждает это в базе.
- `tool: network` с `method: GET` при `trusted_allows: true` и явно разрешённом домене — `allow`, `stage: 1`, `rule_id: profile.domain-trusted`; с `DELETE`, без метода, при `mode: open` и на неразрешённом домене — ступень 2; с `TRACE` — `ask`, `stage: 0`, `rule_id: api.invalid-request`.
- Промпт запроса без метода не изменился ни на байт (`test_empty_dialogue_renders_the_v1_message_byte_for_byte` зелёный).
- `alembic upgrade head` проходит на базе с данными; исторические строки получают `principal = 'token'`.
- Latency-тест ступени 1 p50 ≤ 1 мс зелёный.
- Отчёт `docs/reports/task-25-v3.2-key-attribution-and-network-method.md` написан с фактическими числами; PR упоминает service, adapters, benchmark.

---

## Self-review

**1. Покрытие спеки.**

| Раздел спеки | Задача |
|---|---|
| §1.1 дыра 1 (решение не привязано к клиенту) | 1, 3 |
| §1.1 дыра 2 (`Idempotency-Key` глобален) | 3 (уникальность), 5 (ключ и `answers`) |
| §1.1 дыра 3 (нет метода у `tool: network`) | 2, 4 |
| §2 решения 1–3 (`replace` после движка, поле не на `Verdict`, зависимость-значение) | 1 (Steps 3, 13) |
| §2 решения 4, 6, 7 (`principal_of`, `ReplayKey`, `Replay.principal`) | 5 |
| §2 решение 5 (генерируемый столбец) | 3 (Steps 3, 6), тест «статический токен — тоже принципал» |
| §2 решения 8, 9 (поле на `ActionArgs`, `None` не квалифицируется) | 2 (Step 3), 4 (Step 3) |
| §2 решение 10 (имя ревизии) | 3, Step 6 |
| §3.1 порядок навешивания до `Replay.of` | 1, Step 13 (комментарий в коде) и 5, Step 10 (окончательный порядок) |
| §3.2 таблица трёх исходов зависимости | 1, Step 1 (три теста) + существующий 401-тест файла |
| §3.3 поле записи, индекс, отсутствие FK, JSONL | 1 (поле, JSONL), 3 (столбец, индекс) |
| §3.4 фильтр ленты | 3, Steps 8–10 |
| §4.1–§4.3 принципал, ключ, `answers` | 5, Steps 1–4 |
| §4.4 восстановление | 5, Steps 5–7 |
| §4.5 уникальность и `mapper` | 3, Steps 3–6 |
| §4.6 что остаётся как было (TTL, гонка, 128 символов, отказ хранилища) | ничего не меняется; регресс держат существующие тесты `tests/session/test_replay.py` и `tests/api/test_app.py`, плюс тест «повтор под тем же ключом всё ещё повторяется» (5, Step 8) |
| §5.1 контракт поля и закрытое множество | 2, Steps 1–3 |
| §5.2 нормализация и эффект на `action_hash` | 2, Steps 4, 11 |
| §5.3 пять условий ветки `network` | 4, Step 1 — по тесту на условие |
| §5.4 строка `method=` и её отсутствие | 2, Steps 6–9 |
| §6 контракт целиком | 2 (запрос), 3 (лента), 6 (README, connect) |
| §7.1 инварианты 1–11 | 1: 1, Step 11 (одинаковые вердикты, разный `key_id`) + Global Constraints; 2: обзор кода в ревью, ключ нигде не логируется — `deps.py` пишет только `key_id`; 3: 1, Step 11 (`"key_id" not in response.json()`); 4: 5, Step 8; 5: 3, Step 1 (три теста); 6: существующие тесты fail-closed не трогаются, 401-путь — 1, Step 4; 7: 4, Step 1; 8: 2, Step 1 и приёмка 7, Step 5; 9: Global Constraints + критерий 5; 10: 2, Step 9; 11: 4, Step 5 |
| §7.2 перечень тестов | все десять файлов перечня заведены: `test_deps.py` (1), `test_app.py` (1, 3, 5), `tests/domain/test_replay.py` (5, создаётся), `tests/session/test_replay.py` (5), `tests/store/test_repo.py` (3, 5), `tests/normalize/test_init.py` (2), `tests/rules/test_profile_domain_trusted.py` (4), `tests/classify/test_prompt.py` (2), `tests/test_contracts.py` (2, 3), `tests/rules/test_latency.py` (4). Одно расхождение: тесты валидации метода спека кладёт в `tests/normalize/`, план — в `tests/test_schemas.py`, потому что валидация живёт в pydantic-модели, а не в нормализаторе; нормализация проверяется отдельно там, где ей и место |
| §7.3 шесть критериев приёмки | 7 |
| §7.4 миграция | 3, Step 6 (`0007` поверх `0006`) + «Сверка с v4» (голова одна, merge-ревизии нет) |
| §7.5 не входит | ничего из списка не запланировано |

**2. Плейсхолдеры.** «TBD», «similar to Task N», «add error handling», шагов без кода в плане нет. Два места, где содержание заполняется по факту, названы прямо: отчёт (задача 7, разделы перечислены поимённо) и число расхождений бенчмарка. Один артефакт формы найден при самопроверке и убран: в задаче 4 первой редакцией стоял один параметризованный тест «все методы кроме GET/HEAD», из которого не было видно, что `None` — отдельный случай; теперь `None`, писательные методы и отсутствие домена — три разных именованных теста. В задаче 1, Step 1 остаётся условная инструкция про `valid_record` — она разрешена явным кодом фабрики на случай, если в файле её нет, а не отсылкой «сделай как рядом».

**3. Сверка с деревом после v4.** Все «before»-фрагменты и места вставки перечитаны по `cbe3073`: `DecisionRecord` (`key_id` последним, после `spans_rejected`), `Decision.to_record` (после `cost`), `Inspection` (`key_id` после `idempotency_key`, в `to_record` — после `spans_rejected`, в докстринге `as_cached` — в перечне отброшенных рядом с `idempotency_key`), `Stored` и `Outcome` (протоколы приведены целиком), `deps.py` (`require_token` целиком), `app.py` (`_answer` и однострочный `auth = Depends(...)`), `store/models.py` (`key_id`/`principal` после `spans_rejected`), `store/repo.py` (`insert` с прежним `index_elements`, `list(..., kind=None)`), `store/mapper.py`, `normalize/model.py` и `normalize/__init__.py`, `classify/prompt.py`, `rules/profile_domain_trusted.py` (`_READ_ONLY_METHODS` уже есть), `migrations/versions/0006_v4_spans_and_redaction.py` (`revision = '0006'` → наш `0007`). Тестовые фабрики и хелперы приведены к реальным именам: `_settings`/`_valid_record`/`FakeKeyRepo(by_hash=…)` в `tests/api/test_deps.py`, `_seed_session` и отсутствующий импорт `dataclasses.replace` в `tests/store/test_repo.py`, модульные `RULE`/`TRUSTED` в `tests/rules/test_profile_domain_trusted.py`, локальный `decision()` в `tests/engine/test_decision.py`. Единственное место, где v4 добавил работу: три restore-теста в `tests/session/test_replay.py` переходят на ключ `token:<k>` — это следствие v3.2, а не v4, и оно выписано в задаче 5, Step 7. Ни одно решение спеки v4 не отменил.

**4. Согласованность типов.** `key_id: str | None` — одно имя и один тип во всех семи местах: `require_token` (1), `Decision`/`Inspection` (1), `Stored` (1), `DecisionRecord` (1), `Outcome` (1), `DecisionRow.key_id` (3), параметр `DecisionRepo.list` и query-параметр (3). `STATIC_PRINCIPAL`, `principal_of(key_id)`, `ReplayKey.of(key_id, key)`, `ReplayKey.storage_key()`, `Replay.principal`, `Replay.answers(request, principal)` — определены в задаче 5 и используются под теми же именами в `app.py` и `session/replay.py` той же задачи. `HTTP_METHODS` (2) и `_READ_ONLY_METHODS` (существует в `profile_domain_trusted.py` с v3.1, не переопределяется) — разные множества с разными ролями и разными именами; ветка задачи 4 читает второе. `NormalizedAction.method` (2) читают `prompt.py` (2) и правило (4). Фабрика `network_action(domains, method, cwd)` заведена в задаче 2 и вызывается в задачах 2 и 4 с теми же именами параметров. `DecisionRepo.list(..., kind=None, key_id=None)` — порядок и имена совпадают у настоящего репозитория (3, Step 4), у фейка в `test_app.py` (3, Step 8) и у вызова из маршрута (3, Step 10).
