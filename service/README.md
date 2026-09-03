# service — ядро AgentGate

FastAPI-сервис: `POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz`. Конвейер: нормализация по AST → ступень 1 (hard-deny, профиль, allowlist) → ступень 2 (LLM через OpenAI-совместимый API) → эскалация → ответ; решения в Postgres и JSONL.

Спека: `docs/superpowers/specs/2026-09-03-agentgate-v1-design.md`. План реализации: `docs/superpowers/plans/2026-09-03-agentgate-v1.md`.

Запуск (после реализации): `docker compose up` из этой папки; переменные окружения `AGENTGATE_DB_URL`, `AGENTGATE_TOKEN`, `AGENTGATE_BIND`, `AGENTGATE_PROFILES_DIR`, `AGENTGATE_LOG_PATH` и ключи провайдеров по именам из профилей.

## База для тестов хранилища

`docker-compose.yml` поднимает Postgres 16 на `5433` и создаёт основную базу `agentgate` через `POSTGRES_DB`. Вторая база, `agentgate_test`, на которую указывает `AGENTGATE_TEST_DB_URL` (`postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test`) для `tests/test_store.py`, создаётся автоматически скриптом `scripts/init-test-db.sql`, примонтированным в `/docker-entrypoint-initdb.d/` — Postgres выполняет такие скрипты один раз, при первой инициализации пустого каталога данных.

Из этого следует: если volume `pgdata` уже существовал до добавления скрипта (переиспользуется поднятый ранее контейнер), инициализация не перезапустится сама. В этом случае — либо `docker compose down -v && docker compose up -d db` (пересоздать том с нуля), либо создать базу вручную: `docker compose exec db psql -U agentgate -c "CREATE DATABASE agentgate_test;"`.

## API-ключи

Спека: `docs/superpowers/service/specs/api-keys.md`. Ключи выдаются только из CLI, эндпоинта для этого нет:

```
uv run python -m agentgate keys create --label "kilo-ci" [--expires 90d]
uv run python -m agentgate keys list
uv run python -m agentgate keys revoke <key_id>
```

`create` печатает ключ (`agk_...`) в stdout один раз — он не сохраняется нигде, кроме как в памяти вызвавшего; в базе лежит только его SHA-256. Проверка на горячем пути (`agentgate/api/deps.py`) кэширует результат в памяти процесса на `AGENTGATE_API_KEY_CACHE_TTL_SECONDS` секунд (по умолчанию 45) — отозванный ключ перестаёт приниматься не мгновенно, а в пределах этого окна.

Аддитивно поверх `AGENTGATE_TOKEN`: запрос проходит, если bearer совпадает со статическим токеном **или** с действующим выданным ключом. `AGENTGATE_TOKEN` для non-localhost bind по-прежнему обязателен (`validate_token_for_bind()` не менялся) — более строгий вариант спеки, где non-localhost принимает только ключи, в v1 сознательно не реализован.
