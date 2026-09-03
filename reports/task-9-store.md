# Task 9 — Хранилище Postgres

**Статус:** закрыт. **Ветка:** `feat/agentgate-task-1`. **Базовый коммит:** `1a40cda`.

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
- **`service/docker-compose.yml` написан, но не прогнан через `docker compose up`.** По условиям задачи контейнер Postgres 16 на порту 5433 уже был поднят с подходящими кредами — подключался к нему напрямую через `asyncpg`, вместо того чтобы поднимать второй контейнер, который конфликтовал бы по тому же порту хоста. Форма compose-файла в точности соответствует брифу; сквозную проверку `docker compose up -d db` самостоятельно не выполнял.
- **База `agentgate` (цель `alembic.ini` по умолчанию) пришлось создать вручную** — она не существовала рядом с `agentgate_test`. Создана напрямую через `asyncpg` (`CREATE DATABASE agentgate`), чтобы у `alembic upgrade head` было на чём выполниться для проверки. Это не часть штатного пути старта приложения (видимо, задача ops или задачи 11), просто фиксирую как шаг, которого нет в брифе явно.

## git status --short (до коммита)

```
 M service/tests/conftest.py
?? service/agentgate/store/
?? service/alembic.ini
?? service/docker-compose.yml
?? service/migrations/
?? service/tests/test_store.py
```
