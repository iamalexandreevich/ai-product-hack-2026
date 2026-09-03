# service — ядро AgentGate

FastAPI-сервис: `POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz`. Конвейер: нормализация по AST → ступень 1 (hard-deny, профиль, allowlist) → ступень 2 (LLM через OpenAI-совместимый API) → эскалация → ответ; решения в Postgres и JSONL.

Спека: `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`. План реализации: `docs/superpowers/service/plans/2026-09-03-agentgate-v1.md`.

## Запуск

Локально, без Docker (Postgres поднят отдельно, например через `docker compose up -d db` из этой же папки):

```
cd service
uv sync
AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate uv run alembic upgrade head
AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate uv run python -m agentgate
```

Через Docker Compose (поднимает и Postgres, и сервис; миграции применяются автоматически при старте контейнера `gate`, см. `CMD` в `Dockerfile`):

```
cd service
docker compose up -d --build
```

### Переменные окружения

| переменная | смысл |
|---|---|
| `AGENTGATE_DB_URL` | обязательна; строка подключения к Postgres, `postgresql+asyncpg://...` |
| `AGENTGATE_TOKEN` | статический bearer-токен; обязателен, если `AGENTGATE_BIND` не localhost |
| `AGENTGATE_BIND` | `host:port`, по умолчанию `127.0.0.1:8400`; IPv6-хост в скобках: `[::1]:8400` |
| `AGENTGATE_PROFILES_DIR` | каталог с YAML-профилями, по умолчанию `profiles` |
| `AGENTGATE_LOG_PATH` | путь к JSONL-логу решений, по умолчанию `logs/decisions.jsonl` |
| `AGENTGATE_DEFAULT_PROFILE` | id профиля по умолчанию, по умолчанию `default` |
| `AGENTGATE_API_KEY_CACHE_TTL_SECONDS` | TTL кэша проверки API-ключей, по умолчанию `45` |
| ключи провайдеров LLM | имя переменной задаётся в профиле, поле `models.configs.<name>.api_key_env` (например `OPENROUTER_API_KEY`) |

## Тесты

Без базы (юнит-тесты и всё, что не требует Postgres):

```
cd service && uv run pytest -q
```

С базой (дополнительно прогоняет `tests/test_store.py`, часть `tests/test_main.py`, `tests/test_keys.py`, `tests/test_cli_keys.py`, `tests/e2e/`; база и так поднята для разработки, см. раздел ниже про `agentgate_test`):

```
cd service
AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q
```

## Профили

Один активный YAML-профиль на сервисе, каталог задаётся `AGENTGATE_PROFILES_DIR` (по умолчанию `profiles/`; в репозитории — `profiles/default-dev.yaml`, `id: default`). Профиль описывает allowed/protected paths, protected branches, сетевой allowlist, safe-префиксы команд, эскалацию по deny-окну, prose-слоты для промпта ступени 2 и таблицу моделей `models`.

Добавить модель — новая запись в `models.configs` со своим ключом (именем модели) и как минимум тремя полями:

```yaml
models:
  default: sonnet
  configs:
    sonnet:
      base_url: "https://openrouter.ai/api/v1"
      model: "anthropic/claude-sonnet-4-6"
      api_key_env: OPENROUTER_API_KEY
      timeout_ms: 3000
      structured_output: true
```

`base_url` — endpoint OpenAI-совместимого API; `model` — имя модели, как его ожидает этот endpoint; `api_key_env` — имя переменной окружения, откуда берётся ключ (или `null`, если endpoint без аутентификации, например локальный). `timeout_ms` и `structured_output` — таймаут ступени 2 и требование structured output для конкретного провайдера.

Переключить модель на конкретный запрос — поле `model` в теле `POST /v1/decide` (`DecideRequest.model`), значение должно совпадать с ключом в `models.configs`; иначе — `ask` с `rule_id: api.unknown-model`. Без поля используется `models.default`.

Ступень 2 в шаблонном профиле (`profiles/default-dev.yaml`) по умолчанию использует Gemini через OpenRouter (`models.default: gemini`). Строковые значения в YAML-профиле поддерживают подстановку `${VAR}` / `${VAR:-default}` из окружения процесса (загрузчик, `agentgate/profiles/loader.py`) — так, слаг модели задан как `${OPENROUTER_MODEL_NAME:-google/gemini-3.8-flash}`: без переменной `OPENROUTER_MODEL_NAME` берётся `google/gemini-3.8-flash`, а с ней — оператор переопределяет слаг без правки YAML. Ключ провайдера — по-прежнему `OPENROUTER_API_KEY` (см. таблицу переменных выше).

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
