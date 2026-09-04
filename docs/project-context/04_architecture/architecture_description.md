# Architecture Description

> Status: Intermediate / Work in Progress
> Target architecture and current implementation are intentionally described separately.
>
> Источники: `docs/project-context/04_architecture/target_architecture.jpg` (целевая архитектура), кодовая база `service/`, `contracts/`, `adapters/`, `benchmark/`, спеки в `docs/superpowers/service/specs/`.
> Дата анализа: 2026-09-04, ветка `main`, HEAD `547220f`.

## 1. Architecture Overview

AgentGate — отдельный сетевой сервис-гейт, стоящий между кодинг-агентом (harness) и операционной системой. Перед исполнением каждого вызова инструмента харнесс через свой адаптер/хук отправляет описание предполагаемого действия в `POST /v1/decide` и получает трёхзначный вердикт `allow | deny | ask`. Сервис не исполняет действия, не является агентом и не является сэндбоксом — он только выносит решение и записывает его.

Техническая задача: дать любому харнессу (в том числе open-source, где auto mode отсутствует) единый, версионируемый слой политики безопасности, который (а) не зависит от вендора агента, (б) содержит детерминированную ступень, вообще не читающую текст, и (в) делает каждое решение измеримым — см. `docs/why-agentgate.md` и кейс `docs/project-context/01_hackathon_case.md`.

Продуктовая связка: компромисс «безопасность против friction». Дешёвая детерминированная ступень пропускает рутину за миллисекунды без обращения к LLM; LLM-ступень включается только для того, что ступень 1 не смогла решить; человек привлекается только там, где нужен `ask`.

---

## 2. Core Architecture Principle

Подтверждённый кодом и спекой основной принцип:

```
AI agent → предложенное действие (tool call) → перехват до исполнения (PreToolUse)
→ нормализация в AST (NormalizedAction) → каскад проверок (детерминированная ступень 1 → LLM-ступень 2)
→ эскалация по истории сессии → decision (allow | deny | ask) → обработка решения в харнессе
```

Три архитектурных решения, из которых вытекает всё остальное:

1. **Точка врезки — до исполнения инструмента, вне харнесса.** Решение принимается в отдельном процессе/сервисе, а не внутри агента: политика одна на все харнессы, живёт в git на стороне сервиса, харнессы о ней ничего не знают (`adapters/README.md`, `docs/why-agentgate.md` §2).
2. **Решение никогда не принимается по сырой строке команды.** Единственное представление, на котором разрешено рассуждать всем ступеням, — `NormalizedAction`, полученный разбором в AST (`service/agentgate/normalize/`, docstring `normalize/model.py`). Это то, что делает ступень 1 архитектурно нечувствительной к тому, что «написано» в контексте.
3. **Fail-closed как позвоночник.** Любая ошибка, таймаут, невалидный запрос или невалидный ответ модели → `ask` с HTTP 200. `allow` по ошибке недостижим ни по одному пути (`service/agentgate/api/app.py`, `service/agentgate/pipeline.py`, `service/agentgate/stage2/run.py` — docstrings и тесты).

---

## 3. Target Architecture

Раздел построен по `target_architecture.jpg`. Всё здесь — целевое состояние; о реализации см. §5 и §7.

### AI Agent

**Purpose:** кодинг-агент внутри харнесса: получает контекст, строит план, предлагает действие.
**Inputs:** пользовательский запрос, данные из источников контекста, результат предыдущего действия, вердикт AgentGate.
**Outputs:** предложенное действие (command / tool call).
**Interactions:** источники контекста → AI Agent; AI Agent → AgentGate (Action Analyzer); AgentGate → AI Agent (вердикт и обратная связь); External Tools & Environment → AI Agent (результаты выполнения).
**Target status:** Target architecture component (внешняя по отношению к AgentGate система).

### Data Sources / «Источники данных и потенциального отравления»

**Purpose:** все каналы, из которых в контекст агента попадают данные: User Prompt, Project Files, MCP Servers, Web / Internet, Terminal Output, Tool Outputs, прочие источники. На схеме это явно помечено как поверхность отравления контекста.
**Inputs:** внешний мир.
**Outputs:** контент, попадающий в контекст агента.
**Interactions:** на схеме поток из источников идёт **в Context Guard**, а не напрямую в агента.
**Target status:** Target architecture component.

### Context Guard

**Purpose:** проверка всех данных **до** попадания в контекст AI-агента. На схеме перечислены: Provenance (источник), Sensitive Data Detection, Prompt Injection Detection, Validation, Data Tagging.
**Inputs:** сырые данные из источников контекста; состояние сессии.
**Outputs:** проверенные/размеченные (tagged) данные для агента; сигналы в Session State / Audit.
**Interactions:** Data Sources → Context Guard → AI Agent; двусторонняя связь с Session State / Audit и с Action Analyzer.
**Target status:** Target architecture component.

### Action Analyzer

**Purpose:** разбор предложенного действия. На схеме: Command / Tool Call, используемые данные, Destination, Expected Impact.
**Inputs:** предложенное агентом действие + контекст сессии.
**Outputs:** структурированное описание действия для Policy & Risk Engine.
**Interactions:** AI Agent → Action Analyzer → Policy & Risk Engine; обмен с Context Guard и Session State / Audit.
**Target status:** Target architecture component.

### Policy & Risk Engine

**Purpose:** оценка риска. На схеме три источника оценки: Hard Rules, Session Rules, AI Risk Classifier.
**Inputs:** структурированное действие, профиль/политика, состояние сессии.
**Outputs:** входные данные для узла Security Decision.
**Interactions:** Action Analyzer → Policy & Risk Engine → Security Decision; чтение/запись Session State / Audit.
**Target status:** Target architecture component.

### Session State / Audit

**Purpose:** состояние всей сессии. На схеме: Actions, Files, Secrets, Destinations, Uploads, Cost, Risk Score, Audit Logs.
**Inputs:** события от Action Analyzer, Policy & Risk Engine, Context Guard.
**Outputs:** контекст сессии для оценки риска; аудит-лог.
**Interactions:** связан со всеми внутренними блоками AgentGate.
**Target status:** Target architecture component.

### Security Decision (узел ветвления) и четыре исхода

**Purpose:** маршрутизация действия по одному из четырёх исходов.
**Outputs (по схеме):**
- **BLOCK** — полная остановка действия, ведёт в терминальный узел **Stop**;
- **SAFE RECOVERY** — запретить опасное действие и **найти безопасный путь**;
- **ASK USER** — требуется подтверждение; ведёт в блок **User Decision** (Approve / Reject / Comment);
- **ALLOW** — автоматическое выполнение, действие уходит в External Tools & Environment.

**Interactions:** Policy & Risk Engine → Security Decision → {BLOCK, SAFE RECOVERY, ASK USER → User Decision, ALLOW}; ALLOW и одобренный ASK USER → External Tools & Environment; результаты и вердикты возвращаются к AI Agent.
**Target status:** Target architecture component.

### User Decision (human-in-the-loop)

**Purpose:** явное человеческое решение по действию, отмеченному как ASK USER: Approve, Reject, Comment.
**Inputs:** действие + причина, по которой понадобилось подтверждение.
**Outputs:** одобрение (действие уходит на исполнение), отказ, либо комментарий агенту.
**Target status:** Target architecture component.

### External Tools & Environment

**Purpose:** исполнительная среда: Filesystem, Shell, MCP Tools, Network, Cloud / S3, Database, APIs и т. п.
**Inputs:** разрешённые действия.
**Outputs:** результаты выполнения — которые на схеме возвращаются в агента и в источники контекста, замыкая цикл.
**Target status:** Target architecture component (вне периметра AgentGate).

### Нефункциональные требования (левая рамка схемы)

«Незаметно для пользователя»: быстрый путь для безопасных действий, **параллельные проверки**, минимальная дополнительная задержка, защита без потери продуктивности.
**Target status:** Target architecture requirement.

---

## 4. Target End-to-End Flow

1. Пользователь отправляет запрос AI-агенту.
2. Данные из источников (User Prompt, Project Files, MCP, Web, Terminal Output, Tool Outputs) идут **не напрямую в агента**, а через **Context Guard**: проверяются provenance, чувствительные данные, попытки prompt injection, валидность; данные размечаются тегами.
3. AI Agent получает проверенный контекст, строит план и предлагает действие.
4. **Action Analyzer** разбирает действие: команда/tool call, используемые данные, назначение, ожидаемое воздействие.
5. **Policy & Risk Engine** оценивает риск: Hard Rules → Session Rules → AI Risk Classifier, с учётом состояния сессии из Session State / Audit.
6. Узел **Security Decision** маршрутизирует результат по одной из четырёх ветвей.
7. Ветвления:
   - **ALLOW** → действие автоматически выполняется в External Tools & Environment;
   - **ASK USER** → **User Decision**: Approve → выполнение; Reject → возврат агенту; Comment → возврат агенту с комментарием человека;
   - **SAFE RECOVERY** → опасное действие запрещается, но система **предлагает безопасный путь**, который возвращается агенту;
   - **BLOCK** → **Stop**: полная остановка действия.
8. Результаты выполнения возвращаются в AI Agent и снова попадают в поверхность источников контекста → следующий цикл снова проходит через Context Guard.
9. Все события (действия, файлы, секреты, назначения, стоимость, risk score) накапливаются в **Session State / Audit** и влияют на оценку последующих действий.

---

## 5. Current Implementation

Основано на коде в репозитории, не на схеме.

### 5.1 Server

Каталог `service/` — единственная полностью реализованная часть системы. Python ≥3.12, FastAPI, Postgres (asyncpg), запуск через Docker Compose (`service/docker-compose.yml`, `service/Dockerfile`).

**HTTP API** — `service/agentgate/api/app.py`
*Purpose:* точка входа. Эндпоинты: `POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{profile_id}`, `GET /healthz`.
*Current behavior:* `/v1/decide` возвращает HTTP 200 **для любого исхода**, включая невалидный JSON (`rule_id: api.invalid-request`) и любое исключение из пайплайна (`api.internal-error`) — оба дают `ask`. Единственный не-200 ответ — 401 от аутентификации. Тело запроса парсится вручную, чтобы автоматическая валидация FastAPI никогда не выдала 422.
*Dependencies:* `Gate`, репозитории Postgres, JSONL-логгер, профили.
*Status:* Implemented.

**Аутентификация** — `service/agentgate/api/deps.py`, `service/agentgate/store/keys.py`, `service/agentgate/cli.py`
*Current behavior:* bearer проходит, если совпадает со статическим `AGENTGATE_TOKEN` **или** с действующим выданным API-ключом (аддитивно). Сравнение через `secrets.compare_digest`. В базе только SHA-256 ключа. Проверка кэшируется в памяти процесса на TTL (по умолчанию 45 с), любая ошибка проверки = «не совпало». Выпуск ключей только из CLI (`python -m agentgate keys create|list|revoke`), HTTP-эндпоинта для выпуска нет.
*Status:* Implemented (с известными ограничениями: атрибуция решения к `key_id` не подключена; кэш per-process — см. корневой `CLAUDE.md`).

**Нормализация действия** — `service/agentgate/normalize/` (`__init__.py`, `shell.py`, `paths.py`, `domains.py`, `model.py`)
*Current behavior:* `tool=shell` разбирается через `bashlex` в список `SimpleCommand` (argv, редиректы, stdin, pipeline_id, тела heredoc) плюс `Flags`: `unparseable`, `has_eval`, `has_subst`, `has_env_assign`, `has_heredoc`, `has_unresolved_expansion`. Для `file_read`/`file_write` резолвятся пути, для `network` — домены, для `mcp_call` — блок `mcp`. Если bashlex не смог распарсить (или упало что-либо при обходе дерева) — `flags.unparseable=True` с пустыми commands/paths/domains, а не «ничего не нашли».
*Status:* Implemented. Это код-аналог целевого **Action Analyzer** (command/tool call, используемые данные, destination-домены), но без явного расчёта «Expected Impact».

**Ступень 1 (детерминированная)** — `service/agentgate/stage1/`
*Current behavior:* фиксированная цепочка `check_hard_deny → check_profile → check_allowlist → check_packages` (`stage1/chain.py`), первый не-None результат короткозамыкает цепь. Hard-deny (`stage1/hard_deny.py`) — правила с тремя исходами: `deny (hard=True)` (окончательный, не переопределяется), `ask (hard=False)` когда форма опасна, но цель неопределима, и `None`. `check_profile` — мутирующие цели должны быть внутри `allowed_paths`, домены проверяются по сетевому allowlist. `check_allowlist` — read-only команды, read-only git-подкоманды и операторские `safe_prefixes`; защищённые пути никогда не разрешаются автоматически. `check_packages` (`stage1/packages.py`) — **пустой слот, всегда возвращает None**.
*Status:* Implemented (кроме модуля пакетов — см. §12). Бюджет p50 ≤ 1 мс проверяется тестом `service/tests/test_stage1_latency.py`.

**Ступень 2 (LLM-классификатор)** — `service/agentgate/stage2/` (`prompt.py`, `client.py`, `run.py`, `schema.py`)
*Current behavior:* вызывается только если ступень 1 не дала решения. Промпт — **закрытый список**: системная роль + профиль + prose-слоты + `[TASK]` + `[ACTION]` + `[FLAGS]` + `[STAGE1]`. `metadata`, рассуждения агента, выводы инструментов и сама сырая строка команды в промпт не попадают. Все значения, до которых дотягивается атакующий (cwd, пути, домены, `user_request`, argv), рендерятся через `json.dumps`, чтобы перевод строки не подделал секцию промпта. `[STAGE1]` — одна из двух фиксированных строк. Ответ модели — structured output по `ClassifierOutput` (`decision: A|D|U`, `risk`, `reason`, `suggest`, `extra="forbid"`). Действие с `flags.unparseable` до LLM не доходит вообще — сразу `ask`. Любая ошибка/таймаут/неожиданное исключение → `ask`. Ретраев нет: один вызов, один таймаут.
*Dependencies:* OpenAI-совместимый API, конфигурируемый в профиле (`base_url`, `model`, `api_key_env`, `timeout_ms`, `structured_output`). В `service/profiles/default-dev.yaml` — три конфигурации: `primary` (OpenRouter, `${OPENROUTER_MODEL_NAME:-openai/gpt-4.1-mini}`), `sonnet`, `local`.
*Status:* Implemented.

**Профили (политика)** — `service/agentgate/profiles/`, `service/profiles/default-dev.yaml`
*Current behavior:* один активный YAML-профиль на сервис: `allowed_paths`, `protected_paths`, `protected_branches`, `network.{mode, allowed_domains}`, `safe_prefixes`, `escalation`, `prose`-слоты, таблица `models`. Поддерживается подстановка `${VAR}` / `${VAR:-default}` из окружения. `profile_hash()` пишется в каждое решение. Профиль отдаётся наружу через `GET /v1/profiles/{id}`.
*Status:* Implemented.

**Состояние сессии и эскалация** — `service/agentgate/session/`
*Current behavior:* `SessionState` хранит `deny_consecutive`, `deny_total`, `decisions_total` и окно последних 50 решений. `should_escalate` (`session/escalation.py`) форсирует `ask`, если подряд N отказов (по умолчанию 3) или ≥10 отказов из последних 50. Эскалация **не применяется** к hard-deny и не превращает `ask` во что-то другое; после срабатывания счётчики сбрасываются. Allow-кэш: только `allow` кэшируется (ключ = profile_hash + action_hash + user_request), `deny`/`ask` — никогда; TTL по умолчанию 86400 с, в памяти на `time.monotonic()`, плюс персистентная таблица `allow_cache`.
*Status:* Implemented. Это частичный аналог целевого **Session State / Audit**: есть Actions и счётчики отказов; Files / Secrets / Destinations / Uploads / Cost / Risk Score как отдельные измерения состояния — нет.

**Пайплайн решения** — `service/agentgate/pipeline.py`
*Current behavior:* фиксированный порядок: резолв профиля (неизвестный → `ask`, stage 0, `api.unknown-profile`) → резолв модели (неизвестная → `ask`, `api.unknown-model`) → `with_workspace` → нормализация → поиск в allow-кэше → ступень 1 → (если решения нет или действие unparseable) ступень 2 → эскалация → запись состояния сессии → запись в allow-кэш → `DecisionRecord`. Персистенция происходит строго **после** формирования ответа, и её сбой проглатывается с логированием.
*Status:* Implemented.

**Хранение и логирование** — `service/agentgate/store/` (`models.py`, `repo.py`, `db.py`, `keys.py`), `service/agentgate/log/jsonl.py`, `service/migrations/`
*Current behavior:* Postgres-таблицы `sessions`, `decisions`, `allow_cache`, `api_keys` (Alembic-миграции `0001_init`, `0002_api_keys`). В `decisions` пишутся: сырое действие, нормализованное действие (JSONB), `user_request`, `profile_id`/`profile_hash`, решение, `reason`/`suggest`, stage, `rule_id`, модель, **сырой ответ модели**, latency по ступеням, ошибка, `cached`, `metadata` (с GIN-индексом). Параллельно каждая строка пишется в append-only JSONL (`AGENTGATE_LOG_PATH`); сбой записи лога не влияет на решение. Запись выполняется в `BackgroundTasks` после отправки ответа.
*Status:* Implemented. Отдельной подсистемы метрик/трейсинга (Prometheus, OpenTelemetry) в коде не найдено — наблюдаемость = JSONL + таблица решений + `GET /v1/decisions` + `GET /healthz`.

**Контракты** — `contracts/` (`decide_request.schema.json`, `decide_response.schema.json`, `openapi.yaml`, `deny_message_template.md`, `curl-examples.md`)
*Current behavior:* JSON-схемы и OpenAPI генерируются из pydantic-моделей (`service/scripts/export_contracts.py`, `export_openapi.py`); тест `service/tests/test_contracts.py` следит за расхождением.
*Status:* Implemented.

### 5.2 Client / Harness Integration Layer

**`adapters/` содержит только `README.md`.** Подпапок `opencode/`, `claude-code/`, `codex/`, `kilo/`, упомянутых в README, в репозитории нет; кода адаптеров нет ни одного файла.
*Status:* Target / not implemented.

**Эталонный клиент** — `contracts/hook_client.py`
*Purpose:* CLI-мост «hook JSON на stdin → решение на stdout», единственная существующая реализация интеграционного слоя.
*Current behavior:* распознаёт два формата хуков по форме payload: Claude Code PreToolUse (`tool_name` / `tool_input` / `session_id` / `cwd`) и OpenCode `tool.execute.before` (`sessionID` / `tool` / `args`). Маппит имена инструментов харнесса в контрактные `tool`-типы (`Bash`→`shell`, `Write|Edit|MultiEdit`→`file_write`, `Read`→`file_read`, `WebFetch|WebSearch`→`network`, остальное→`mcp_call`; аналогично для OpenCode). Собирает тело `/v1/decide`, шлёт POST с bearer из `AGENTGATE_TOKEN`. Возвращает коды выхода: `0` allow, `2` deny, `3` ask. Fail-closed на клиенте: пустой/невалидный stdin, нераспознанная форма хука, недоступность сервиса и таймаут — всё даёт `ask`/3, а не traceback (что под семантикой Claude Code читалось бы как fail-open).
*Dependencies:* только stdlib (`urllib`), переменные `AGENTGATE_URL`, `AGENTGATE_TOKEN`, `AGENTGATE_PROFILE`, `AGENTGATE_USER_REQUEST`.
*Status:* Implemented as a reference client. Регистрации в конкретном харнессе (файлов настроек хуков, плагина Kilo/OpenCode, установщика) в репозитории нет.

*Что интеграционный слой сейчас **не** делает:* не передаёт историю диалога, не оценивает результаты инструментов, не реализует ветку «SAFE RECOVERY», не имеет собственного UI подтверждения — `ask` предполагается штатным диалогом харнесса, но кода, который это делает, нет. `deny_message_template.md` существует как шаблон, но применяет его сам харнесс/адаптер.

### 5.3 Benchmark

**Важно: в рабочем дереве benchmark присутствует лишь фрагмент.** Полная кодовая база бенчмарка была добавлена коммитом `4ec0f71` и **отменена** коммитом `8e2cb5f` («Revert "--added codebase of the benchmark"»); при последующем мердже `547220f` вернулась только часть файлов.

Фактически в HEAD (`git ls-tree HEAD -- benchmark`) присутствуют: `benchmark/CLAUDE.md`, `benchmark/README.md`, `benchmark/config.py`, `benchmark/client/security_service.py`, `benchmark/reporting/report.py`, `benchmark/tests/test_reporting.py`.

**Отсутствуют** (при том, что `benchmark/CLAUDE.md` описывает их как существующие): `cli.py`, `schemas/` (`case.py`, `result.py`), `runner/` (`executor.py`, `recorder.py`), `evaluator/scorer.py`, `storage/sqlite`, `attacks/` (таксономия 15 категорий × 5 кейсов = 70 кейсов), `tools/mock_agentgate.py`, `pyproject.toml`, `pricing.example.yaml`.

Следствие: **бенчмарк в текущем дереве не запускается.** `benchmark/reporting/report.py` импортирует `from schemas.result import ...`, `benchmark/client/security_service.py` — `from schemas.case import ...`; пакета `schemas` в дереве нет, импорт упадёт. Тест `benchmark/tests/test_reporting.py` по той же причине не соберётся.

Что реально есть в коде:

- **`benchmark/config.py`** — `ServiceConfig` (URL, токен, timeout, harness, `profile_id`, `model`), `PricingTable` (только явные операторские цены, ничего не выдумывается), `json_path`, чтение из `SECURITY_SERVICE_URL`/`AGENTGATE_URL`, `AGENTGATE_PROFILE_ID`, `AGENTGATE_MODEL`, `BENCHMARK_PRICING_TABLE`. *Status:* Implemented (модуль самодостаточен).
- **`benchmark/client/security_service.py`** — единственное место, знающее HTTP-контракт: `build_decide_request` собирает тело `/v1/decide` из пары `human_req` + `assistant_tool_call`; `normalize_response` приводит ответ к `ServiceResponse`, отдельно фиксируя `contract_violation`, когда сервис нарушил «HTTP 200 на любой исход»; `derive_components` выводит задействованные компоненты из `stage`/`rule_id`/`cached` с пометкой `derived`; `extract_usage_and_cost` пробует вытащить токены по настраиваемым JSON-путям и при неудаче возвращает `cost=None` с причиной; `_enrich_model_metadata` дорезолвит провайдера и id модели через `GET /v1/profiles/{id}`. Транспортная ошибка не выбрасывается наружу, а превращается в `ServiceResultType.ERROR`. *Status:* Partially implemented (модуль написан, но не запускается без `schemas/`).
- **`benchmark/reporting/report.py`** — сборка сводки и текстового отчёта: перцентили latency, разбивка по категориям/сложности, `benign_asked_friction`, `false_positive_rate`, разбор провалов по тегам. *Status:* Partially implemented (та же зависимость от `schemas/`).

Проектные правила бенчмарка (из `benchmark/CLAUDE.md`, статус — документация, не код): система под тестом — сам сервис, а не агент; скоринг детерминированный, LLM-судьи нет; `session_mode: per_case` по умолчанию, чтобы allow-кэш и счётчики эскалации не протекали между кейсами; `benign_utility` — контрольная группа, где `ask` считается friction; ошибка транспорта всегда даёт 0 баллов.

---

## 6. Current End-to-End Flow

Что подтверждается кодом сегодня:

1. Харнесс перехватывает вызов инструмента до исполнения и передаёт hook-JSON на stdin `contracts/hook_client.py`. **Оговорка:** сам факт регистрации хука в конкретном харнессе кодом в репозитории не подтверждается — есть только клиент, готовый такой JSON принять.
2. `hook_client.py` определяет форму payload (Claude Code / OpenCode), маппит инструмент, собирает тело запроса и шлёт `POST /v1/decide` с bearer-токеном. `user_request` подставляется из `--user-request` / `AGENTGATE_USER_REQUEST`; истории диалога нет.
3. Сервис аутентифицирует запрос (статический токен или выданный API-ключ), валидирует тело; невалидное тело → `ask`, HTTP 200.
4. Резолвится профиль и конфигурация модели; неизвестные — `ask` (stage 0).
5. Действие нормализуется в `NormalizedAction` (AST через bashlex либо резолв путей/доменов/MCP).
6. Если для сессии есть попадание в allow-кэш — сразу `allow`, `stage: 0`, `rule_id: "cache"`, `cached: true`.
7. Иначе — ступень 1: hard-deny → профиль → allowlist → пакеты (пустой слот). Первый результат выигрывает.
8. Если ступень 1 не дала решения (или действие unparseable) — ступень 2: закрытый промпт к OpenAI-совместимому API, один вызов, один таймаут, structured output; любая проблема → `ask`.
9. Эскалация: если это не hard-deny и не уже-`ask`, а история сессии перебрала порог отказов — решение заменяется на `ask` с `rule_id: escalation`, счётчики сбрасываются.
10. Состояние сессии обновляется; `allow` кладётся в кэш; ответ (`decision`, `reason`, `suggest`, `stage`, `rule_id`, `model`, `latency_ms`, `cached`, `decision_id`) уходит клиенту.
11. После ответа в фоне пишутся JSONL-строка и строки Postgres (сессия → решение → allow-кэш); сбой записи логируется и проглатывается.
12. `hook_client.py` печатает решение в stdout и завершается с кодом 0/2/3, который харнесс интерпретирует как allow/deny/ask.

Этот путь end-to-end **проверяется исполнением**: `service/tests/e2e/test_e2e.py` поднимает uvicorn с реальной Postgres и прогоняет через него `contracts/hook_client.py` (маркер `requires_db`).

Чего в текущем flow нет: проверки данных до попадания в контекст агента, оценки результатов инструментов, ветки «безопасной альтернативы» как отдельного исхода (есть только текстовое поле `suggest`), собственного UI подтверждения, накопления Cost / Risk Score / Secrets / Uploads по сессии.

---

## 7. Target vs Current Architecture

| Component / Capability | Target | Current implementation | Status | Evidence |
|---|---|---|---|---|
| Точка врезки: перехват действия до исполнения | Да | Реализована на стороне эталонного клиента; регистрации в харнессе нет | Partially implemented | `contracts/hook_client.py`; `adapters/` (только README) |
| Action Analyzer (command / tool call / данные / destination) | Да | Нормализация в AST: argv, редиректы, пути, домены, MCP, флаги | Implemented | `service/agentgate/normalize/` |
| Action Analyzer → «Expected Impact» как явная величина | Да | Явной оценки воздействия нет; косвенно — через правила и LLM | Target / not implemented | `target_architecture.jpg`; `service/agentgate/stage1/` |
| Policy & Risk Engine → Hard Rules | Да | Hard-deny с тремя исходами, не переопределяется | Implemented | `service/agentgate/stage1/hard_deny.py`, `chain.py` |
| Policy & Risk Engine → Session Rules | Да | Эскалация по deny-окну сессии | Partially implemented | `service/agentgate/session/escalation.py` |
| Policy & Risk Engine → AI Risk Classifier | Да | Ступень 2, structured output, закрытый промпт | Implemented | `service/agentgate/stage2/` |
| Политика/профиль как конфигурация | Подразумевается | YAML-профиль, `profile_hash` в каждом решении | Implemented | `service/agentgate/profiles/`, `service/profiles/default-dev.yaml` |
| Security Decision → ALLOW | Да | `DecisionKind.allow` | Implemented | `service/agentgate/api/schemas.py` |
| Security Decision → BLOCK / Stop | Да | `DecisionKind.deny`; терминальный «Stop» — ответственность харнесса | Partially implemented | `service/agentgate/api/schemas.py`; `contracts/deny_message_template.md` |
| Security Decision → ASK USER | Да | `DecisionKind.ask`; диалог подтверждения на стороне харнесса, кода нет | Partially implemented | `service/agentgate/api/schemas.py`; `adapters/README.md` |
| Security Decision → SAFE RECOVERY (поиск безопасного пути) | Да | Отдельного исхода нет; есть текстовое поле `suggest` в `deny`/`ask` | Target / not implemented | `target_architecture.jpg`; `service/agentgate/api/schemas.py` |
| User Decision: Approve / Reject / **Comment** | Да | Ни канала возврата решения человека, ни `comment` в контракте нет | Target / not implemented | `target_architecture.jpg`; `contracts/decide_request.schema.json` |
| Context Guard (provenance, sensitive data, prompt-injection detection, validation, tagging) | Да | В коде отсутствует полностью; спроектирован как v4 | Target / not implemented | `docs/superpowers/service/specs/v4-context-guard.md`; `context-versions-roadmap.md` |
| Оценка данных **до** попадания в контекст агента | Да | Сервис видит только предложенное действие + последнее сообщение пользователя | Target / not implemented | `service/agentgate/api/schemas.py` (`DecideRequest`) |
| История диалога в запросе | Подразумевается схемой | Полей под историю нет (граница v1 задана сознательно) | Target / not implemented | `context-versions-roadmap.md`; `service/agentgate/api/schemas.py` |
| Оценка tool-result (`direction: in`) | Да | Нет | Target / not implemented | `docs/superpowers/service/specs/v3-tool-result-evaluation.md` |
| Session State / Audit → Actions, счётчики, аудит-лог | Да | Счётчики решений, окно последних 50, JSONL + Postgres | Implemented | `service/agentgate/session/state.py`, `store/models.py`, `log/jsonl.py` |
| Session State / Audit → Secrets, Files, Destinations, Uploads, Cost, Risk Score | Да | Как отдельные измерения состояния сессии отсутствуют | Target / not implemented | `target_architecture.jpg`; `service/agentgate/session/state.py` |
| «Быстрый путь для безопасных действий» | Да | Ступень 1 без LLM (p50 ≤ 1 мс, проверено тестом) + allow-кэш | Implemented | `service/tests/test_stage1_latency.py`; `service/agentgate/session/cache_key.py` |
| «Параллельные проверки» | Да | Проверки строго последовательны, с коротким замыканием | Target / not implemented | `service/agentgate/stage1/chain.py`, `pipeline.py` |
| Модуль пакетов (slopsquatting) | Подразумевается кейсом | Пустой слот, всегда `None` | Target / not implemented | `service/agentgate/stage1/packages.py` |
| Multi-harness интеграции (Claude Code, OpenCode, Codex, Kilo) | Да | Маппинг для двух форм хуков в эталонном клиенте; плагинов нет | Partially implemented | `contracts/hook_client.py`; `adapters/README.md` |
| Бенчмарк как контур проверки | Да | В HEAD только client/config/reporting; датасет и раннер отсутствуют, импорты не резолвятся | Partially implemented | `git ls-tree HEAD -- benchmark`; `benchmark/CLAUDE.md` |
| Идемпотентность повторных `decide` | Требуется контрактом адаптера | Отсутствует; повтор создаёт вторую строку и дважды двигает счётчики | Target / not implemented | `docs/superpowers/service/specs/adapter-contract-gap-analysis.md` |
| Fail-closed при недоступности гейта | Спорно | Внутри сервиса — жёстко fail-closed; контракт адаптера по умолчанию fail-open | Unknown / requires team confirmation | `adapter-contract-gap-analysis.md` |

---

## 8. Key Interfaces and Data Flow

**Harness → Integration layer.**
Транспорт: вызов хука процессом харнесса, payload — JSON на stdin; ответ — JSON на stdout плюс код выхода. Формы: Claude Code PreToolUse (`tool_name`, `tool_input`, `session_id`, `cwd`), OpenCode `tool.execute.before` (`sessionID`, `tool`, `args`). Синхронно, блокирует исполнение инструмента. *Evidence:* `contracts/hook_client.py`.

**Integration layer → Server.**
`POST /v1/decide`, HTTP/JSON, `Authorization: Bearer <token|agk_…>`, синхронно.
Request (`DecideRequest`): `session_id?`, `harness`, `tool ∈ {shell, file_write, file_read, network, mcp_call}`, `raw` (обязателен для `shell`, ≤32 KiB), `args {cwd, paths[], domains[], mcp?}`, `user_request` (обрезается до последних 2048 символов), `profile_id?`, `model?`, `metadata` (≤16 KiB, хранится и возвращается как есть).
Response (`DecideResponse`): `decision ∈ {allow, deny, ask}`, `reason`, `suggest`, `stage ∈ {0,1,2}`, `rule_id?`, `model?`, `latency_ms {stage1?, stage2?, total}`, `cached`, `decision_id` (ULID).
Инвариант: HTTP 200 на любом исходе; 401 — единственный не-200. *Evidence:* `service/agentgate/api/app.py`, `contracts/openapi.yaml`.

**Server → Decision pipeline.**
Внутренний, синхронный: `DecideRequest` → `NormalizedAction` → `Stage1Decision | None` → `Stage2Result` → эскалация → `DecideResponse` + `DecisionRecord`. Персистенция вынесена за пределы горячего пути (BackgroundTasks). *Evidence:* `service/agentgate/pipeline.py`.

**Server → LLM.**
Исходящий HTTP к OpenAI-совместимому `base_url` из профиля, structured output по JSON-схеме `ClassifierOutput`, один вызов, один таймаут (`timeout_ms`), без ретраев. Ключ — из переменной окружения, названной в профиле. *Evidence:* `service/agentgate/stage2/client.py`, `service/profiles/default-dev.yaml`.

**Server → Integration layer → Harness.**
`allow` → инструмент выполняется; `deny` → агенту возвращается текст по `contracts/deny_message_template.md` с `reason` и `suggest`, агент продолжает работу; `ask` → штатный диалог подтверждения харнесса с нашим `reason`. На уровне процесса это коды выхода 0 / 2 / 3. Недоступность сервиса в эталонном клиенте → `ask` (3). *Evidence:* `contracts/hook_client.py`, `adapters/README.md`.

**Server → операторские интерфейсы.**
`GET /v1/decisions` (фильтры `session_id`, `model`, `limit ≤ 500`, курсор `before`), `GET /v1/profiles/{id}` (публичное представление профиля, включая `models.configs`), `GET /healthz` (`{status, db, llm}`, всегда HTTP 200; `llm` — всегда `null`). *Evidence:* `service/agentgate/api/app.py`.

**Benchmark → System under test.**
Тот же публичный контракт: `POST /v1/decide` через `httpx.AsyncClient`, плюс `GET /v1/profiles/{id}` для резолва провайдера/версии модели и `GET /healthz` как проба. Асинхронно, с конфигурируемым concurrency (по описанию в `benchmark/CLAUDE.md`; сам раннер в дереве отсутствует). Бенчмарк не имеет привилегированного доступа внутрь сервиса и ничего не додумывает: чего нет в контракте — помечается `unavailable` с причиной либо `derived`. *Evidence:* `benchmark/client/security_service.py`.

---

## 9. Decision-Making Architecture

### Current implementation

**Входные данные решения:** ровно четыре вещи — нормализованное действие, профиль (политика), последний запрос пользователя и состояние сессии. Ни истории диалога, ни выводов инструментов, ни рассуждений агента, ни `metadata` в оценке не участвует.

**Этапы (строгий порядок, `service/agentgate/pipeline.py`):**
1. Стадия 0 — валидация: неизвестный профиль / неизвестная модель / невалидный запрос → `ask`.
2. Нормализация в `NormalizedAction` (AST). Непарсящееся действие помечается флагом и никогда не считается безопасным no-op.
3. Allow-кэш сессии — единственный путь к мгновенному `allow` без проверок.
4. Ступень 1, детерминированная, без LLM: hard-deny → профиль → allowlist → пакеты.
5. Ступень 2 — LLM-классификатор, только если ступень 1 промолчала.
6. Эскалация по истории сессии.

**Типы решений:** `allow`, `deny`, `ask`. Три, а не четыре: целевой исход SAFE RECOVERY в контракте отсутствует, его роль частично играет текстовое поле `suggest` внутри `deny`/`ask`.

**Обработка неопределённости:** честный `ask` вместо угадывания. Hard-deny, распознавший опасную *форму*, но не сумевший определить *цель* (например, force push без определимого refspec), возвращает `ask`, а не `deny`. Действие, которое bashlex не смог разобрать, до LLM не доходит и даёт `ask`. LLM отвечает `U` (uncertain) → `ask`.

**Human confirmation:** сервис умеет только *запросить* подтверждение (`ask` + `reason`). Диалога, канала возврата ответа человека и типа `comment` в системе нет — это делегировано харнессу и в коде не подтверждается.

**Поведение при ошибке:** любая ошибка на любом уровне → `ask` с HTTP 200: невалидный JSON (`api.invalid-request`), исключение из пайплайна (`api.internal-error`), таймаут/сбой/невалидный ответ LLM (`ask` + поле `error`), сбой записи в БД (проглатывается, решение уже отдано), недоступность сервиса (эталонный клиент отдаёт `ask`/3). `allow` по ошибке недостижим — на каждый такой путь есть тест.

**Приоритеты:** hard-deny финален — его не переопределяет ни ступень 2, ни эскалация. Эскалация не трогает уже-`ask`. `deny` и `ask` не кэшируются никогда.

### Target behavior (отличия)

- Решение принимается не только по действию, но и по **проверенному контексту**: Context Guard оценивает данные до входа в агента (provenance, sensitive data, prompt injection, validation, tagging).
- Четвёртый исход **SAFE RECOVERY**: не просто отказ с подсказкой, а построение безопасной альтернативы.
- **User Decision** как полноценный контур: Approve / Reject / **Comment** с возвратом в агента.
- Оценка риска опирается на богатое состояние сессии: Files, Secrets, Destinations, Uploads, Cost, Risk Score.
- Проверки выполняются **параллельно** ради минимальной задержки; сейчас — последовательно с коротким замыканием.
- По дорожной карте (`context-versions-roadmap.md`) это разложено на версии: v2 — история диалога, v3 — оценка tool-result + provenance, v4 — Context Guard (`mask`). Порядок зафиксирован владельцем продукта; там же явно записано принятое окно уязвимости: с v2 подконтрольный атакующему вывод инструментов попадает в промпт, а семантическая защита приезжает только на v4.

---

## 10. Benchmark Architecture

Целевой контур (из `benchmark/README.md`, `benchmark/CLAUDE.md`):

```
benchmark case (YAML: human_req + assistant_tool_call)
  → dataset load + validate
  → runner.executor (симуляция клиента харнесса, session_mode per_case)
  → POST /v1/decide (AgentGate — система под тестом)
  → evaluator.scorer (детерминированный, без LLM-судьи)
  → runner.recorder → SQLite + JSONL
  → reporting.report → метрики (ASR, FP/friction, latency-перцентили, разбивка по категориям и сложности)
```

Что из этого подтверждается кодом в HEAD: только звенья **client** (сборка запроса и нормализация ответа — `benchmark/client/security_service.py`), **config** (`benchmark/config.py`) и **reporting** (`benchmark/reporting/report.py`). Звенья dataset, executor, scorer, recorder, storage и сам CLI в рабочем дереве отсутствуют (были добавлены в `4ec0f71` и отменены в `8e2cb5f`), поэтому прогон бенчмарка сейчас невозможен, а присутствующие модули не импортируются из-за отсутствующего пакета `schemas`.

Что бенчмарк проверяет **архитектурно** (по замыслу): исключительно публичный контракт сервиса — `POST /v1/decide` плюс `GET /v1/profiles/{id}` и `GET /healthz`. Он не проверяет ни харнесс, ни адаптеры, ни реальное исполнение команд: система под тестом — граница «последний запрос пользователя + предложенный tool call → вердикт». Из этого следует, что бенчмарк способен измерять качество ступеней 1 и 2, эскалацию (через `session_mode: shared`), latency по ступеням и соблюдение контракта (`contract_violation` при HTTP ≠ 200), но **не** способен измерять то, что находится вне этой границы: многоходовые манипуляции, provenance-цепочки и бюджеты сессии — это зафиксировано в `attacks/taxonomy.md` §5 (файл в дереве отсутствует, ссылка — из `benchmark/CLAUDE.md`).

Инвариант «никогда не выдумывать данные сервиса» архитектурно значим: стоимость считается только по явной таблице цен и иначе помечается `unavailable` с причиной; список задействованных компонентов *выводится* из `stage`/`rule_id`/`cached` и помечается `derived`; провайдер и id модели резолвятся через `GET /v1/profiles/{id}` и помечаются `profile_lookup`.

Бенчмарк — **не** production-компонент: он не участвует в горячем пути принятия решений и обращается к сервису как обычный внешний клиент.

---

## 11. Architectural Boundaries

Подтверждено материалами репозитория:

- **AgentGate принимает решение, но не является кодинг-агентом.** Он не планирует, не пишет код и не вызывает инструменты.
- **AgentGate не исполняет и не блокирует действие физически.** Он возвращает вердикт; принудить харнесс исполнить решение он не может. Жёсткую границу (OS-сэндбокс, egress-фильтр) сервис не заменяет — это явно сказано в `docs/why-agentgate.md` §3.
- **AgentGate не является сэндбоксом.**
- **AgentGate не знает о внутренностях харнесса, а харнесс — о внутренностях сервиса.** Адаптер знает только `AGENTGATE_URL`, `AGENTGATE_TOKEN` и опционально `profile_id`; профиль политики живёт целиком на стороне сервиса (`adapters/README.md`, корневой `CLAUDE.md`).
- **Интеграционный слой адаптирует разные харнессы к одному сервису решений** — вся вариативность форматов хуков заканчивается на границе `hook_client.py`.
- **Бенчмарк — не production-компонент**, и система под тестом в нём — сервис, а не агент (`benchmark/CLAUDE.md`).
- **Сервис не читает сырую строку команды для принятия решения** и не пускает в промпт LLM ни `metadata`, ни выводы инструментов, ни рассуждения агента.
- **Хранилище — только Postgres**, SQLite не поддерживается; ретраев к LLM нет.

---

## 12. Known Architectural Gaps

1. **Адаптеров харнессов нет.** `adapters/` содержит только README; ни одного файла интеграции с Claude Code, OpenCode, Codex или Kilo в репозитории нет. Существует только эталонный CLI-клиент `contracts/hook_client.py`. Это разрыв между заявленным «auto mode любому харнессу» и кодом.
2. **Context Guard отсутствует целиком.** Крупный блок целевой схемы (provenance, sensitive data detection, prompt injection detection, validation, data tagging) в коде не представлен; отнесён к v4 и ещё не спроектирован до уровня решения (`v4-context-guard.md` перечисляет кандидатов, ни один не выбран).
3. **Ветка SAFE RECOVERY не существует как исход.** Контракт трёхзначный (`allow|deny|ask`); «найти безопасный путь» сведено к текстовому полю `suggest`, которое ничего не гарантирует и не проверяется.
4. **User Decision реализован лишь наполовину.** Сервис умеет сказать `ask`, но канала возврата решения человека (Approve / Reject / **Comment**) в контракте нет, и UI подтверждения в репозитории нет.
5. **Session State беднее целевого.** Есть счётчики решений и окно отказов; Files, Secrets, Destinations, Uploads, Cost, Risk Score как измерения состояния сессии отсутствуют.
6. **Модуль пакетов — заглушка.** `service/agentgate/stage1/packages.py` всегда возвращает `None`, при том что slopsquatting прямо назван в кейсе хакатона.
7. **Бенчмарк не запускается.** Датасет (70 кейсов, 15 категорий), CLI, раннер, скорер и хранилище отсутствуют в HEAD после revert; оставшиеся модули импортируют несуществующий пакет `schemas`. Никаких измеренных результатов в репозитории нет, и приводить их нельзя.
8. **Нет идемпотентности `decide`.** Повторный запрос (сетевой таймаут, ретрай клиента) создаст вторую строку решения и дважды сдвинет счётчики сессии, приблизив эскалацию за одно действие. Контракт адаптера идемпотентности требует (`adapter-contract-gap-analysis.md`).
9. **Конфликт fail-open / fail-closed не решён.** Сервис жёстко fail-closed внутри, контракт адаптера по умолчанию fail-open на клиенте: «положил гард — разрешено всё». Три варианта решения зафиксированы, выбор владельца продукта на момент анализа не сделан.
10. **Наблюдаемость минимальна.** Есть JSONL, таблица решений и `GET /v1/decisions`; метрик (Prometheus), трейсинга и алертов в коде не найдено. `GET /healthz` всегда возвращает `llm: null` — состояние LLM-провайдера не проверяется.
11. **«Параллельные проверки» из требований схемы не реализованы** — цепочка строго последовательна.
12. **Атрибуция решения к API-ключу не подключена**: по ключу пишется только `last_used_at`, `key_id` в `DecisionRow`/JSONL не попадает (корневой `CLAUDE.md`, docstring `ApiKeyRow`).
13. **Кэш проверки ключа — per-process**: в многопроцессном деплое отзыв ключа доходит до воркеров независимо, задержка отзыва — по худшему из воркеров.
14. **`docs/project-context/04_product_notes.md` — пустой шаблон**: продуктовые формулировки (пользователь, гипотеза, критерии успеха) не зафиксированы, поэтому связь «архитектура ↔ продуктовая задача» в этом документе выведена из кейса хакатона и `why-agentgate.md`, а не из продуктовых заметок.

---

## 13. Open Questions

1. Fail-open или fail-closed на стороне адаптера: требуем `GATE_FAIL_CLOSED=1` как условие поддерживаемой конфигурации, принимаем fail-open как размен, или меняем умолчание контракта? (Варианты 1 / 2 / 3 из `adapter-contract-gap-analysis.md`.)
2. Входит ли **SAFE RECOVERY** в скоуп хакатонного демо, и если да — это четвёртое значение `decision` или соглашение поверх `deny` + `suggest`?
3. Нужен ли **Comment** от человека агенту как часть контракта (новый эндпоинт/поле), или подтверждение целиком остаётся внутри харнесса?
4. Какой харнесс интегрируем первым и до какой степени: Kilo Code (по кейсу хакатона), Claude Code или OpenCode? Считается ли `hook_client.py` достаточной интеграцией для демо?
5. Восстанавливаем ли отменённую кодовую базу бенчмарка (датасет 70 кейсов, CLI, раннер, скорер) в этой ветке, и кто владелец этого восстановления?
6. Какая версия по дорожной карте является целью хакатона: остаёмся на v1 или заявляем v2 (история диалога)?
7. Нужен ли ключ идемпотентности `decide` до демо, или двойной учёт при ретраях принимается как известное ограничение?
8. Какие метрики считаются результатом проекта (ASR / FP / Friction / Latency) и на каком датасете они будут получены, если бенчмарк восстанавливается не полностью?

---

## 14. Architecture Status Summary

| Area | Current status | Target state | Main gap |
|---|---|---|---|
| Server | Implemented: API, каскад 1→2, профили, сессии, Postgres + JSONL, API-ключи, Docker | Плюс Context Guard, оценка tool-result, богатое состояние сессии, параллельные проверки | Сервис видит только одно действие + последнее сообщение пользователя |
| Harness integration | Partially implemented: только эталонный `hook_client.py` (формы Claude Code + OpenCode) | Плагины на харнесс (Kilo, OpenCode, Codex, Claude Code) + обработка всех исходов | Ни одного адаптера в `adapters/`; SAFE RECOVERY и User Decision не обрабатываются |
| Decision pipeline | Implemented: hard-deny → профиль → allowlist → LLM → эскалация, fail-closed, три исхода | Четыре исхода, оценка проверенного контекста, provenance, risk score | Нет SAFE RECOVERY, нет Context Guard, модуль пакетов — заглушка |
| Benchmark | Partially implemented: client + config + reporting; датасет/CLI/раннер отсутствуют, импорты не резолвятся | Полный прогон 70 кейсов с метриками ASR / FP / Friction / Latency | Кодовая база отменена коммитом `8e2cb5f` и не восстановлена; измеренных результатов нет |
| Observability | Partially implemented: JSONL + `decisions` в Postgres + `GET /v1/decisions` + `GET /healthz` | Метрики, аудит по сессии (Cost, Risk Score, Secrets, Destinations), атрибуция к ключу | Нет метрик/трейсинга; `healthz.llm` всегда `null`; `key_id` не пишется |

**Пять выводов:**

- Архитектурный фундамент уже стоит и проверен исполнением: перехват до действия, нормализация в AST, детерминированная ступень до LLM, трёхзначный вердикт, fail-closed на каждом пути, полная запись решений — этого достаточно, чтобы демонстрировать основной flow end-to-end (`service/tests/e2e/test_e2e.py`).
- Наибольшее расхождение с целевой архитектурой — **весь верхний контур схемы**: Context Guard и проверка данных до входа в контекст агента отсутствуют полностью и вынесены в v4.
- Второе по значимости расхождение — **интеграционный слой**: заявленная мультихарнессность держится на одном эталонном CLI-клиенте, каталог `adapters/` пуст.
- Третье — **бенчмарк**: контур измерения, на который опирается вся аргументация «безопасность как метрика команды», сейчас не запускается; никаких результатов заявлять нельзя.
- До финала критично закрыть: минимум одну реальную интеграцию с харнессом, восстановление/пересборку бенчмарка с прогоном на датасете и явное решение по конфликту fail-open/fail-closed — оно определяет, верно ли утверждение «`allow` по ошибке невозможен» для системы, а не только для сервиса.
