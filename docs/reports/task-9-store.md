# Task 9 — Хранилище Postgres

**Статус:** закрыт. **Ветка:** `worktree-agent-a29cdd8366d5bdb11` (собственная ветка воркдерева задачи; ранее в этом отчёте ошибочно указывалась общая `feat/agentgate-task-1`, которая не двигалась, — исправлено). **Базовый коммит:** `1a40cda`.

## Что построено

| Файл | Содержимое |
|---|---|
| `service/agentgate/store/models.py` | `Base` (`DeclarativeBase`); `SessionRow` (таблица `sessions`) — счётчики сессии и `recent_decisions: JSONB`; `DecisionRow` (таблица `decisions`) — полная запись одного решения, колонка `metadata_` замаплена на физическую колонку `metadata`, пять индексов (`session+ts`, `model+ts`, `decision+ts`, `harness+ts`, GIN по `metadata`); `AllowCacheRow` (таблица `allow_cache`) — композитный PK `(session_id, action_hash)` |
| `service/agentgate/store/db.py` | `make_engine(db_url) -> AsyncEngine`, `make_session_factory(engine) -> async_sessionmaker` |
| `service/agentgate/store/repo.py` | `DecisionRecord` — датакласс 1:1 с `DecisionRow` (поле `metadata: dict` вместо `metadata_`), `to_row()`/`from_row()`/`to_dict()`; `DecisionRepo.insert`/`.list` (курсорная пагинация по `id` по убыванию, `before` — курсор); `SessionRepo.upsert` (через `INSERT ... ON CONFLICT DO UPDATE`), `.load_all`, `.cache_put`, `.cache_load_valid` |
| `service/alembic.ini`, `service/migrations/env.py`, `service/migrations/script.py.mako`, `service/migrations/versions/0001_init.py` | Alembic: конфиг, `env.py` с async-подключением, шаблон ревизии, сгенерированная автогенерацией первая миграция (три таблицы, пять индексов) |
| `service/docker-compose.yml` | Только сервис `db` (Postgres 16); сервис приложения — задача 12 |

Реализация выполнена по коду из брифа `docs/superpowers/service/sdd/task-9-brief.md` дословно — датаклассы, сигнатуры, имена таблиц и колонок, правила курсорной пагинации — как предписано.

Правка `service/tests/conftest.py`: добавлены `TEST_DB_URL`, маркер `requires_db` и асинхронные фикстуры `db_engine`/`session_factory` из брифа — после существующей автоприменяемой `_clear_agentgate_env`, которая не тронута. `TEST_DB_URL` читается из `os.environ` один раз при импорте модуля, до того как автофикстура очистки переменных `AGENTGATE_*` срабатывает для каждого теста, — очистка окружения на него не влияет.

Каждый метод репозитория открывает и коммитит свою собственную сессию и завершается — ни один метод не держит транзакцию открытой поперёк точки, видимой вызывающему коду. Это оставляет для задачи 10 возможность вызвать запись в хранилище уже после отправки HTTP-ответа клиенту, без неявной транзакции, растянутой на весь ответ.

## Проверка соединения с базой (до написания кода)

```
uv run python -c "... asyncpg.connect('postgresql://agentgate:agentgate@localhost:5433/agentgate_test') ... fetchval('select 1') ..."
```
Вывод: `1` — соединение подтверждено до начала работы над кодом хранилища.

## TDD

- **RED:** `cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest tests/test_store.py -v` — до создания пакета `agentgate/store`:
  ```
  ModuleNotFoundError: No module named 'agentgate.store'
  Interrupted: 1 error during collection
  ```
  Совпадает с ожиданием Step 3 брифа буквально.

- **GREEN:** тот же запуск после реализации `models.py`/`db.py`/`repo.py`:
  ```
  tests/test_store.py::test_session_upsert_and_load PASSED
  tests/test_store.py::test_decision_insert_and_list PASSED
  tests/test_store.py::test_allow_cache_roundtrip PASSED
  3 passed in 0.63s
  ```

**Эти три теста выполнялись против настоящей базы Postgres 16 на `localhost:5433` — они не были пропущены (`skip`).** Проверено явно в обе стороны: с `AGENTGATE_TEST_DB_URL` — `3 passed`; без переменной — `3 skipped` (guard для CI без базы, оставлен в коде, но это не тот прогон, результаты которого зафиксированы как сдача задачи).

- **Полный набор, с переменной БД, под `-W error`:**
  ```
  cd service && AGENTGATE_TEST_DB_URL=... uv run pytest -q -W error
  145 passed in 0.68s
  ```
  145 = 142 унаследованных (база задачи 8) + 3 новых теста хранилища. Вывод чистый, без warning'ов.

## Миграция: команда и вывод

База `agentgate` (целевая по умолчанию в `alembic.ini`, отдельная от `agentgate_test`, которую используют фикстуры pytest) не существовала на работающем инстансе Postgres — создана заранее через `asyncpg` (`CREATE DATABASE agentgate`, подключение `agentgate/agentgate` к базе `postgres`).

Генерация миграции:
```
cd service && uv run alembic revision --autogenerate -m "init" --rev-id 0001
Generating .../service/migrations/versions/0001_init.py ...  done
```
В файле — `op.create_table("sessions")`, `op.create_table("decisions")`, `op.create_table("allow_cache")` и `op.create_index('ix_decisions_metadata', 'decisions', ['metadata'], unique=False, postgresql_using='gin')` — проверено чтением сгенерированного файла.

Применение:
```
cd service && uv run alembic upgrade head
(вывод пуст, exit 0)
```

Проверено не по коду возврата alembic, а прямым запросом к живой базе через `asyncpg`:
```
tables: ['alembic_version', 'allow_cache', 'decisions', 'sessions']
decisions columns: id, session_id, ts, harness, tool, raw, normalized (jsonb), user_request, profile_id,
  profile_hash, decision, reason, suggest, stage, rule_id, model, model_raw_response (jsonb),
  latency_stage1_ms, latency_stage2_ms, latency_total_ms, error, cached, metadata (jsonb)
decisions indexes: decisions_pkey, ix_decisions_decision_ts, ix_decisions_harness_ts,
  ix_decisions_metadata (gin), ix_decisions_model_ts, ix_decisions_session_ts
alembic_version: ['0001']
```
Все три таблицы, все колонки `decisions` с правильными типами JSONB и все пять заявленных индексов (включая GIN по `metadata`) существуют в живой базе после `upgrade head`. Миграция реально выполнена, а не только сгенерирована.

## Соответствие глобальным ограничениям

- **Только Postgres:** JSONB-колонки, `postgresql.dialects.insert` для upsert'ов, никакого SQLite-фолбэка и никаких компромиссов по портируемости.
- **Запись после ответа не блокирует ответ:** каждый метод репозитория сам открывает и коммитит сессию — вызывающий код может await'ить запись уже после отправки HTTP-ответа без неявной сквозной транзакции.
- **Курсорная пагинация:** `DecisionRepo.list` — сортировка по `id` по убыванию, `before` фильтрует `id < before`; протестировано и подтверждено (постраничный тест из брифа).
- **Контрактные имена задач 10/11:** `Base`, `SessionRow`, `DecisionRow`, `make_engine`, `make_session_factory`, `DecisionRecord`, `DecisionRepo`, `SessionRepo` — присутствуют буквально с сигнатурами из брифа.

## Находки самопроверки

- **Полнота:** каждая таблица (`sessions`, `decisions`, `allow_cache`), каждая колонка, каждый индекс (пять на `decisions`, включая GIN) и каждый метод репозитория из брифа присутствуют, сигнатуры совпадают буквально.
- **Дисциплина:** ORM-удобства сверх брифа не добавлены (никаких `relationship`/`back_populates`, никаких гибридных свойств), кэширующий слой — только та таблица `allow_cache`, что предписана брифом, ретраев нигде в `store/` нет. Код — буквально по кодовым блокам брифа, включая неиспользуемый тестами, но предписанный интерфейсом метод `DecisionRecord.to_dict()`.
- **Тестирование:** все три теста проверяют реальное поведение против живой базы Postgres — без моков, без in-memory замены, без SQLite. Настоящий RED зафиксирован до появления какого-либо кода `store/`; настоящий GREEN — после. Guard пропуска проверен на срабатывание только при действительном отсутствии переменной; отчётные результаты — из прогона, где он не срабатывал.
- **Границы:** `git status --short` показывает только файлы внутри `service/` (в точности список из брифа) плюс сами отчётные файлы. Ничего вне `service/` и `reports/` не тронуто.

## Проблемы и наблюдения

- **Ключ идемпотентности (поднят координатором, реализация вне рамок этой задачи).** Контракт адаптера (появился позже брифа) предполагает идемпотентность по `(harness, session id, call id, direction)`. У `DecisionRow` сейчас нет колонок `call_id`/`direction` и нет уникального ограничения на эту комбинацию. Добавить это позже ничем не блокируется — это аддитивная миграция (новые nullable-колонки + новый уникальный индекс, `0002_...`). Единственное, что стоит иметь в виду: `DecisionRepo.insert()` сейчас делает простой `session.add()` + `commit()` без обработки конфликтов (в отличие от `SessionRepo.upsert`/`cache_put`, которые используют `pg_insert(...).on_conflict_do_update`). Если позже добавят уникальное ограничение под ключ идемпотентности, повторный вызов `insert()` упадёт `IntegrityError` вместо тихого поглощения — реализующему идемпотентность придётся либо ловить это на месте вызова, либо переделать `insert()` в upsert/`on_conflict_do_nothing`. Действовать сейчас не нужно, отмечаю именно потому, что это тот самый вид трения, о котором спрашивал координатор.
- **`service/docker-compose.yml` изначально был написан, но не прогнан через `docker compose up`.** Исправлено в раунде фиксов ниже — см. «Важное 3».
- **Исправленное заблуждение (было здесь ошибочно, см. раунд фиксов).** Первая версия этого пункта утверждала, что пробел на чистой машине — это база `agentgate`, а не `agentgate_test`. Это было **перепутано направление**: `alembic.ini` целится в `agentgate`, которую и так создаёт `POSTGRES_DB: agentgate` в компоуз-файле — там пробела не было и нет. Реальный пробел — ровно наоборот: `agentgate_test`, на которую указывает `AGENTGATE_TEST_DB_URL`, не создаётся ничем в репозитории. Ручное создание `agentgate` (через `asyncpg`, `CREATE DATABASE agentgate`) было нужно только потому, что уже работающий на момент задачи контейнер был поднят с `POSTGRES_DB=agentgate_test` — это особенность конкретного окружения, а не пробел в коде. Исправлено — см. «Важное 3» ниже.

## git status --short (до коммита раунда 1)

```
 M service/tests/conftest.py
?? service/agentgate/store/
?? service/alembic.ini
?? service/docker-compose.yml
?? service/migrations/
?? service/tests/test_store.py
```

---

## Раунд фиксов 1 (после внешнего ревью)

Базовый коммит фикса: `5544693`. Ревью вручную выполнило двенадцать граничных сценариев против живой базы — ни один не сломался, критичных находок и разрывов контракта для задач 10/11 нет. Проблема была не в поведении, а в том, что закоммиченный набор тестов (три happy-path теста из брифа) почти ничего из этого не доказывал. Раунд добавляет недостающую сеть регрессионных тестов, чинит два реальных бага честным TDD, закрывает пробел с автосозданием тестовой базы в компоуз-файле и убирает мёртвый импорт.

### Важное 1 — добавлены граничные тесты (16 новых, 3 → 19 в `test_store.py`)

Все выполняются против живой базы. Как и предупреждало ревью: большинство прошли с первого запуска, потому что поведение уже было верным, — это регрессионные страховки, а не фиксы багов; падения я не подделывал, ломая реализацию искусственно. Исключение — два теста ниже (лимит и `expires_at`), для них честный RED действительно был получен (см. «Важное 2»).

- `test_decision_list_pages_to_exhaustion_without_skip_or_duplicate` — постраничный обход 5 строк с `limit=2` до пустой страницы, без пропусков и дублей.
- `test_decision_list_cursor_edges` — несуществующий, но синтаксически валидный `before` (сверено с тем же `<`-семантикой, посчитанной в Python), `before` == самый маленький существующий `id` (пустая страница), `before` ниже всего диапазона (`""`), `before` выше всего диапазона (`"z"*26`, без падений).
- `test_decision_list_limit_exceeding_remaining_rows_returns_all` — `limit=1000` при 3 строках возвращает ровно 3.
- `test_decision_list_limit_is_clamped_to_valid_range` — `limit=0`, `-1`, `10_000_000` — все зажаты в `[1, 1000]` (см. «Важное 2» — здесь был честный RED).
- `test_decision_metadata_and_normalized_survive_non_ascii_and_nesting` — кириллица, эмодзи (включая ZWJ-последовательность), вложенные кавычки/переносы/табы, вложенные структуры в `metadata` и `normalized`.
- `test_decision_empty_and_null_jsonb_distinguished_from_populated` — `metadata={}` и `model_raw_response=None` в явном контрасте с заполненными вариантами.
- `test_recent_decisions_round_trips_at_and_above_capacity` — 60 записанных решений (> `RECENT_MAXLEN=50`) переживают `upsert`/`load_all` как последние 50 по порядку, с правильным `maxlen`.
- `test_recent_decisions_round_trips_when_empty` — сессия без единой записи загружается с пустой очередью и правильным `maxlen`.
- `test_duplicate_decision_id_raises_integrity_error` — фиксирует `IntegrityError` как форму ошибки, которую должна учитывать задача 10.
- `test_insert_orphan_decision_raises_integrity_error` / `test_cache_put_orphan_decision_raises_integrity_error` — см. «Важное 3».
- `test_created_at_preserved_across_upsert` — читает `SessionRow.created_at` напрямую (это поле не выставлено в `SessionState`) до и после повторного `upsert`, проверяет неизменность.
- `test_cache_load_valid_full_payload` — проверяет все четыре элемента кортежа, не только первые два.
- `test_load_all_returns_multiple_sessions` — две разные сессии, проверка полей каждой.
- `test_decision_record_to_dict` — метод, к которому обращается JSONL-писатель задачи 10; проверка сериализации `ts` в ISO-строку и передачи остальных полей.
- `test_cache_put_requires_timezone_aware_expires_at` — см. «Важное 2».

### Важное 2 — два реальных бага, честный TDD

Для обоих был доступен настоящий RED, поэтому я застэшил только фикс-часть `service/agentgate/store/repo.py` (`git stash push --keep-index -- service/agentgate/store/repo.py`), прогнал новые тесты против дофиксовой версии, зафиксировал падения, затем `git stash pop`, вернув фикс (стек стэша в итоге пуст — проверено `git stash list`).

**RED** (`AGENTGATE_TEST_DB_URL=... uv run pytest tests/test_store.py -v -k "limit_is_clamped or timezone_aware"`, против дофиксового `repo.py`):
```
tests/test_store.py::test_decision_list_limit_is_clamped_to_valid_range FAILED
E       assert 0 == 1  (limit=0 вернул пустой список вместо клэмпа до 1)
tests/test_store.py::test_cache_put_requires_timezone_aware_expires_at FAILED
E       Failed: DID NOT RAISE ValueError
2 failed, 17 deselected in 0.40s
```

**Фикс 1 — `limit` без валидации.** `DecisionRepo.list` теперь делает `limit = max(1, min(limit, 1000))` перед построением запроса. `limit=0`/отрицательные больше не возвращают тихо пустоту (либо, для отрицательных, не падают `DBAPIError`/`asyncpg.InvalidRowCountInLimitClauseError` — этот докодовый сбой проверен вручную перед фиксом); патологически большой `limit` больше не проходит насквозь как неограниченное чтение. Задокументировано в докстринге метода.

**Фикс 2 — наивный `expires_at` тихо переинтерпретировался.** `SessionRepo.cache_put` теперь сразу кидает `ValueError`, если `expires_at.tzinfo is None`, с докстрингом, объясняющим почему: наивное значение не трактуется как UTC — драйвер переинтерпретирует его через таймзону сессии соединения, — тогда как `cache_load_valid` всегда сравнивает с tz-aware UTC `now`; это давало бы тихий сдвиг момента истечения кэша (репродукция ревью: наивный `datetime(2099,1,1,12,0,0)` на хосте с MSK превращался в Postgres в `09:00:00+00`, на три часа раньше).

**GREEN** (тот же прогон после `stash pop`): все 19 тестов в `test_store.py`, включая оба исправленных.

### Важное 3 — порядок по FK: докстринги + тесты

В докстринг класса `DecisionRepo` и метода `insert` добавлено: `session_id`, если не `None`, — внешний ключ на `sessions.id`, ссылка должна уже существовать — сперва `SessionRepo.upsert`, потом `DecisionRepo.insert`. Аналогичная заметка добавлена в докстринг `SessionRepo.cache_put` про оба его FK (`decisions.id`, `sessions.id`).

Два новых теста фиксируют форму ошибки: `test_insert_orphan_decision_raises_integrity_error` (сессия никогда не апсертилась, `insert` с несуществующим `session_id` → `IntegrityError`) и `test_cache_put_orphan_decision_raises_integrity_error` (сессия есть, решения нет, `cache_put` с несуществующим `decision_id` → `IntegrityError`). Оба поведения уже были верны (Postgres сам enforce'ит FK независимо от ORM-кода), оба теста прошли с первого запуска — не фикс бага, а страховка плюс документация реального требования по порядку вызовов для задачи 10.

### Важное 4 — реальный пробел на чистой машине: `agentgate_test`, а не `agentgate`

Ревью верно указало, что моя первоначальная третья заметка была перепутана. `alembic.ini` целится в `agentgate`, которую и так создаёт `POSTGRES_DB: agentgate` в компоуз-файле — там пробела не было. Настоящий пробел — `agentgate_test`, на которую указывает `AGENTGATE_TEST_DB_URL` для тестов хранилища: на чистой машине `docker compose up -d db` сам по себе её не создаёт, и тесты с задокументированным URL **падают на подключении**, а не аккуратно пропускаются — худший из двух исходов, потому что выглядит как баг, а не как отсутствие базы.

**Исправлено в компоуз-файле** (не только в README): добавлен `service/scripts/init-test-db.sql` (`CREATE DATABASE agentgate_test;`), примонтированный read-only в `/docker-entrypoint-initdb.d/init-test-db.sql` в `service/docker-compose.yml`. Entrypoint-скрипт Postgres выполняет каждый `*.sql`/`*.sh` под этим путём один раз, при первом старте на пустом каталоге данных — без дополнительных шагов при обычном `docker compose up -d db`.

**Выбран фикс в компоуз-файле, а не только заметка в README**, потому что инструкция в README — то, что человеку нужно помнить и вручную выполнить, а init-скрипт делает верное состояние автоматическим. В `service/README.md` также добавлен короткий раздел, документирующий механизм и его единственное реальное ограничение: init-скрипты выполняются только на пустом каталоге данных, поэтому уже существующий том `pgdata` (в том числе тот, что использовался при первой сдаче этой задачи — он был поднят напрямую с `POSTGRES_DB=agentgate_test`, а не через этот компоуз-файл) не получит `agentgate_test` задним числом; README даёт оба средства (`docker compose down -v` для пересоздания тома, либо ручной `CREATE DATABASE`).

**Проверено сквозным прогоном, не только чтением файла на правдоподобие**: поднят одноразовый `postgres:16-alpine` на отдельном порту (`docker run ... -p 15433:5432 -v .../init-test-db.sql:/docker-entrypoint-initdb.d/init-test-db.sql:ro`, без предсуществующего тома), дождался `pg_isready`, затем `psql -c "select datname from pg_database"` — `agentgate_test` появилась рядом с `agentgate` без единого ручного шага. Также прогнан `docker compose config` против отредактированного `docker-compose.yml` — конфиг парсится, новый bind-mount резолвится в верный путь. Одноразовый контейнер остановлен и удалён (`docker stop`, флаг `--rm`) сразу после проверки; уже работающий боевой контейнер, на котором крутятся тесты этой задачи, не был затронут.

### Второстепенные пункты

- **Клэмп `limit`** — объединено с «Важное 2» (тот же фикс, тот же тест).
- **Наивный `expires_at`** — объединено с «Важное 2» (тот же фикс, тот же тест).
- **Неиспользуемый импорт `from sqlalchemy import text` в `tests/conftest.py`** — удалён. Автоприменяемая `_clear_agentgate_env` и фикстуры `db_engine`/`session_factory` не тронуты.

### Полный набор после фиксов

```
$ cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q -W error
161 passed in 1.62s
```
161 = 142 унаследованных + 19 в `test_store.py` (3 исходных + 16 новых). Guard пропуска на этом прогоне не срабатывал — отдельно проверено: `unset AGENTGATE_TEST_DB_URL && uv run pytest tests/test_store.py -q` → `19 skipped`, то есть оба состояния различимы, и отчётные 161 — действительно из подключённого прогона.

### Явно вне рамок, не тронуто

По указанию координатора: несоответствие ключа пагинации и индексов (`id`-пагинация против индексов `(x, ts)` — индекс вида `(session_id, id DESC)` относится к будущей миграции), `created_at`/`last_seen_at` как write-only поля (в `SessionState` для них нет полей), захардкоженные dev-креды в `alembic.ini`, размер пула `make_engine`, колонки идемпотентности.

### Изменённые файлы в этом раунде

```
 M service/README.md
 M service/agentgate/store/repo.py
 M service/docker-compose.yml
 M service/tests/conftest.py
 M service/tests/test_store.py
?? service/scripts/init-test-db.sql
```

### Проблемы, перенесённые без изменений

Та же, что и в раунде 1: трение с ключом идемпотентности (`DecisionRepo.insert` без обработки конфликтов; будущее уникальное ограничение под `(harness, session_id, call_id, direction)` потребует либо перехвата на месте вызова, либо переделки в upsert) — по-прежнему явно вне рамок этой задачи, повторно не разбирается.
