# Задача 11: HTTP API

## Что построено

- `service/agentgate/api/deps.py` — `make_require_token(settings)`, единственная точка проверки
  Bearer-токена, от которой зависят все защищённые роуты. Токен не задан → пропускаем всех (это
  безопасно, потому что `validate_token_for_bind()` уже не даёт сервису стартовать на не-localhost
  bind без токена). Токен задан → требуем `Authorization: Bearer <token>`, сравнение через
  `secrets.compare_digest`, никогда через `==` — по дисциплине из
  `docs/superpowers/service/specs/api-keys.md` («Проверка на горячем пути»).
- `service/agentgate/api/app.py` — `create_app(settings, gate, decision_repo, session_repo,
  profiles, jsonl, db_probe=None) -> FastAPI` с четырьмя роутами:
  - `POST /v1/decide` — тело разбирается и валидируется вручную (`request.json()`, затем
    `DecideRequest.model_validate`), а не через типизированный параметр — так автоматическая
    валидация FastAPI (`RequestValidationError` → 422) вообще не срабатывает. Невалидное тело
    (не-JSON или не прошедшее `DecideRequest`) → 200, `ask`, stage 0, `rule_id
    "api.invalid-request"`, `reason` — текст первой ошибки валидации. Вызов `gate.decide(req)`
    обёрнут в `try/except Exception` — любое исключение из него превращается в 200, `ask`,
    `rule_id "api.internal-error"`. При успехе персист планируется через
    `background.add_task(persist, rec, state)`, то есть выполняется уже после отправки ответа.
  - `GET /v1/decisions` — `limit` ограничен `Query(ge=1, le=500, default=100)`, передаётся в
    `DecisionRepo.list` (который дополнительно сам зажимает в `[1, 1000]`); ответ —
    `{"items": [...], "next_before": str | null}`, курсорная пагинация по `decision_id`.
  - `GET /v1/profiles/{id}` — `profile.public_dict()` либо 404.
  - `GET /healthz` — без зависимости аутентификации; вызывает `db_probe()`, если он передан, и
    отдаёт `{"status": "ok"|"degraded", "db": bool, "llm": null}`.
  - `persist(rec, state)` — сначала пишет строку в JSONL, затем в одном `try/except` делает upsert
    сессии **до** вставки решения (FK-порядок: `decisions.session_id → sessions.id`), затем — при
    `decision == "allow" and not cached` — пишет строку allow-кэша. Любое исключение здесь
    логируется и гасится: клиент уже получил ответ, персист не должен на него влиять.
- `service/agentgate/__main__.py` — `build_app(settings=None)` делает всю сборку (читает
  `Settings`, вызывает `validate_token_for_bind()`, грузит профили, создаёт engine и репозитории,
  восстанавливает состояние сессий и allow-кэш в `InMemorySessionStateStore` из Postgres, собирает
  `Gate` без собственного `persist` — эту роль берёт на себя `persist` из `create_app` — и вызывает
  `create_app`). `main()` — тонкая обёртка: `asyncio.run(build_app())`, затем `uvicorn.run(...)`.

## Отступление от буквального кода брифа

Шаг 3 брифа пишет в JSONL как `jsonl.write(rec.to_dict())`. Поле `DecisionRecord.to_dict()`
называется `"id"` (имя поля датакласса), а не `"decision_id"`. Но собственный тест брифа
(`test_decide_allow_and_persist`) проверяет
`json.loads(lines[0])["decision_id"] == data["decision_id"]` — при буквальной реализации эта
проверка падает. Я изменил `persist`, чтобы писать `dict(rec.to_dict(), decision_id=rec.id)`,
приведя JSONL-строку к тому же соглашению, что уже используется в `/v1/decisions`
(`dict(r.to_dict(), decision_id=r.id)`). Подтверждено RED-прогоном ниже.

## Доказательства TDD

1. `service/tests/test_api.py` написан до `app.py`/`deps.py` — тесты брифа плюс исходы
   deny/ask, тест `api.internal-error` через подменный `Gate`, который бросает исключение,
   граничные случаи аутентификации и пара тестов формы ответа.
2. RED: `uv run pytest tests/test_api.py -v` → `ModuleNotFoundError: No module named
   'agentgate.api.app'` (0 собрано, 1 ошибка) — настоящий провал.
3. Реализованы `deps.py` и `app.py`.
4. GREEN: `uv run pytest tests/test_api.py -v` → 14 passed.
5. `service/tests/test_main.py` (на реальной БД, за существующим guard'ом `requires_db`) написан
   *после* того, как `__main__.py` уже существовал (шаг 4 брифа не содержит собственного RED-шага,
   поэтому `__main__.py` был написан в том же проходе, что `app.py`/`deps.py`). Чтобы всё равно
   получить настоящий RED именно для `test_main.py` (правило `service/CLAUDE.md`: «тест, который не
   падал до реализации, не считается тестом»), файл `agentgate/__main__.py` был временно убран, тест
   перезапущен: `ImportError: cannot import name '__main__' from 'agentgate'` — настоящий провал —
   после чего файл восстановлен и тест перезапущен: 3 passed.
6. Полный набор без БД: `uv run pytest -q -W error` → `405 passed, 22 skipped` (391+14 passed,
   19+3 skipped относительно базовой линии — арифметика сходится).
7. Полный набор с `AGENTGATE_TEST_DB_URL`: `uv run pytest -q -W error` → `427 passed`
   (410 базовых + 14 + 3). Чисто, ни одного предупреждения, превращённого в ошибку.

## Как устроен шов аутентификации для будущей замены на API-ключи

`make_require_token(settings)` — единственное место, где какой-либо роут трогает заголовок
`Authorization`; все роуты используют один и тот же экземпляр
`Depends(make_require_token(settings))` (`auth`, построенный один раз в `create_app`, подключённый
через `dependencies=[auth]`). Замена статического токена на схему из
`docs/superpowers/service/specs/api-keys.md` — это переписывание тела `require_token` (хэшировать
bearer, найти по хэшу, проверить `revoked_at`/`expires_at`, закэшировать ненадолго в памяти
процесса) без изменения роутов. `secrets.compare_digest` уже используется для сравнения
статического токена — та же дисциплина, что заложена в дизайн API-ключей для сравнения хэшей.

## Как реализована граница fail-closed на 200

Два явных пути, оба покрыты тестами:
- Невалидное тело никогда не попадает в автоматическую валидацию FastAPI: роут принимает сырой
  `Request`, а не параметр типа `DecideRequest`, поэтому `RequestValidationError`/422 в принципе не
  может быть поднят фреймворком на этом пути. Ошибки `request.json()` (`ValueError`) и
  `DecideRequest.model_validate` (`pydantic.ValidationError`) перехватываются явно и превращаются в
  `_ask("api.invalid-request", ...)`.
- Любое исключение из `gate.decide(req)` — баг в `normalize`, синхронный сбой хранилища или что-то
  ещё, от чего сам `Gate.decide` не защищён (см. докстринг `agentgate/pipeline.py`: у него нет
  собственного верхнеуровневого `try/except`) — перехватывается `try/except Exception` вокруг
  вызова и превращается в `_ask("api.internal-error", ...)`. Проверено подменным `Gate`, чей
  `decide` бросает `RuntimeError` (`test_decide_raises_is_ask_200_internal_error`): 200, `ask`,
  `rule_id == "api.internal-error"`, ничего не персистится (конвейер не успел создать
  `DecisionRecord`).

Ни один из путей не может привести к `allow`.

## Что покрыто тестами, а что — только реальным запуском, в `__main__`

Покрыто тестами (через `build_app`, на реальной БД, `tests/test_main.py`):
- `Settings` → `validate_token_for_bind()` (падает для не-localhost bind без токена).
- `load_profiles` и проверка наличия дефолтного профиля (`SystemExit`, если профиля нет).
- Создание engine/session-factory/репозиториев против настоящего Postgres.
- Восстановление состояния сессии: посеяна `SessionRow` через `SessionRepo.upsert`,
  `InMemorySessionStateStore` подменён перехватывающей заглушкой, проверено, что `store.preload(...)`
  вызван с корректно восстановленным `SessionState` (`deny_total`, `decisions_total` совпадают).
- Восстановление allow-кэша: посеяны `DecisionRow` и запись в `AllowCacheRow` через
  `SessionRepo.cache_put`, проверено, что `store.cache_put(session_id, action_hash, decision_id,
  ttl_seconds)` вызван с верными значениями и TTL, согласованным с `expires_at - now`.
- `/healthz` собранного `app` отдаёт `db: true` против настоящей базы (значит, `db_probe` подключён
  правильно).

Только реальным запуском, ничем не протестировано: тело `main()` — `logging.basicConfig`, сам вызов
`asyncio.run` и `uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)`. `uvicorn.run`
блокирует вызывающий поток собственным event loop'ом и не может быть запущен из юнит-теста — именно
поэтому `build_app()` вынесен в отдельную тестируемую функцию. Полный ручной прогон из шага 6
брифа (`docker compose` + `alembic upgrade head` + `uv run python -m agentgate` + `curl`) не
выполнялся: в этом воркtree ещё нет каталога `alembic/versions/` (миграции не настроены), так что
этот шаг в буквальном виде сейчас не запускается. DB-тесты выше уже проходят через тот же код
(`build_app`) на той же живой Postgres (`localhost:5433`), только через `Base.metadata.create_all`
(фикстура `session_factory`), а не `alembic upgrade head`; настоящий запуск `python -m agentgate`
дополнительно прогнал бы сам `uvicorn.run`, `logging.basicConfig` и чтение `Settings` из реальных
переменных окружения — ни одна из этих строк не выглядит существенно более рискованной, чем то, что
уже покрыто.

## Изменённые файлы

- `service/agentgate/api/deps.py` (новый)
- `service/agentgate/api/app.py` (новый)
- `service/agentgate/__main__.py` (новый)
- `service/tests/test_api.py` (новый)
- `service/tests/test_main.py` (новый)

## Замечания для ревью

- TTL allow-кэша в `persist` (`_CACHE_TTL_SECONDS = 86400`) — константа, совпадающая со значением
  по умолчанию `cache_ttl_seconds` в `Gate.__init__`, так как отдельного поля в `Settings` для этого
  нет, а `build_app` создаёт `Gate` с дефолтом. Если в будущей задаче это станет настраиваемым на
  уровне `Gate`, `persist` в `create_app` нужно будет прокинуть то же значение — сейчас у
  `create_app` нет способа узнать, с каким TTL был собран переданный `Gate` (скрытая связанность, не
  баг сегодня).
- `GET /v1/decisions` с `limit` вне `[1, 500]` отдаёт собственный 422 FastAPI (через
  `Query(ge=1, le=500)`), а не 200. Контракт «всегда 200» брифа относится только к
  `POST /v1/decide`, так что это осознанно, но фиксирую как единственный не-всегда-200 эндпоинт в
  модуле.
- `git status --short` в воркtree показывает ровно пять новых файлов, перечисленных выше — больше
  ничего не тронуто.

## `git status --short`

```
?? service/agentgate/__main__.py
?? service/agentgate/api/app.py
?? service/agentgate/api/deps.py
?? service/tests/test_api.py
?? service/tests/test_main.py
```
