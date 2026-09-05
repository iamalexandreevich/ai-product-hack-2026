# service — ядро AgentGate

FastAPI-сервис: `POST /v1/decide`, `POST /v1/inspect`, `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz`. Конвейер решения: нормализация по AST → ступень 1 (hard-deny, правила пользователя из `rules`, профиль, allowlist) → ступень 2 (LLM через OpenAI-совместимый API) → эскалация → ответ; решения в Postgres и JSONL.

С v3 запрос `decide` может нести `rules` — детерминированные `allow`/`ask`/`deny` пользователя, которые ступень 1 применяет на трёх позициях цепочки, — и `call_id`. `POST /v1/inspect` судит результат инструмента до того, как его увидит модель: детекторы → маска или `drop`, при флаге — классификатор ступени 2. Fail-closed там — `drop`. Контракт обоих — `contracts/README.md`, раздел «v3».

Спека: `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`. План реализации: `docs/superpowers/service/plans/2026-09-03-agentgate-v1.md`. Карта модулей — `service/CLAUDE.md`.

## Как это устроено

Швы, за каждым — протокол, и подстановка своей реализации не требует правок выше по стеку:

| Шов | Протокол | Реализации в проде | Где собирается |
|---|---|---|---|
| Правило ступени 1 | `Rule` (`agentgate/rules/base.py`) | 12 правил в `agentgate/rules/` | `STAGE1` в `agentgate/rules/chain.py` |
| Ступень 2 | `Classifier` (`agentgate/classify/base.py`) | `LLMClassifier` | `bootstrap.build_service` |
| Состояние сессии | `SessionStateStore` (`agentgate/domain/session.py`) | `InMemorySessionStateStore` внутри `PersistentSessionStateStore` | `bootstrap.build_service` |
| Повтор по `Idempotency-Key` | `ReplayStore` (`agentgate/domain/replay.py`) | `InMemoryReplayStore` внутри `PersistentReplayStore` | `bootstrap.build_service` |
| Запись решения | `DecisionWriter` (`agentgate/store/writer.py`) | `Jsonl…` + `Postgres…` внутри `Composite…` | `bootstrap.build_service` |
| Ступень 2 inspect | `InspectClassifier` (`agentgate/inspect/classify.py`) | `LLMInspectClassifier` | `bootstrap.build_service` |
| Кэш вердиктов inspect | `InspectCache` (`agentgate/domain/inspect_cache.py`) | `InMemoryInspectCache` | `bootstrap.build_service` |

Всё, что каскад возвращает, — один тип `Verdict` (`agentgate/domain/verdict.py`): и правило, и классификатор, и allow-кэш, и ранний отказ API.

## Как добавить

### …правило ступени 1

Новый класс и одна строка в списке. `Gate` не меняется, тесты соседних правил не трогаются.

```python
# agentgate/rules/kubectl_delete.py
class KubectlDeleteRule:
    id = "hard-deny.kubectl-delete"
    hard = True

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        if any(c.argv[:2] == ["kubectl", "delete"] for c in action.commands):
            return Verdict.deny(self.id, "kubectl delete against a live cluster", hard=True)
        return None
```

Импорты: `Verdict` из `agentgate.domain.verdict`, `Policy` из `agentgate.domain.policy`, `NormalizedAction` из `agentgate.normalize.model`. Подключение — строка в `STAGE1` (`agentgate/rules/chain.py`); порядок списка и есть приоритет: `None` означает «моё правило тут ни при чём», и решает следующее. Hard-deny вместо этого добавляется в `HARD_DENY_RULES` (`agentgate/rules/hard_deny/__init__.py`) — до правил профиля и allowlist. Дописывать в конец списка безопасно: все его правила жёсткие, и ни одно не может перехватить ваше. Единственное правило, отвечающее `ask`, вынесено из списка в цепочку именно для этого.

### …модель ступени 2

OpenAI-совместимый провайдер — это запись в `models.configs` профиля и **ни строки кода** (см. «Профили» ниже): `build_classifiers` поднимает по `LLMClassifier` на каждую запись при старте. Провайдер с другим протоколом — класс с `name` и `classify`:

```python
# agentgate/classify/local_guard.py
class LocalGuardClassifier:
    name = "local-guard"

    async def classify(self, action, user_request, policy, stage1_note) -> Verdict:
        kind = await self._ask_local_model(action, policy)   # ваш транспорт -> DecisionKind
        return Verdict(decision=kind, stage=2, model=self.name)
```

Подключение — одна строка в `bootstrap.build_service` после сборки реестра:

```python
classifiers["default"]["local-guard"] = LocalGuardClassifier()
```

Ключ реестра — то, что харнесс присылает в поле `model` запроса (или `models.default` профиля). `classify` не имеет права бросать: любой сбой возвращается как `ask` с заполненным `error`, иначе fail-closed держится только внешним обработчиком API.

### …хранилище состояния сессий

Пять методов и одна строка. `PersistentSessionStateStore` оборачивает любой из них и добавляет восстановление из Postgres при старте.

```python
# agentgate/session/redis.py
class RedisSessionStateStore:
    async def get_or_create(self, session_id, harness, profile_id, workspace) -> SessionState: ...
    async def save(self, state: SessionState) -> None: ...
    async def cache_get(self, session_id: str, key: str) -> str | None: ...
    async def cache_put(self, session_id: str, key: str, decision_id: str, ttl_seconds: int) -> None: ...
    def preload(self, states: list[SessionState]) -> None: ...
```

Подключение — одна строка в `bootstrap.build_service`:

```python
store = state_store or PersistentSessionStateStore(RedisSessionStateStore(...), sessions)
```

Писать в Postgres из этих методов нельзя: строка allow-кэша ссылается на строку решения, которой на момент решения ещё нет (FK), а запись в базу на горячем пути оплачивается каждым вызовом агента. Всю персистентность решения делает `PostgresDecisionWriter` после отправки ответа.

### …хранилище повторов

Два метода. `PersistentReplayStore` оборачивает любую реализацию и добавляет восстановление из Postgres при старте.

```python
from agentgate.domain.replay import Replay


class RedisReplayStore:
    async def get(self, key: str) -> Replay | None: ...
    async def put(self, key: str, replay: Replay, ttl_seconds: int) -> None: ...
```

```python
replay = replay_store or PersistentReplayStore(RedisReplayStore(...), decisions, ttl)
```

### …детектор inspect

Строка в таблице. `Inspector` не меняется: он получает кортеж детекторов и просто перебирает их по строкам.

```python
# agentgate/inspect/detectors.py
BASE64_URL = Detector(
    id="inspect.data-url",
    patterns=(re.compile(r"data:[a-z/+.-]+;base64,[A-Za-z0-9+/=]{200,}"),),
    action=Action.mask,
    hints=("data:",),
)
```

```python
# agentgate/inspect/chain.py
INSPECT_STAGE1 = (INJECTION, PIPE_EXEC, ENCODED, BASE64_URL, INVISIBLE)
```

`hints` и `precheck` — дешёвые предпроверки перед регулярками; инвариант: они могут отсеять только строку, которую шаблоны заведомо не совпадут, никогда наоборот. Без них бюджет 20 мс на 256 КБ не держится.

### …классификатор inspect

```python
from agentgate.inspect.classify import InspectCase, InspectOutcome


class LocalInspectClassifier:
    async def classify(self, case: InspectCase) -> InspectOutcome: ...
```

`classify` не бросает: любой сбой возвращается как `InspectOutcome` с `error`, и `Inspector` отвечает вердиктом ступени 1. Подключение — строка в `bootstrap.build_service`.

### …кэш вердиктов inspect

```python
class RedisInspectCache:
    async def get(self, key: str) -> Inspection | None: ...
    async def put(self, key: str, value: Inspection, ttl_seconds: int) -> None: ...
```

Ключ строит `inspect_cache_key` (`agentgate/session/cache_key.py`) — там же, где ключ allow-кэша.

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

С базой (дополнительно прогоняет `tests/store/test_repo.py`, `tests/store/test_keys.py`, `tests/test_bootstrap.py`, `tests/test_cli_keys.py`, часть `tests/api/test_app.py` и `tests/e2e/`; база и так поднята для разработки, см. раздел ниже про `agentgate_test`):

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

`docker-compose.yml` поднимает Postgres 16 на `5433` и создаёт основную базу `agentgate` через `POSTGRES_DB`. Вторая база, `agentgate_test`, на которую указывает `AGENTGATE_TEST_DB_URL` (`postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test`) для тестов хранилища (`tests/store/`, `tests/e2e/`), создаётся автоматически скриптом `scripts/init-test-db.sql`, примонтированным в `/docker-entrypoint-initdb.d/` — Postgres выполняет такие скрипты один раз, при первой инициализации пустого каталога данных.

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
