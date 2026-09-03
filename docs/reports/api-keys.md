# API-ключи: выдача, хранение, проверка

Задача добавлена продакт-оунером сверх плана из 13 задач, поверх результата Task 12. Спека:
`docs/superpowers/service/specs/api-keys.md`. Реализация — с одним контролируемым отступлением от
спеки (см. ниже).

## Что построено

- **`service/agentgate/store/models.py`** — `ApiKeyRow`, таблица `api_keys`: `id` (ULID, PK),
  `key_hash` (SHA-256, уникальный индекс `ix_api_keys_key_hash`), `label`, `created_at`,
  `expires_at`/`revoked_at`/`last_used_at` (все nullable).
- **`service/migrations/versions/0002_api_keys.py`** — аддитивная миграция, `down_revision = '0001'`,
  создаёт только `api_keys` и её индекс, ничего существующего не трогает. **Применена** к обеим
  живым базам на порту 5433 — см. ниже.
- **`service/agentgate/store/keys.py`** — `generate_key()` (`agk_` + `base64url(secrets.token_bytes(32))`
  без паддинга), `hash_key()` (SHA-256 hex), `ApiKeyRecord.is_valid()` (не отозван и не просрочен),
  `ApiKeyRepo` — `create` (возвращает открытый ключ один раз), `get_by_hash`, `list`, `revoke`
  (мягкий, идемпотентный), `touch_last_used`.
- **`service/agentgate/cli.py`** — `python -m agentgate keys create --label <s> [--expires <90d|12h|30m|45s>]`,
  `keys list`, `keys revoke <key_id>`. `create` печатает ключ в stdout и больше ничего; любая ошибка
  (нераспознанная длительность, неизвестный `key_id`, ошибка хранилища) — в stderr, ненулевой код
  возврата, stdout остаётся пустым.
- **`service/agentgate/__main__.py`** — `main()` перед стартом сервера проверяет
  `sys.argv[1] == "keys"` и делегирует в `run_keys_cli`, не доходя до `build_app`/`uvicorn.run`
  (проверено тестом, который подменяет оба и убеждается, что ни один не вызван). `build_app()`
  собирает `ApiKeyRepo` на той же session factory, что и остальные репозитории, и передаёт его в
  `create_app(..., key_repo=key_repo)`.
- **`service/agentgate/api/deps.py`** — переписан. Класс `_KeyVerifier` — проверка ключа с
  in-process TTL-кэшем по хэшу; `make_require_token` теперь аддитивна (детали — ниже).
- **`service/agentgate/config.py`** — `Settings.api_key_cache_ttl_seconds: float = 45.0`
  (`AGENTGATE_API_KEY_CACHE_TTL_SECONDS`). `validate_token_for_bind()` не менялся.
- **`service/README.md`** — короткий раздел «API-ключи»: команды CLI, аддитивность поверх токена,
  TTL кэша.

## Отступление от спеки (по прямому указанию контроллера)

Спека требует, чтобы non-localhost bind полностью игнорировал `AGENTGATE_TOKEN` и принимал только
выданные ключи, а `validate_token_for_bind()` был перевёрнут. **Это сознательно не реализовано** —
такая строгая постановка сломала бы уже смёрженные тесты Task 11, которые строят `Settings`/
`create_app` со статическим токеном на non-localhost bind. Вместо этого ключи добавлены
**аддитивно**: `make_require_token` пропускает запрос, если bearer совпадает со статическим
`AGENTGATE_TOKEN` (старый путь через `secrets.compare_digest`, без изменений) **или** с
действующим выданным ключом. `validate_token_for_bind` — байт в байт как было. Более строгий
вариант спеки остаётся отложенным хардненингом на будущее.

Порядок проверки в `require_token`:

1. Токен не задан → пропустить всех (старое dev-режим поведение на localhost, ключи не
   опрашиваются вовсе — `repo.calls == 0` в тесте).
2. Токен задан и bearer совпал с ним по `compare_digest` → пропустить (ключевой репозиторий даже
   не вызывается).
3. Иначе, если передан `key_repo` и заголовок вида `Bearer <token>` — проверить `<token>` как ключ
   через `_KeyVerifier`.
4. Иначе — `401`, единое сообщение независимо от причины (нет заголовка, неверный токен,
   неизвестный/просроченный/отозванный ключ, ошибка БД при проверке ключа).

## Миграция: применена к живому Postgres на 5433

```
$ AGENTGATE_DB_URL="postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate" uv run alembic upgrade head
$ AGENTGATE_DB_URL="postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test" uv run alembic upgrade head
```

Проверено через `psql` на обеих базах:

```
$ docker exec agentgate-pg psql -U agentgate -d agentgate -c "select * from alembic_version"
 version_num
-------------
 0002

$ docker exec agentgate-pg psql -U agentgate -d agentgate -c "\d api_keys"
    Column    |           Type           | Nullable
--------------+--------------------------+----------
 id           | character varying(26)    | not null
 key_hash     | character varying(64)    | not null
 label        | character varying(128)   | not null
 created_at   | timestamp with time zone | not null
 expires_at   | timestamp with time zone |
 revoked_at   | timestamp with time zone |
 last_used_at | timestamp with time zone |
Indexes:
    "api_keys_pkey" PRIMARY KEY, btree (id)
    "ix_api_keys_key_hash" UNIQUE, btree (key_hash)
```

То же самое — на `agentgate_test`. Таблица и уникальный индекс существуют на обеих базах, версия
`0002` зафиксирована в `alembic_version`.

## Пример `keys create` (форма `agk_...`)

Запуск против реальной (не тестовой) базы через настоящий CLI-путь:

```
$ AGENTGATE_DB_URL="postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate" \
  uv run python -m agentgate keys create --label smoke-test --expires 1d
agk_bb6zkSI3W_WL61YrS81XoqL1wTPoNeLo7bmNy6dLl-E
```

`keys list` сразу после показал только `id`/`label`/`created_at`/`expires_at` — без ключа и без
хэша. Это одноразовый smoke-тест-ключ; он тут же отозван (`keys revoke 01M1KZ3NKBCBHGNR7GJQVJR2F3`,
код возврата 0) и нигде больше не используется — привилегий он ни на что не даёт (сервис сейчас
доступен только на localhost).

## TDD

Каждый поведенческий модуль был красным до того, как стал зелёным:

- **`tests/test_deps_keys.py`** (13 тестов) — написан против старой сигнатуры
  `make_require_token(settings)`. Первый прогон: `TypeError: unexpected keyword argument
  'key_repo'` на 12 из 13 (13-й не использует `key_repo` вовсе, ожидаемо прошёл и на старом коде).
  После переписывания `deps.py` — 13/13 зелёных.
- **`tests/test_api.py`** (`build()` расширен параметром `key_repo`, 2 новых теста) — первый
  прогон: `TypeError: create_app() got an unexpected keyword argument 'key_repo'` на всех 16 тестах
  файла (общий хелпер `build()` теперь всегда передаёт `key_repo=`). После добавления параметра в
  `create_app` — 16/16.
- **`tests/test_cli_keys.py`** (9 тестов) — первый прогон: `ModuleNotFoundError: No module named
  'agentgate.cli'`. После реализации `cli.py` поймал реальный баг (ниже) — после исправления 9/9.
- **`tests/test_keys.py`** (18 тестов) — по честности: этот файл я написал не строго до
  реализации `store/keys.py` (в сессию попало прерывание по инфраструктурной ошибке, и я
  восстанавливал контекст через `git diff`, а не заново с «сначала тест»). Тесты реальные и один
  раз были пойманы падающими по правильной причине (см. баг с `pytestmark` ниже), но строгую
  дисциплину red-first для этого одного файла не подтверждаю — отмечаю явно, а не скрываю.

**Баг, пойманный на красной фазе (deps.py)**: первый черновик использовал
`background: BackgroundTasks | None = None`. FastAPI попытался трактовать `| None` как
Pydantic-поле ответа, и все роуты в `test_api.py` падали на этапе сборки приложения с
`FastAPIError: Invalid args for response field!`. Исправлено: `background: BackgroundTasks = None`
(голый специальный тип с «неподходящим», но безвредным дефолтом) — FastAPI распознаёт
`BackgroundTasks` по точному типу и всегда подставляет реальный экземпляр независимо от дефолта, а
прямые юнит-тесты (не через ASGI), не передающие `background`, получают `None` и просто не
планируют обновление `last_used_at`.

**Баг, пойманный на красной фазе (test_cli_keys.py)**: первая версия использовала `async def
test_...` вместе с проектными асинхронными фикстурами `db_engine`/`session_factory`, а затем
вызывала `run_keys_cli(...)` (внутри — свой `asyncio.run`) изнутри уже работающего цикла
pytest-asyncio → `RuntimeError: asyncio.run() cannot be called from a running event loop`, плюс
отдельно `attached to a different loop` от asyncpg при смешении движка из одного цикла с
awaitами из другого. Исправлено: все тесты в файле — обычные синхронные функции, которые вообще не
трогают цикл pytest-asyncio; фикстура `fresh_db` и хелпер `_with_repo` каждый раз открывают свой
движок, делают одно дело и закрывают его внутри одного самодостаточного `asyncio.run()`.

## Проверка: полный набор тестов, с БД и без, под `-W error`

```
$ uv run pytest -q -W error                       # без AGENTGATE_TEST_DB_URL
446 passed, 43 skipped in 2.48s

$ AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test \
  uv run pytest -q -W error
489 passed in 7.84s
```

Базовая линия (до изменений, на `b644462`): 422/25 без БД, 447 с БД. Прирост: +24 passed / +18
skipped без БД, +42 passed с БД. Вывод чистый — ни одного предупреждения, повышенного до ошибки, ни
одного неожиданного skip.

## Кэш проверки и отзыв в пределах TTL

`_KeyVerifier` хранит `dict[key_hash] -> (key_id_или_None, время_кэширования)`. На каждый запрос:
хэшируем bearer (SHA-256, plaintext никогда не сравнивается и не кэшируется напрямую), при
попадании в кэш моложе TTL (по умолчанию 45 c) отдаём закэшированный результат без обращения к
БД — это то, что проверяет `test_cache_serves_repeat_without_second_db_hit` через
считающий-вызовы фейковый репозиторий. Иначе — запрос в `get_by_hash`; любое исключение (БД
недоступна, таймаут) ловится, логируется (только текст исключения, никогда не хэш и не ключ) и
трактуется как «не совпало» **только для этого запроса** — специально не кэшируется, чтобы
следующий запрос сразу же повторно попытался обратиться к хранилищу, а не залип в отказе (или,
что хуже, в пропуске) на весь TTL из-за временного сбоя.

Отзыв ограничен TTL: отзыв меняет только строку в `api_keys`, кэш в процессе не инвалидируется
(межпроцессного канала инвалидации нет, и спека прямо принимает этот компромисс). Ключ,
отозванный в середине окна, продолжает проходить проверку из кэша до истечения TTL этой записи.
`test_revocation_takes_effect_only_after_the_cache_ttl` проверяет ровно эту границу через
подставной `now_fn`. Это задокументировано и в `service/README.md`.

`last_used_at` обновляется через `background.add_task(_touch_last_used_safe, key_repo, key_id)`
внутри самой зависимости `require_token` (FastAPI прокидывает единый `BackgroundTasks` в любую
зависимость, которая его запрашивает) — планируется только при успешном совпадении по ключу, то
есть никогда не выполняется на пути со статическим токеном и никогда не блокирует ответ.
`_touch_last_used_safe` гасит и логирует любое исключение — та же дисциплина «пишем после ответа,
ошибку глушим», что уже действует для `persist()` в `agentgate/api/app.py`.

## Файлы

Изменены (все в `service/`): `store/models.py`, `api/deps.py`, `api/app.py`, `config.py`,
`__main__.py`, `README.md`, `tests/test_api.py`.
Новые: `migrations/versions/0002_api_keys.py`, `store/keys.py`, `cli.py`, `tests/test_keys.py`,
`tests/test_deps_keys.py`, `tests/test_cli_keys.py`.

## Замечания

1. `store/keys.py` — не строго red-first (см. TDD выше), отмечено явно.
2. Кэш в `_KeyVerifier` не ограничен по размеру и живёт per-process: неизвестный bearer тоже
   получает отрицательную запись в кэше; на реальных объёмах ключей и трафика это не проблема, но
   стоит иметь в виду. При нескольких воркерах `uvicorn` отзыв-в-пределах-TTL — per-worker, а не
   глобальный: каждый воркер узнаёт об отзыве независимо, в пределах своего TTL.
3. Миграция `0002` применена к обеим базам (`agentgate` и `agentgate_test`) — задание требовало
   доказать применение «к живому Postgres (5433)»; обе базы на этом порту реально «живые»
   (одна — за самим сервисом, вторая — за `AGENTGATE_TEST_DB_URL`-тестами), оставлять одну на
   `0001` смысла не было.
4. Строгая постановка спеки (non-localhost игнорирует `AGENTGATE_TOKEN`) сознательно отложена —
   по прямому указанию, не по ошибке.
5. В корне репозитория во время сессии появился неотслеживаемый `contracts/__pycache__/` —
   побочный эффект прогона существующего `tests/test_contracts.py`, который импортирует из
   `contracts/`. Он вне `service/`, поэтому не тронут и не закоммичен.

## `git status --short` (область `service/`)

```
 M service/README.md
 M service/agentgate/__main__.py
 M service/agentgate/api/app.py
 M service/agentgate/api/deps.py
 M service/agentgate/config.py
 M service/agentgate/store/models.py
 M service/tests/test_api.py
?? service/agentgate/cli.py
?? service/agentgate/store/keys.py
?? service/migrations/versions/0002_api_keys.py
?? service/tests/test_cli_keys.py
?? service/tests/test_deps_keys.py
?? service/tests/test_keys.py
```
