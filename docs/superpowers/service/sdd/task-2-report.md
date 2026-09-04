# Задача 2: `Decision`, `DecisionWriter` и разбор `Gate.decide()`

Ветка `refactor/solid-v1.5`, коммит **`bab29cc`** (родитель `449eb8d`, задача 1).

## Что построено

1. **`agentgate/engine/decision.py`** — `Decision` (frozen dataclass: сырой исход одного вызова —
   запрос, `Verdict`, `Latency`, что было нормализовано, состояние сессии) и `DecisionView`
   (pydantic-модель плоской формы: JSONL-строка, строка Postgres и элемент `GET /v1/decisions` —
   теперь одна и та же форма, определённая один раз). `Decision.to_response()` строит
   `DecideResponse`, `Decision.to_view()` строит `DecisionView`. Поле `decision_id` в `DecisionView`
   — `@computed_field`, всегда равно `id`.

2. **`agentgate/store/writer.py`** — протокол `DecisionWriter` (`async def write(self, decision) ->
   None`, никогда не бросает) и три реализации:
   - `JsonlDecisionWriter` — пишет `decision.to_view().model_dump(mode="json")` в лог;
   - `PostgresDecisionWriter` — пишет сессию → решение → allow-cache в этом порядке (FK-требование:
     `decisions.session_id → sessions.id`, `allow_cache.decision_id → decisions.id`); кэширует
     только `allow`, только когда есть `cache_key`, никогда повторно для уже кэшированного попадания;
     пропускает запись сессии для звонков без `session_id`;
   - `CompositeDecisionWriter` — запускает все writer'ы, ловит и логирует (`log.exception`, id
     решения в тексте) отказ одного, не давая ему остановить остальные.

   Заменяет два ранее существовавших пути персистентности: `Gate._do_persist` (жил только в
   `pipeline.py`, использовался только тестами) и замыкание `persist` в `api/app.py` (боевой путь).
   Оба ушли; поведение боевого пути (порядок FK, кэширование только `allow`, отказ одного writer'а
   не роняет решение) перенесено в `PostgresDecisionWriter`/`CompositeDecisionWriter` без изменений.

3. **`agentgate/store/repo.py`** — временный мост: `DecisionRepo.insert` теперь принимает
   `Decision` (было `DecisionRecord`) и пишет через `_row_from_view(decision.to_view())`.
   `DecisionRecord` остаётся (нужен `DecisionRepo.list`, уходит в задаче 6) и получил метод
   `to_view() -> DecisionView` — поля `DecisionRecord` совпадают с полями `DecisionView` один в
   один, так что это чистая переупаковка без потери данных. Это понадобилось не для тестов из
   брифа задачи 2, а чтобы `tests/test_store.py` и `tests/test_main.py` (файлы, которые задача не
   должна была трогать — не входят в её `git add`) продолжили собирать строки напрямую через
   `DecisionRepo.insert(...)`, минуя `Gate`/`DecisionWriter`, без единой правки в этих файлах.
   Мёртвый `DecisionRecord.to_row()` (единственный вызывающий — старый `insert`) удалён.

4. **`agentgate/engine/gate.py`** (новый модуль, `pipeline.py` удалён) — `Gate.decide()` разложен на
   `_resolve` (профиль/модель/сессия → `_Context` или ранний `Verdict.ask`), `_evaluate` (ступень 1
   → ступень 2), `_escalate`, `_settle_session`, `_finish` (сборка `Decision`). `Gate` больше не
   знает о персистентности вообще — ни поля `_persist`, ни аргумента в конструкторе.

5. **`agentgate/config.py`** — `Settings.allow_cache_ttl_seconds: int = 86400`. Убирает дублирование
   знания о TTL, которое раньше жило в двух местах: `_CACHE_TTL_SECONDS` в `api/app.py` и дефолт
   `cache_ttl_seconds=86400` в `Gate.__init__`.

6. **`agentgate/api/app.py`** — `create_app(settings, gate, writer, decision_repo, profiles,
   db_probe=None, key_repo=None)`; убраны `_CACHE_TTL_SECONDS`, замыкание `persist`, функция `_ask`.
   Ранний отказ собирается в `_refuse` напрямую через `DecideResponse(...)` (см. «Развилки брифа»
   ниже). Роут `decide` формирует ответ через `decision.to_response()` и планирует
   `background.add_task(writer.write, decision)`. Роут `/v1/decisions` не тронут — работает с
   `decision_repo.list()`, который пока возвращает `DecisionRecord` (не в этой задаче).

7. **`agentgate/__main__.py`** — `build_app` собирает `writer = CompositeDecisionWriter([Jsonl...,
   Postgres...])` (JSONL первым — сохраняет прежний порядок: отказ Postgres не отменяет запись в
   лог) и передаёт его в `create_app`. `make_db_probe` и обработчик `/healthz` больше не глушат
   исключение молча — `log.warning("database probe failed", exc_info=True)` перед `return False`.

8. **`tests/factories.py`** (новый) — `WORKSPACE`, `profile()`, `decide_request()`, `FakeLLM`,
   `RecordingDecisionWriter`, `FailingDecisionWriter`. Все новые тестовые модули (и `test_api.py`)
   импортируют отсюда — устраняет кросс-импорты (`test_api.py` раньше тянул `FakeLLM`/`profile` из
   `test_pipeline.py`).

## Развилки брифа (обе разрешены по правилу «побеждает более позднее указание»)

1. **Шаг 4, `test_postgres_writer_upserts_the_session_before_the_decision`.** В файл вошла только
   честная версия: класс `OrderRecordingRepos` (оба репозитория пишут в общий лог порядка) и тест
   `test_postgres_writer_writes_session_then_decision_then_cache`, утверждающий
   `repos.order == ["session", "decision", "cache"]`. Черновик с лямбда-хаком
   (`sessions.upsert = lambda s: order.append(...) or _done()`) в файл не попал.

2. **Шаг 9, `_refuse`.** Реализован без метода `Verdict.to_response_for` — `DecideResponse`
   собирается прямо в `_refuse` из полей `Verdict.ask(...)`.

Третьей подобной развилки в брифе не найдено.

## Доказательства TDD

### `Decision` / `DecisionView` (шаг 2 → 3)

RED:
```
$ cd service && uv run pytest tests/engine/test_decision.py -v
...
tests/engine/test_decision.py:5: in <module>
    from agentgate.engine.decision import Decision
E   ModuleNotFoundError: No module named 'agentgate.engine.decision'
```
Ожидаемо — модуль ещё не существовал.

GREEN после реализации `agentgate/engine/decision.py`:
```
$ cd service && uv run pytest tests/engine/test_decision.py -v
...
7 passed in 0.14s
```

### `DecisionWriter` (шаг 4 → 5)

RED:
```
$ cd service && uv run pytest tests/store/test_writer.py -v
...
E   ModuleNotFoundError: No module named 'agentgate.store.writer'
```

GREEN после реализации `agentgate/store/writer.py` + моста в `agentgate/store/repo.py`:
```
$ cd service && uv run pytest tests/store/test_writer.py -v
...
9 passed in 0.14s
```
(9 = 8 исходных тестов минус лямбда-хак плюс честная замена — как и требовал бриф.)

### `Gate` (шаг 7 → 8)

RED:
```
$ cd service && uv run pytest tests/engine/test_gate.py -v
...
E   ModuleNotFoundError: No module named 'agentgate.engine.gate'
```

GREEN после реализации `agentgate/engine/gate.py` и удаления `pipeline.py`/`test_pipeline.py`:
```
$ cd service && uv run pytest tests/engine -v
...
27 passed in 0.93s
```

## Полный прогон

Базовая линия перед началом (подтверждена): `524 passed in 7.25s`.

Финальный прогон с реальной БД:
```
$ cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q
543 passed in 7.82s
```
Прогон повторён и с `-W error::DeprecationWarning` — новых предупреждений нет.

Отдельно проверены «замороженные» табличные тесты (их ожидания не менялись):
```
$ cd service && uv run pytest tests/test_stage1_hard_deny.py tests/test_stage1_chain.py \
    tests/test_normalize_shell.py tests/test_stage1_latency.py -q
269 passed in 0.56s
```

Контракт:
```
$ cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py \
    && git diff --exit-code ../contracts
EXIT:0   # пустой diff — публичный контракт не сдвинулся
```

## Файлы

Создано:
- `service/agentgate/engine/decision.py`
- `service/agentgate/engine/gate.py`
- `service/agentgate/store/writer.py`
- `service/tests/factories.py`
- `service/tests/store/__init__.py`, `service/tests/store/test_writer.py`
- `service/tests/engine/test_decision.py`, `service/tests/engine/test_gate.py`

Удалено:
- `service/agentgate/pipeline.py`
- `service/tests/test_pipeline.py`

Изменено:
- `service/agentgate/store/repo.py` — мост `insert(Decision)`, `_row_from_view`,
  `DecisionRecord.to_view()`, удалён мёртвый `DecisionRecord.to_row()`, поправлена устаревшая
  ссылка на «Task 10» и параметр `rec` в докстринге `DecisionRepo`.
- `service/agentgate/api/app.py` — новая сигнатура `create_app`, `_refuse`, роут `decide` через
  `Decision`/`writer.write`.
- `service/agentgate/__main__.py` — сборка `CompositeDecisionWriter`, `Gate` с
  `allow_cache_ttl_seconds`, логирующий `make_db_probe`.
- `service/agentgate/config.py` — поле `allow_cache_ttl_seconds`.
- `service/tests/test_api.py` — импорты из `tests.factories`, `writer` вместо `session_repo`/`jsonl`,
  `FakeDecisionRepo.list()` возвращает объекты с `.id`/`.to_dict()` (адаптер `_ListedDecision` вокруг
  `DecisionView`) — маршрут `/v1/decisions` в этой задаче не менялся и по-прежнему ждёт эту форму от
  `decision_repo.list()`.
- `service/tests/test_config.py` — тест дефолта `allow_cache_ttl_seconds`.

## Находки саморевью и как закрыты

- **Мёртвый код после моста в `repo.py`.** `DecisionRecord.to_row()` остался без единого вызывающего
  после переключения `DecisionRepo.insert` на `_row_from_view`. Удалён.
- **Неиспользуемый импорт `DecisionKind`** в `tests/store/test_writer.py` — присутствовал в
  исходном коде брифа, но тесты его не используют (сборка `Verdict` идёт через
  `Verdict.allow/deny`). Убран.
- **Устаревшая ссылка на «Task 10»** в докстринге `DecisionRepo` плюс имя параметра `rec`, оставшееся
  от старой сигнатуры `insert(rec: DecisionRecord)`. Поправлено на нейтральную формулировку без
  привязки к номеру задачи и с текущим именем параметра.
- **Проверено, а не предположено:** ручная AST-проверка неиспользуемых импортов по всем
  изменённым файлам (`ruff`/`pyflakes` в окружении нет) — чисто, кроме двух находок выше и
  ранее существовавшего неиспользуемого `import pytest` в `test_api.py` (не мой код, не трогал).

## Принятые решения

- **Мост `DecisionRecord.to_view()`** не был явно расписан в брифе, но необходим: без него
  `DecisionRepo.insert`, принимающий теперь только `Decision`-подобные объекты (через `.to_view()`),
  ломает `tests/test_store.py` (17+ мест, где тест напрямую собирает `DecisionRecord` и передаёт в
  `insert`) и `tests/test_main.py` — ни один из этих файлов не значится в списке `git add` брифа,
  то есть бриф расчитывал, что они останутся нетронутыми. Поля `DecisionRecord` совпадают с полями
  `DecisionView` один в один, так что `to_view()` — это чистая, без потерь, переупаковка; после
  добавления этого метода оба файла прошли **без единой правки**, что и подтверждает: это был
  правильный уровень для моста, а не разовый костыль в тестах.
- **`FakeDecisionRepo.list()` в `test_api.py`** оборачивает `DecisionView` в маленький локальный
  адаптер `_ListedDecision` (`.id` + `.to_dict()`), потому что маршрут `/v1/decisions` в этой задаче
  сознательно не трогается (перепись — задача 6) и по-прежнему читает `.to_dict()`/`.id` с того,
  что вернул `decision_repo.list()` — форма, которую реальный (не тронутый в этой задаче)
  `DecisionRepo.list()` всё ещё возвращает через `DecisionRecord`.
- **`agentgate/engine/decision.py`**: импорт `from dataclasses import dataclass` перенесён к началу
  файла (в коде брифа он стоял между `DecisionView` и `Decision`) — порядок «импорты → классы»
  соответствует стайл-гайду 3.2, поведение не меняется.

## Что отложено

Ничего из объёма задачи 2 не отложено. Известные явные границы задачи (не входят в неё по брифу):
- `DecisionRecord` и маппинг строки Postgres остаются до задачи 6.
- Маршрут `/v1/decisions` продолжает работать через `DecisionRecord` — переезжает на `DecisionView`
  в задаче 6.
- Привязка решения к API-ключу (`key_id` в `Decision`/`DecisionView`) — известное ограничение,
  зафиксированное в корневом `CLAUDE.md`, эта задача его не затрагивает.
