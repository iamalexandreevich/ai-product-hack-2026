# AgentGate v1 — дизайн сервиса

Дата: 3 сентября 2026. Статус: согласовано в brainstorming, ждёт ревью перед планом реализации.

Этот документ описывает v1 сервиса AgentGate для хакатона AI Product Hack 2026 (кейс «auto mode»). Он уточняет и в части решений заменяет `docs/base.md` и `docs/artifacts/agent-gate-design.md`. Там, где они расходятся, прав этот документ.

---

## 1. Что изменилось относительно base.md

| Было в base.md | Стало в v1 |
|---|---|
| Ступень 2 — своя дообученная модель Qwen3.5 | Готовые LLM по API, модель сменная; обучение на H200 — только если останется время |
| Kilo-адаптер и PR в Kilo первым делом | Универсальный сервис с одним контрактом; интеграции под харнессы делает отдельное направление |
| Решение с историей, taint через `/v1/observe` | v1 оценивает одно действие плюс последний запрос пользователя; история, PostToolUse, provenance — roadmap |
| Deny-and-continue как формат текста | `deny` с обязательными `reason` и `suggest`; отдельного значения в enum нет |
| Модуль пакетов внутри ступени 1 | Слот в цепочке ступени 1, реализация — отдельный план |
| Бенчмарк — наша работа | Бенчмарк — отдельное направление; мы отдаём ему `/v1/decide`, `/v1/decisions` и JSONL-лог |
| SQLite локально / Redis на сервере | Postgres всегда; счётчики и кэш в памяти процесса с записью в Postgres |

Цель сервиса: минимизировать human in the loop при контролируемом ASR. Главная метрика — Friction (число `ask` на легитимную задачу) при ASR не выше облачного zero-shot.

## 2. Границы v1

Делаем:
- `POST /v1/decide` для PreToolUse: одно действие + последний запрос пользователя → `allow | deny | ask`.
- Ступень 1 (детерминированная): нормализация по AST, hard-deny, capability-профиль, safe-allowlist, слот под модуль пакетов.
- Ступень 2 (LLM по OpenAI-совместимому API, structured output), сменная модель.
- Сессии, счётчики эскалации, кэш `allow`, база решений в Postgres, JSONL-лог.
- Профили политики в YAML на стороне сервиса.
- Минимальный тестовый клиент в формате хука.

Не делаем в v1 (roadmap):
- `/v1/observe`, PostToolUse, taint/provenance, история действий в промпте.
- Модуль пакетов (slopsquatting).
- Ступень 3 (облачная CoT на `U`).
- Override, запись правил через API, Test → Protect, панель.
- Плагины харнессов (OpenCode, Claude Code, Codex, Kilo) — направление `adapters/`.
- Обучение моделей.

Харнессы о профилях и внутренностях сервиса не знают. Адаптер знает только URL, токен и (опционально) `profile_id`.

## 3. Структура репозитория

```
ai-product-hack-2026/
  CLAUDE.md          # правила репозитория, ссылки на спеки
  docs/              # спеки, дизайн, обзор рынка, артефакты
  contracts/         # OpenAPI + JSON-схемы запроса/ответа; hook_client.py как эталонный клиент
  service/           # наш сервис (ядро AgentGate)
  adapters/          # направление 1: перехват сессий, плагины харнессов (по подпапке на харнесс)
  benchmark/         # направление 3: внутренний и внешний бенчмарк
```

Правила:
- Каждая папка верхнего уровня — свой `README.md`, свои зависимости (`pyproject.toml` / `package.json`), свои тесты. Команды пишут только в своих папках.
- `contracts/` меняется только PR-ом с упоминанием всех трёх направлений. Схемы в `contracts/` генерируются из pydantic-моделей сервиса и коммитятся; тест в `service/` проверяет, что сгенерированное совпадает с закоммиченным.

Структура `service/`:

```
service/
  agentgate/
    api/         # роуты /v1/*, pydantic-схемы запроса/ответа
    normalize/   # bashlex AST, пути, домены → NormalizedAction
    stage1/      # hard_deny.py, profile_check.py, allowlist.py, packages.py (заглушка), chain.py
    stage2/      # OpenAI-совместимый клиент, промпт, structured output, fallback-парсер
    profiles/    # загрузка и валидация YAML
    session/     # счётчики эскалации, кэш allow, интерфейс хранилища счётчиков
    store/       # SQLAlchemy-модели, Alembic-миграции
    log/         # JSONL-лог решений
  profiles/default-dev.yaml
  tests/
  docker-compose.yml    # сервис + Postgres
  pyproject.toml
```

Стек: Python 3.12, FastAPI, pydantic v2, SQLAlchemy 2 (async) + asyncpg, Alembic, bashlex, httpx, uv. Код и комментарии — английский; документация — русский; идентификаторы API не переводятся.

## 4. API

### 4.1. Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/v1/decide` | Основной вызов из PreToolUse |
| `GET` | `/v1/decisions?session_id=&model=&limit=&before=` | Лента решений для панели и бенчмарка; курсорная пагинация по `decision_id` |
| `GET` | `/v1/profiles/{id}` | Прочитать профиль (как загружен, без секретов) |
| `GET` | `/healthz` | Живость; включает статус БД и активного LLM-endpoint |

Аутентификация: bearer из `AGENTGATE_TOKEN`. При биндинге не на localhost токен обязателен, сервис без него не стартует. Сессия создаётся лениво при первом `decide`.

### 4.2. Запрос `POST /v1/decide`

```json
{
  "session_id": "opencode-3f2a…",
  "harness": "opencode",
  "tool": "shell",
  "raw": "npm install lodahs && rm -rf ./dist",
  "args": {"cwd": "/home/u/repo", "paths": [], "domains": []},
  "user_request": "почини сборку",
  "profile_id": "default-dev",
  "model": "qwen-4b",
  "metadata": {"run_id": "bench-17", "plugin_version": "0.1.0"}
}
```

| Поле | Тип | Обязательное | Смысл |
|---|---|---|---|
| `session_id` | string ≤ 128 | нет | Без него счётчики и кэш не ведутся, решение пишется с `session_id = null` |
| `harness` | string ≤ 64 | да | Свободная строка: `opencode`, `claude-code`, `codex`, `kilo`, `bench`, … |
| `tool` | enum | да | `shell \| file_write \| file_read \| network \| mcp_call` |
| `raw` | string ≤ 32 КБ | да для `shell` | Сырая команда; для других инструментов — сырой payload в текстовом виде |
| `args.cwd` | string | да | Абсолютный путь рабочего каталога |
| `args.paths` | string[] | нет | Для `file_*`: затрагиваемые пути. Для `shell` игнорируется, сервис извлекает сам |
| `args.domains` | string[] | нет | Для `network`: домены. Для `shell` игнорируется |
| `args.mcp` | object | нет | Для `mcp_call`: `{server, tool, arguments}` |
| `user_request` | string | да | Последнее сообщение пользователя; сервер обрезает до 512 токенов с конца текста (сохраняем хвост) |
| `profile_id` | string | нет | По умолчанию `default` |
| `model` | string | нет | Имя конфигурации модели из профиля; переопределяет `models.default` |
| `metadata` | object ≤ 16 КБ | нет | Произвольный JSON. Хранится и возвращается, в решение и в промпт не попадает |

### 4.3. Ответ

```json
{
  "decision": "deny",
  "reason": "npm install lodahs: имя на расстоянии 1 от lodash; установка неизвестного пакета вне задачи",
  "suggest": "Установи lodash; сборку чисти через npm run clean",
  "stage": 2,
  "rule_id": null,
  "model": "qwen-4b",
  "latency_ms": {"stage1": 1, "stage2": 84, "total": 86},
  "cached": false,
  "decision_id": "01J…"
}
```

| Поле | Смысл |
|---|---|
| `decision` | `allow \| deny \| ask` |
| `reason` | Текст, который адаптер отдаёт агенту без правок. Для `allow` — пустая строка |
| `suggest` | Безопасная альтернатива. Пустая строка, если предложить нечего |
| `stage` | Кто решил: `1`, `2`. `0` — ответ из кэша или отказ на уровне API (невалидный запрос) |
| `rule_id` | Идентификатор правила ступени 1 (`hard-deny.exfil`, `profile.path`, `allowlist.readonly`, `escalation`, …) или `null` |
| `model` | Имя конфигурации модели, если ступень 2 вызывалась, иначе `null` |
| `latency_ms` | По ступеням и суммарно; отсутствующая ступень — `null` |
| `cached` | Ответ взят из кэша `allow` |
| `decision_id` | ULID, ключ записи в базе |

Текст для агента при `deny` собирает адаптер из шаблона: «Действие заблокировано политикой AgentGate (`rule_id`): `reason`. Не пытайся выполнить то же самое обходным путём. `suggest`». Шаблон лежит в `contracts/`.

### 4.4. Fail-closed на уровне API

Любой из случаев → `ask` с `reason`, объясняющей причину, и HTTP 200 (харнесс должен получить решение, а не ошибку):
- невалидное тело, неизвестный `tool`, превышены лимиты размеров;
- парсер не разобрал команду и ступень 2 недоступна;
- таймаут / ошибка / невалидный ответ LLM;
- внутренняя ошибка сервиса.

HTTP 401 только на отсутствие или неверный токен. `allow` по ошибке невозможен: тест на каждый путь отказа обязателен.

## 5. Конвейер решения

Каскад с коротким замыканием. Порядок фиксирован; первая ступень, вернувшая решение, завершает обработку.

```
запрос → валидация → кэш allow? → нормализация → ступень 1 → ступень 2 → эскалация → ответ → запись в БД и JSONL
```

### 5.1. Нормализация

1. `shell`: `raw` разбирается bashlex в AST. Извлекаются простые команды с argv, пайпы, редиректы, подстановки `$(…)` и обратные кавычки, `eval`/`exec`/`source`, присваивания переменных. Если парсер упал — флаг `unparseable=true`, и действие закрывается ступенью 1 правилом `unparseable`: `ask`, `stage: 1`, `model: null`. Классификатор не вызывается — он отвечал бы о команде, которую не видел: `commands`, `paths` и `domains` у такого действия пусты по построению.
2. Пути → абсолютные относительно `cwd`; раскрываются `~`, `..`, символические ссылки (без обращения к ФС, только лексически; `realpath` — roadmap).
3. Домены извлекаются из argv `curl`, `wget`, `git clone`, `pip install git+…`, `npm install <url>`, `ssh`, `scp`.
4. Для `file_*`, `network`, `mcp_call` структура берётся из `args` как есть.
5. Результат — `NormalizedAction`: `tool`, `commands: [{argv, redirects, stdin_from}]`, `pipes`, `paths`, `domains`, `flags: {unparseable, has_eval, has_subst, has_env_assign}`.

Решение по сырой строке запрещено везде.

### 5.2. Ступень 1 — детерминированная

Цепочка проверок; каждая — функция `(NormalizedAction, Profile, SessionState) -> Decision | None`. `None` означает «пропускаю дальше». Порядок:

1. **Hard-deny** (`rule_id: hard-deny.*`), не переопределяется ничем:
   - `exfil`: сетевая команда, где в аргументах, stdin или редиректе есть пути к секретам (`.env*`, `*.pem`, `id_rsa*`, `~/.ssh/**`, `~/.aws/**`, `~/.kube/**`, `*.key`, `*.p12`);
   - `destructive`: `rm -rf` / `rm -r` / `find -delete` / `shred` по пути вне `allowed_paths` или равному корню workspace;
   - `pipe-exec`: `curl|wget … | sh|bash|zsh|python|node` в одном пайпе, а также `sh -c "$(curl …)"`;
   - `protected-write`: запись (редирект, `tee`, `cp`/`mv` назначением, `sed -i`, `file_write`) в `protected_paths`;
   - `privilege`: `sudo`, `su`, `chmod 777`/`o+w` на каталоги, `chown` вне workspace, правки файрвола (`iptables`, `ufw`, `pfctl`);
   - `git-force`: `git push --force`/`-f` в ветки из `protected_branches`.
2. **Профиль** (`rule_id: profile.*`): пути вне `allowed_paths` → `deny`; домены вне `allowed_domains` → `deny` при `network.mode: allowlist|off`, `ask` при `ask`, пропуск при `open`.
3. **Safe-allowlist** (`rule_id: allowlist.*`) → `allow`: read-only команды (`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `find` без `-delete`/`-exec`, `git status|diff|log|show|branch`, `pwd`, `echo` без редиректов, `which`, `env` без аргументов); `file_read` внутри `allowed_paths`; `file_write` внутри `allowed_paths` вне `protected_paths`; префиксы из `safe_prefixes`. Составная команда (пайп, `&&`, `;`) проходит allowlist только если проходит каждая часть, и в ней нет `eval`, подстановок и редиректов в файлы.
4. **Пакеты** (`packages.py`): заглушка, всегда `None`. Интерфейс тот же, чтобы модуль slopsquatting встал сюда без изменений цепочки.
5. Всё остальное → ступень 2.

Правила из roadmap (Test → Protect, секция `rules:` профиля) встанут между 1 и 2 как элементы того же типа.

Бюджет: p50 ≤ 1 мс на нормализацию + ступень 1, измеряется в тестах.

### 5.3. Ступень 2 — LLM-классификатор

**Клиент.** OpenAI-совместимый `chat/completions` через httpx: `base_url`, `api_key` (из переменной окружения), `model`, `timeout_ms`, `structured_output: bool`. Один класс на всех провайдеров: OpenAI, OpenRouter (Claude, Gemini, открытые модели), vLLM, llama.cpp/Ollama локально. Инференс на H200 в сервис не входит; H200 — кластер для обучения.

**Вход**, reasoning-blind. Порядок частей промпта фиксирован ради кэширования префикса:

1. Системный промпт (стабильный): роль ревьюера действий кодинг-агента; описание схемы ответа; профиль в текстовой форме (`workspace`, `network`, `protected`), prose-слоты `environment` / `allow` / `soft_deny` как есть.
2. Пользовательское сообщение (переменное):

```
[TASK] почини сборку
[ACTION] tool=shell cwd=/home/u/repo
argv=[["npm","install","lodahs"],["rm","-rf","/home/u/repo/dist"]]
paths=[/home/u/repo/dist] domains=[]
[FLAGS] unparseable=false has_eval=false has_subst=false
[STAGE1] passed: no hard-deny match, not in allowlist
```

При появлении истории (roadmap) она вставляется между `[TASK]` и `[ACTION]` как дописываемый хвост, префикс остаётся общим. Ни рассуждений агента, ни выводов инструментов, ни `metadata` в промпт не попадает.

**Выход** через structured output, `response_format` по JSON-схеме:

```json
{"decision": "A|D|U", "risk": "exfiltration|destructive|privilege|supply_chain|injection|config|network|none", "reason": "одно предложение", "suggest": "одно предложение или пусто"}
```

Fallback для endpoint без `response_format`: та же схема в тексте промпта, ответ валидируется pydantic-моделью; невалидный JSON → `ask`. Выбор режима — поле `structured_output` конфигурации модели.

**Отображение**: `A` → `allow`; `D` → `deny` с `reason`/`suggest` из ответа; `U` → `ask` с `reason` из ответа. Ступень 3 в v1 не реализуется; `U` уходит в опциональный следующий обработчик (по умолчанию отсутствует), чтобы добавить её без изменений конвейера.

**Fail-closed**: таймаут, HTTP-ошибка, невалидный JSON, `decision` вне `{A,D,U}`, пустой ответ → `ask` с `reason` «классификатор недоступен: <кратко>», `stage: 2`, поле `error` в базе заполнено.

**Ретраи**: нет. Один вызов, один таймаут. Повторы увеличивают latency и маскируют нестабильность endpoint, которую бенчмарк должен видеть.

### 5.4. Эскалация и кэш

- Счётчики на сессию: `deny_consecutive`, окно последних 50 решений. Три `deny` подряд или 10 `deny` из последних 50 → текущее решение принудительно `ask`, `rule_id: escalation`, `reason` «агент упёрся в политику N раз, проверьте задачу». Любой `allow` сбрасывает `deny_consecutive`. Hard-deny на `ask` не заменяется: он остаётся `deny` и в счётчики входит.
- Кэш `allow` на сессию: ключ `sha256(profile_hash + normalized_action + user_request)`, TTL — время жизни сессии (в v1 — 24 часа с `last_seen_at`). `deny` и `ask` не кэшируются. Ответ из кэша помечен `cached: true`, `stage: 0`.
- Счётчики и кэш живут в памяти процесса за интерфейсом `SessionStateStore` и после каждого решения пишутся в Postgres; при старте восстанавливаются из `sessions` и `allow_cache`. Redis-реализация того же интерфейса — при масштабировании на несколько инстансов.

## 6. Профиль политики

Профиль — внутренняя конфигурация сервиса. Харнессы его не читают и о нём не знают; адаптер передаёт только `profile_id` (или ничего). Один YAML на профиль в `service/profiles/`, загружается при старте, валидируется pydantic-схемой; ошибка валидации роняет старт.

```yaml
id: default-dev
allowed_paths: ["${WORKSPACE}", "/tmp/agentgate-scratch"]
protected_paths: [".env*", ".git/hooks/**", ".opencode/**", ".kilo/**", ".claude/**", ".codex/**",
                  "AGENTS.md", "SKILL.md", ".cursorrules", "~/.ssh/**", "~/.aws/**", "~/.kube/**"]
protected_branches: ["main", "master", "release/*"]
network:
  mode: allowlist          # off | allowlist | ask | open
  allowed_domains: ["registry.npmjs.org", "pypi.org", "files.pythonhosted.org", "github.com", "api.github.com"]
safe_prefixes:
  - ["npm", "test"]
  - ["npm", "run", "lint"]
  - ["pytest"]
  - ["cargo", "test"]
models:
  default: sonnet
  configs:
    sonnet:
      base_url: "https://openrouter.ai/api/v1"
      model: "anthropic/claude-sonnet-4-6"
      api_key_env: OPENROUTER_API_KEY
      timeout_ms: 3000
      structured_output: true
    qwen-4b:
      base_url: "http://localhost:8000/v1"
      model: "qwen3.5-4b"
      api_key_env: null
      timeout_ms: 1500
      structured_output: true
escalation:
  deny_consecutive: 3
  deny_window: {count: 10, of_last: 50}
prose:
  environment: "Монорепозиторий на TypeScript, CI через GitHub Actions"
  allow: "Разрешены любые команды pnpm внутри workspace"
  soft_deny: "Не трогать каталог infra/ без явного запроса"
rules: []                  # roadmap: Test → Protect, модуль пакетов
```

- `${WORKSPACE}` подставляется из `args.cwd` первого запроса сессии (лексически ближайший каталог с `.git`, иначе сам `cwd`); без `session_id` — из `cwd` текущего запроса.
- Ключи API только через переменные окружения; в YAML — имя переменной.
- `profile_hash` = sha256 нормализованного содержимого профиля; пишется в каждое решение, чтобы бенчмарк различал политики.
- `GET /v1/profiles/{id}` отдаёт профиль без значений секретов (имена переменных остаются).

## 7. Хранение

**Postgres** (asyncpg, SQLAlchemy 2 async, Alembic с первого дня). Поднимается в `docker-compose.yml` рядом с сервисом; строка подключения `AGENTGATE_DB_URL`. SQLite не поддерживается.

| Таблица | Поля | Назначение |
|---|---|---|
| `sessions` | `id` PK, `harness`, `profile_id`, `workspace`, `created_at`, `last_seen_at`, `deny_consecutive`, `deny_total`, `decisions_total`, `recent_decisions` (JSONB, последние 50 исходов) | Одна строка на сессию, состояние счётчиков |
| `decisions` | `id` ULID PK, `session_id` FK nullable, `ts`, `harness`, `tool`, `raw`, `normalized` JSONB, `user_request`, `profile_id`, `profile_hash`, `decision`, `reason`, `suggest`, `stage`, `rule_id`, `model`, `model_raw_response` JSONB, `latency_stage1_ms`, `latency_stage2_ms`, `latency_total_ms`, `error`, `cached`, `metadata` JSONB | Каждое решение полностью; сырьё для бенчмарка и панели |
| `allow_cache` | `session_id` FK, `action_hash`, `decision_id` FK, `expires_at`; PK `(session_id, action_hash)` | Кэш `allow` |

Индексы: `decisions(session_id, ts)`, `decisions(model, ts)`, `decisions(decision, ts)`, `decisions(harness, ts)`, GIN на `decisions.metadata`.

Запись в базу выполняется после отправки ответа (background task FastAPI); latency ответа от базы не зависит. Ошибка записи логируется, ответ не меняется.

**JSONL-лог** `AGENTGATE_LOG_PATH` (по умолчанию `./logs/decisions.jsonl`): одна строка на решение, те же поля, что в `decisions`. Резерв для бенчмарка, если файл удобнее API.

## 8. Тестовый клиент

`contracts/hook_client.py`: читает JSON в формате хука (PreToolUse Claude Code / OpenCode `tool.execute.before`) из stdin, собирает запрос `decide`, вызывает сервис, печатает решение и код выхода (`0` allow, `2` deny, `3` ask). Используется для ручной проверки и e2e-тестов. Полноценные плагины харнессов — `adapters/`, не наша работа.

## 9. Тесты

Все через pytest, запускаются `uv run pytest` из `service/`:

- **Ступень 1, табличные**: команда → ожидаемое решение и `rule_id`. Обязательно обфускация: builtins (`command`, `builtin`), `$(…)`, `eval`, `base64 -d | sh`, подстановка через переменные (`X=rm; $X -rf /`), склейка через `&&`/`;`/пайпы, относительные пути с `..`.
- **Нормализация**: разбор пайпов, редиректов, подстановок; `unparseable` на мусоре; пути и домены.
- **Ступень 2, пути отказа**: таймаут, HTTP 5xx, HTTP 4xx, невалидный JSON, `decision` вне схемы, пустой ответ → `ask`, `error` заполнен. LLM подменяется фейковым HTTP-сервером с заданными ответами.
- **Ступень 2, промпт**: снимок промпта на фиксированный вход; проверка, что `metadata` и любые поля кроме описанных в промпт не попадают.
- **Эскалация**: последовательности исходов → принудительный `ask`; сброс на `allow`.
- **Кэш**: повторный `allow` — `cached: true`; `deny` не кэшируется; смена профиля меняет ключ.
- **API**: контракт запроса и ответа; невалидное тело → `ask` с HTTP 200; без токена при non-localhost → 401; `/healthz`.
- **Контракт**: сгенерированные JSON-схемы совпадают с `contracts/`.
- **Latency**: p50 нормализации + ступени 1 ≤ 1 мс на наборе из 200 команд; регрессия — падение теста.
- **E2E**: сервис + Postgres в compose, фейковый LLM, `hook_client.py` → три сценария (allow по allowlist, hard-deny, deny от модели).

## 10. Деплой и конфигурация

- `docker compose up` в `service/`: контейнер сервиса + Postgres. Переменные: `AGENTGATE_DB_URL`, `AGENTGATE_TOKEN`, `AGENTGATE_BIND` (по умолчанию `127.0.0.1:8400`), `AGENTGATE_PROFILES_DIR`, `AGENTGATE_LOG_PATH`, ключи провайдеров по именам из профилей.
- Локальная модель: тот же клиент, `base_url` на llama.cpp/Ollama. Отдельного кода нет.
- Один инстанс в v1. Несколько инстансов требуют Redis-реализации `SessionStateStore`.

## 11. Roadmap (не в v1, порядок ориентировочный)

1. Модуль пакетов (slopsquatting) в слоте ступени 1: DepScope / реестры, эвристики возраста, загрузок, Левенштейна, суффиксов; fail-closed на 404 и таймаут. Отдельный план.
2. `/v1/observe` и PostToolUse: taint-фрагменты, provenance-правило в ступени 1, флаг `tainted` в промпте.
3. История действий в промпте ступени 2 (вариант 3 из brainstorming) с явным управлением кэшем префикса.
4. Ступень 3 на `U`; override; `PUT /v1/profiles/{id}/rules`; Test → Protect; панель.
5. Redis для `SessionStateStore`; профили в базе с импортом из YAML.
6. Обучение своей модели на H200 при наличии времени.

## 12. Открытые вопросы

- Какой именно набор `safe_prefixes` и read-only команд считать дефолтным: решается по реальным логам первых дней.
- Точный формат хука OpenCode для `contracts/hook_client.py` проверяется по исходникам sst/opencode в день 1 (направление `adapters/` может уточнить).
