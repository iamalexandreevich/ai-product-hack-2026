# Задача 6 — отчёт: `Classifier`, `PersistentSessionStateStore`, `bootstrap.py`, типизированные границы

Коммит: `04b4a50 refactor(service): four protocols and one composition root`
Ветка: `refactor/solid-v1.5`

## Итог

Полный прогон: **871 passed, exit code 0**.

```
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
EXITCODE=0
871 passed in 9.03s
```

(Базовый прогон до задачи — 846 passed, exit 0. Прирост +25 — разделённые тесты и новые тесты
store/bootstrap/FK-порядка.)

Контракт: `git diff --exit-code ../contracts` → **0** после перегенерации обоих скриптов.
Латентность: `tests/rules/test_latency.py` зелёный. Корпус: `tests/equivalence/` — 227 passed,
`baseline.json` не тронут (`git diff --stat tests/equivalence/` пуст).
`grep -rn "monkeypatch.setattr" tests/` — пусто.

---

## 1. Что построено

### Четыре протокола, один composition root

- `agentgate/classify/base.py` — `Classifier` (Protocol): `name: str`,
  `async classify(action, user_request, policy, stage1_note) -> Verdict`. Никогда не бросает.
- `agentgate/classify/llm.py` — `LLMClassifier` (бывший `run_stage2`, ставший методом) и
  `build_classifiers(profile, http) -> dict[str, Classifier]`.
- `agentgate/session/persistent.py` — `PersistentSessionStateStore` (реализация
  `SessionStateStore` + `restore()`), плюс узкий Protocol `SessionRecords` — то, что этот store
  требует от репозитория (гайд 2.2: интерфейс объявляет клиент, а не поставщик).
- `agentgate/store/mapper.py` — `row_from_view` / `view_from_row`, единственное место, знающее
  про `metadata_`.
- `agentgate/bootstrap.py` — `build_service(settings, *, http, state_store, writer) -> Service` и
  `build_key_repo(settings)`.
- `agentgate/domain/session.py` — к `SessionStateStore` добавлен `RestorableSessionStateStore`
  (`SessionStateStore` + `restore`). Движок не должен уметь ничего восстанавливать; composition
  root — должен.

`Gate` больше не строит `LLMClient` и не держит `httpx`. Проверка «unknown model» — отсутствие
ключа в реестре, а не `try/except KeyError`.

`__main__.py` сократился со 107 строк до 39: разбор argv и `uvicorn.run`.
`cli.py` потерял `_build_repo` и берёт `build_key_repo` из `bootstrap`.
`create_app` — шесть типизированных параметров, мёртвые ветки `decision_repo is None` /
`session_repo is None` удалены. `/v1/decisions` отвечает `DecisionsPage`, `/healthz` — `Health`.
`dict(r.to_dict(), decision_id=r.id)` исчез из обоих мест.

### Перенос модулей

`agentgate/stage2/{client,prompt,schema}.py` → `agentgate/classify/`, `stage2/run.py` растворился
в `classify/llm.py`, `agentgate/session/state.py` → `agentgate/domain/session.py`.

**Важно про историю:** сами переименования (`git mv`) попали не в мой коммит, а в чужой —
`1d926a4 docs(service): argv docstring matches the default it describes` (автор: Александр,
13:40). Я сделал `git mv` в начале работы, что положило переименования в индекс; пользователь
параллельно закоммитил свою правку `shell/argv.py` и `ledger.md`, и коммит забрал весь индекс,
включая мои `R` и `git rm agentgate/stage2/run.py`. Историю чужого коммита я не переписывал.
Следствие: в диффе `04b4a50` переименований нет — только содержательные правки перенесённых
файлов. Рабочее дерево корректно, дублей нет, `agentgate/stage2/` отсутствует.

---

## 2. Доказательства TDD

### 2.1 RED: write-through `cache_put` из брифа нарушает FK (главная находка)

Бриф (шаг 4) требовал, чтобы `PersistentSessionStateStore.cache_put` писал строку allow-кэша в
Postgres. Реализовал ровно так, как в брифе, и написал пробу против живой БД, воспроизводящую то,
что делает `Gate._settle_session`:

```python
async def test_cache_put_write_through_at_decide_time(session_factory):
    store = PersistentSessionStateStore(InMemorySessionStateStore(), SessionRepo(session_factory))
    await store.save(await store.get_or_create("s1", "t", "default", "/w"))
    # Exactly what Gate._settle_session does: the decision row for this id is
    # only written later, by the writer, after the response has been sent.
    await store.cache_put("s1", "k" * 64, "01JDECISIONNOTWRITTENYET00", 86400)
```

```
cd service && AGENTGATE_TEST_DB_URL=... uv run pytest tests/session/test_fk_probe.py -q

E  sqlalchemy.exc.IntegrityError: <class 'asyncpg.exceptions.ForeignKeyViolationError'>:
   insert or update on table "allow_cache" violates foreign key constraint
   "allow_cache_decision_id_fkey"
E  DETAIL:  Key (decision_id)=(01JDECISIONNOTWRITTENYET00) is not present in table "decisions".
1 failed
```

Почему это ожидаемо: FK у allow-кэша направлен **в обратную сторону** относительно того,
что разбирает бриф. Бриф обосновывает порядок только для `decisions.session_id → sessions.id`
(«сессия до решения»), но `allow_cache.decision_id → decisions.id` требует, чтобы строка кэша шла
**после** строки решения. Store вызывается внутри `Gate.decide`, строка решения пишется writer'ом
уже после ответа. Значит write-through кэша невозможен по построению.

Цена, если бы это уехало в прод: `store.cache_put` бросает → исключение выходит из `Gate.decide` →
внешний fail-closed обработчик в `api/app.py` превращает **каждый `allow` в сессии** в
`ask` / `api.internal-error`. Это не рефакторинг, а отказ сервиса.

**Решение (отказ от части брифа):** `save` пишет через себя, `cache_put` — только в память;
персистентность allow-кэша остаётся в `PostgresDecisionWriter`, после `decisions.insert`.
Это к тому же честное разделение: строка allow-кэша ключуется идентификатором решения, значит
она принадлежит записи решения, а не состоянию сессии.

Проба удалена; её смысл зафиксирован двумя постоянными тестами:
`tests/session/test_persistent.py::test_cache_put_is_readable_without_touching_the_repository`
(с комментарием про FK) и
`tests/store/test_writer.py::test_postgres_writer_writes_the_decision_before_the_cache_row`.

### 2.2 RED → GREEN: тесты `PersistentSessionStateStore`

Тесты из брифа написаны до реализации (`tests/session/test_persistent.py`); первый прогон падал
`ModuleNotFoundError: agentgate.session.persistent`, затем — на отсутствии `FakeSessionRecords` в
`tests/factories.py`. После реализации store и фейка — зелёные.

### 2.3 RED: `timedelta` не импортирован

Первый прогон пробы упал раньше — `NameError: name 'timedelta' is not defined` в `persistent.py`.
Тривиально, но показателен: тест поймал ошибку до того, как её увидел бы прод.

### 2.4 GREEN на каждом шаге

После каждого шага прогонялся затронутый набор; финальные прогоны — 870 → 871 passed, exit 0.

---

## 3. Как доказан FK-порядок против реальной БД

`tests/api/test_app.py::test_a_sessioned_decision_and_its_cache_row_reach_postgres` (`requires_db`).

Собирает приложение над **настоящими** `SessionRepo` / `DecisionRepo` над `session_factory`,
делает один `POST /v1/decide` в новой сессии `fresh-session` и проверяет:

1. строка решения появилась (`DecisionRepo.list(session_id="fresh-session")` вернул ровно
   `decision_id` из ответа) — значит `sessions.id` уже существовал к моменту вставки, то есть
   `PersistentSessionStateStore.save` отработал внутри `decide`, до writer'а;
2. строка allow-кэша появилась и ссылается на тот же `decision_id`
   (`SessionRepo.cache_load_valid()`) — значит writer записал кэш после решения.

Тест не полагается на исключения: `CompositeDecisionWriter` их глотает, поэтому неверный порядок
проявляется как **отсутствие строки**, что тест и ловит. Обе стороны FK покрыты одним прогоном
через HTTP.

Дополнительно `tests/e2e/test_e2e.py` поднимает настоящий процесс `python -m agentgate` через
`build_service` и проходит — это подтверждает, что composition root загружается и работает.

---

## 4. Контракт: что показал diff

`git diff --exit-code ../contracts` после `export_contracts.py` + `export_openapi.py` → **пусто**.

`decide_request.schema.json` / `decide_response.schema.json` не изменились. `openapi.yaml` тоже
**не изменился** — и это ожидаемо: `scripts/export_openapi.py` генерирует компоненты только из
`DecideRequest`, `DecideResponse` и `Profile`; `DecisionListResponse`, `DecisionRecord`, `Health`
и `Error` там написаны руками. Новые модели экспортер не читает, поэтому «diff как проверка» из
брифа автоматически не срабатывает — я сверил модели с закоммиченным yaml вручную:

```
DecisionRecord (yaml) − DecisionView: []
DecisionView − DecisionRecord (yaml): ['model_raw_response']   # yaml: additionalProperties: True
DecisionListResponse (yaml): ['items','next_before']  ==  DecisionsPage: ['items','next_before']
Health (yaml): status enum[ok,degraded]; db boolean; llm string|null
```

**Одно расхождение нашлось, и я правил модель, а не yaml:** бриф предлагал
`Health.llm: bool | None`, тогда как контракт объявляет `llm` как `string | null`. Модель приведена
к контракту: `llm: str | None = None`. `status` типизирован как `Literal["ok", "degraded"]`, что
точнее брифовского `str` и совпадает с `enum` в yaml.

`model_raw_response` присутствует в `DecisionView`, но не описан в `DecisionRecord` — это уже так
и было до задачи (роут отдавал `to_dict()`, включавший это поле), и yaml разрешает это через
`additionalProperties: True`. Поведение не изменено.

---

## 5. Три `monkeypatch.setattr` → инъекции

### 5.1 `tests/test_main.py:77` — подмена класса store в модуле

Было:
```python
monkeypatch.setattr(main_mod, "InMemorySessionStateStore", CapturingStore)
app, returned_settings = await main_mod.build_app(settings)
assert len(captured["preloaded"]) == 1
assert len(captured["cache_puts"]) == 1
```
— один тест с шестью утверждениями о внутренностях (`preload` вызван, `cache_put` вызван, TTL в
диапазоне, healthz жив, settings те же).

Стало (`tests/test_bootstrap.py`) — параметр вместо подмены и утверждения о **наблюдаемом
состоянии** восстановленного store, по одному факту на тест:
```python
async def test_build_service_restores_the_persisted_session(session_factory, tmp_path):
    seeded, _ = await _seed(session_factory)
    service = await build_service(settings_for(tmp_path))
    restored = await service.state_store.get_or_create("s1", "t", "default", "/w")
    assert restored.deny_total == seeded.deny_total

async def test_build_service_restores_the_allow_cache(session_factory, tmp_path):
    _, decision_id = await _seed(session_factory)
    service = await build_service(settings_for(tmp_path))
    assert await service.state_store.cache_get("s1", "hash1") == decision_id

async def test_build_service_restores_the_store_it_was_given(session_factory, tmp_path):
    store = RecordingStore()
    service = await build_service(settings_for(tmp_path), state_store=store)
    assert service.state_store is store and store.restores == 1
```
Плюс `test_built_gate_decides_against_the_wired_profile` — что собранный `Gate` реально решает.

### 5.2 `tests/test_cli_keys.py:176-179` — подмена `build_app`, `uvicorn.run`, `sys.argv`

Было: три `setattr` и проверка «фейки не вызывались».

Стало — два поведенческих теста, ноль подмен атрибутов:
```python
def test_issuing_a_key_needs_no_profiles_and_no_token_for_the_bind(...):
    # Настройки, которые build_service отверг бы дважды: non-localhost bind без токена
    # и несуществующий profiles_dir. Ключ, который всё-таки выпустился, и есть доказательство,
    # что CLI не поднимал сервис.
    settings = Settings(db_url=TEST_DB_URL, bind="0.0.0.0:8400", profiles_dir=tmp_path / "no-such-dir", ...)
    assert run_keys_cli(["create", "--label", "smoke"], settings=settings) == 0

def test_keys_argument_dispatches_to_the_cli_instead_of_the_server(...):
    main_mod.main(["keys", "create", "--label", "smoke"])   # argv как параметр
```
Для этого `main()` получил параметр `argv: list[str] | None = None` (по умолчанию `sys.argv[1:]`) —
подмена `sys.argv` больше не нужна, а покрытие ветки `keys` в `main()` сохранено, чего вариант
брифа («вызывать `run_keys_cli` напрямую») лишился бы. `monkeypatch.setenv` остался: окружение —
граница системы.

### 5.3 `tests/test_session.py:61-74` — подмена `time.monotonic`

Часы стали зависимостью: `InMemorySessionStateStore(now: Callable[[], float] = time.monotonic)`.
Тесты передают `FakeClock` из `tests/factories.py`. Слитый тест
`test_memory_store_roundtrip_and_cache_ttl` разделён (гайд 6.5) на четыре факта:
`test_state_survives_a_save_and_reload`, `test_cache_returns_what_was_put_under_that_key`,
`test_cache_misses_an_unknown_key`, `test_cache_entry_expires_after_its_ttl`; граничный тест стал
`test_cache_entry_is_expired_exactly_at_its_ttl`.

---

## 6. Отступления от брифа (и почему)

1. **`cache_put` не пишет через себя** — раздел 2.1. Доказано IntegrityError против живой БД.
   Тесты `test_postgres_writer_caches_an_allow` и соседние **остались** в
   `tests/store/test_writer.py`, а не переехали в `test_persistent.py`, как просил бриф.
2. **`Health.llm: str | None`, а не `bool | None`** — контракт (`openapi.yaml`) объявляет
   `string | null`; правил модель, не yaml.
3. **`DecisionsPage` и `Health` лежат в новом `agentgate/api/responses.py`, а не в
   `api/schemas.py`.** В `schemas.py` они дают цикл импортов: `engine/decision.py` импортирует
   `api.schemas` (`DecideRequest`, `DecisionKind`), а `DecisionsPage` должен импортировать
   `DecisionView` из `engine.decision`. `schemas.py` остаётся листом, от которого зависят все.
   Причина записана в докстринге модуля.
4. **`Gate.__init__` сохранил `default_profile`.** Бриф перечислил
   `(profiles, classifiers, rules, state_store, allow_cache_ttl_seconds)`, но
   `decide` делает `request.profile_id or self._default_profile` — без него не собирается.
   Считаю это опечаткой брифа.
5. **`PersistentSessionStateStore.save` глотает и логирует ошибку репозитория.** Если бы она
   поднималась, `Gate.decide` падал бы, и падение Postgres превращало бы каждое решение в `ask` —
   изменение поведения и обвал доступности, тогда как сегодня запись идёт после ответа и её сбой
   проглатывается. Глобальное ограничение «рефакторинг, а не изменение поведения» перевешивает.
   Зафиксировано двумя тестами: `test_session_write_failure_does_not_change_the_response` и
   `test_session_write_failure_is_logged`.
6. **`Service` получил поле `state_store`** (бриф: `app, gate, engine, settings`; интерфейсный
   раздел упоминал ещё `writer`). Без `state_store` восстановление нельзя проверить поведенчески —
   пришлось бы возвращаться к подмене класса, ради устранения которой задача и затевалась.
   `writer` не добавлял: его никто не читает.
7. **`hasattr(store, "restore")` не используется.** Вместо проверки атрибута — протокол
   `RestorableSessionStateStore`; `build_service` вызывает `restore()` всегда, в том числе на
   подставленном store.

---

## 7. Изменённые файлы

Новые: `agentgate/classify/base.py`, `agentgate/classify/llm.py`, `agentgate/session/persistent.py`,
`agentgate/store/mapper.py`, `agentgate/api/responses.py`, `agentgate/bootstrap.py`.

Изменённые: `agentgate/__main__.py`, `agentgate/api/app.py`, `agentgate/api/deps.py`,
`agentgate/cli.py`, `agentgate/domain/session.py`, `agentgate/engine/decision.py`,
`agentgate/engine/gate.py`, `agentgate/session/escalation.py`, `agentgate/session/memory.py`,
`agentgate/store/repo.py`, `agentgate/store/writer.py`, `agentgate/classify/{client,prompt}.py`
(правка импортов и ссылок в докстрингах).

Тесты: `tests/factories.py` переписан (добавлены `FakeClassifier`, `FakeClock`,
`FakeSessionRecords`, `decision`, `session_state`, `classifiers`, `gate`; удалён `FakeLLM` —
транспортный фейк остался только там, где предмет проверки и есть HTTP-клиент).
Переносы: `test_api.py`→`api/test_app.py`, `test_deps_keys.py`→`api/test_deps.py`,
`test_stage2_*`→`classify/*`, `test_store.py`→`store/test_repo.py`,
`test_keys.py`→`store/test_keys.py`, `test_log.py`→`log/test_jsonl.py`,
`test_main.py`→`test_bootstrap.py`, `test_session.py`→`domain/test_session.py` +
`session/test_memory.py` + `session/test_cache_key.py`.

---

## 8. Самопроверка

- Composition root ровно один — `bootstrap.build_service`. `cli.py` берёт `build_key_repo`, а не
  строит движок сам, и не поднимает сервис ради ключа (доказано тестом из 5.2).
- Строка сессии пишется один раз за решение (`PersistentSessionStateStore.save`) и до строки
  решения (`test_a_sessioned_decision_and_its_cache_row_reach_postgres`,
  `test_postgres_writer_leaves_the_session_row_to_the_state_store`).
- Все три `monkeypatch.setattr` стали параметрами; `grep` пуст.
- `Classifier.classify` возвращает `Verdict` на всех путях, включая
  `except Exception` (`test_unexpected_exception_is_ask`); `allow` по ошибке невыразим.
- `DecisionRecord` удалён; `metadata`/`metadata_` знает только `store/mapper.py`.
- Комментарии — только инварианты. Убрана единственная оставшаяся ссылка на задачу
  («Task 10» в `session/escalation.py`).
- Вывод тестов чистый: 871 passed, ни warning'ов, ни печати в stdout.

## 9. Что отложено / оговорки

- **Чужой коммит забрал переименования** (раздел 1). История не переписывалась; если владельцу
  важно, чтобы `git mv` лежали в моём коммите, это отдельное решение о rebase.
- `tests/test_schemas.py` не перенесён в `tests/api/` — `agentgate/api/schemas.py` эта задача не
  меняла, а бриф зеркалит только тронутые модули.
- `PostgresDecisionWriter.__init__(decisions, sessions, cache_ttl_seconds)` и
  `JsonlDecisionWriter.__init__(logger)` остались нетипизированными. Честная типизация требует
  ещё двух узких протоколов (`DecisionRecords`, `AllowCacheRecords`); бриф этого не просил, и это
  вне объёма F12 (границы API).
- `build_service` создаёт `httpx.AsyncClient()`, который никто не закрывает — как и прежний
  `build_app`. Поведение не менялось; закрытие клиента и `engine.dispose()` на shutdown —
  отдельная задача (`Service.engine` для этого уже есть).
- Атрибуция решения к API-ключу (`key_id` в `DecisionRow`/JSONL) по-прежнему не подключена —
  известное ограничение из корневого `CLAUDE.md`, задачей 6 не затрагивалось.
