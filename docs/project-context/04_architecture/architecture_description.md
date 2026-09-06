# Architecture Description

> Status: Intermediate / Work in Progress
> Target architecture and current implementation are intentionally described separately.
>
> Источники: `docs/project-context/04_architecture/target_architecture.jpg` (целевая архитектура), кодовая база `service/`, `contracts/`, `adapters/`, `benchmark/`, `frontend/`, спеки в `docs/superpowers/service/specs/`.
>
> **Дата анализа: 6 сентября 2026, ветка `main`, HEAD `d24af97`.** Разделы §3 и §4 описывают
> картинку `target_architecture.jpg` и с тех пор не менялись: это целевое состояние, а не
> реализация. Разделы §5–§14 переписаны под фактический код; предыдущая редакция (от
> 4 сентября 2026) описывала раскладку модулей до рефакторинга v1.5 и не знала ни о
> `POST /v1/inspect`, ни об адаптерах, ни о выполненном прогоне бенчмарка.
>
> **Наличие файла или поля здесь не считается доказательством готовности.** Проверки
> исполнением, на которые опирается этот документ, перечислены в §15.

## 1. Architecture Overview

OPENMAGI — отдельный сетевой сервис-гейт, стоящий между кодинг-агентом (harness) и операционной системой. Перед исполнением каждого вызова инструмента харнесс через свой адаптер/хук отправляет описание предполагаемого действия в `POST /v1/decide` и получает трёхзначный вердикт `allow | deny | ask`. После исполнения адаптер может отдать результат инструмента в `POST /v1/inspect` и получить `pass | mask | drop` — это направление добавлено в v3 и расширено в v4. Сервис не исполняет действия, не является агентом и не является сэндбоксом — он только выносит решение и записывает его.

Техническая задача: дать любому харнессу (в том числе open-source, где auto mode отсутствует) единый, версионируемый слой политики безопасности, который (а) не зависит от вендора агента, (б) содержит детерминированную ступень, вообще не читающую текст, и (в) делает каждое решение измеримым — см. `docs/why-agentgate.md` и кейс `docs/project-context/01_hackathon_case.md`.

Продуктовая связка: компромисс «безопасность против friction». Дешёвая детерминированная ступень пропускает рутину за миллисекунды без обращения к LLM; LLM-ступень включается только для того, что ступень 1 не смогла решить; человек привлекается только там, где нужен `ask`.

---

## 2. Core Architecture Principle

Подтверждённый кодом и спекой основной принцип:

```
до действия:   AI agent → предложенный tool call → перехват (PreToolUse)
               → нормализация в AST (NormalizedAction)
               → каскад (детерминированная ступень 1 → LLM-ступень 2)
               → эскалация по истории сессии
               → decision (allow | deny | ask) → обработка решения в харнессе

после действия: результат инструмента + провенанс → перехват до показа модели (PostToolUse)
               → каскад inspect (кэш → секреты → детекторы → маска → классификатор)
               → verdict (pass | mask | drop) → подстановка результата в контекст агента
```

Три архитектурных решения, из которых вытекает всё остальное:

1. **Точка врезки — до исполнения инструмента, вне харнесса.** Решение принимается в отдельном процессе/сервисе, а не внутри агента: политика одна на все харнессы, живёт в git на стороне сервиса, харнессы о ней ничего не знают (`adapters/README.md`, `docs/why-agentgate.md` §2). С v3 у пользователя есть и своя политика, но она едет в запросе и может только ужесточать серверную (`service/agentgate/rules/client_rules.py`).
2. **Решение никогда не принимается по сырой строке команды.** Единственное представление, на котором разрешено рассуждать всем ступеням, — `NormalizedAction`, полученный разбором в AST (`service/agentgate/normalize/`, docstring `normalize/model.py`). Это то, что делает ступень 1 архитектурно нечувствительной к тому, что «написано» в контексте.
3. **Fail-closed как позвоночник.** Любая ошибка, таймаут, невалидный запрос или невалидный ответ модели → `ask` с HTTP 200 на маршруте `decide`. `allow` по ошибке недостижим ни по одному пути (`service/agentgate/api/app.py`, `service/agentgate/engine/gate.py`, `service/agentgate/classify/base.py` — docstrings и тесты). На маршруте `inspect` fail-closed значение другое — `drop`: спрашивать про уже полученный результат некого (`service/agentgate/engine/inspector.py`).

Оговорка, не снятая на 6 сентября 2026: **fail-closed верно для сервиса, но не обязательно для системы.** Клиентская сторона (`adapters/packages/core/src/policy.ts`) по умолчанию fail-open при недоступности гарда; строгий режим включается `GATE_FAIL_CLOSED=1`. Разногласие и три варианта его снятия — `docs/superpowers/service/specs/adapter-contract-gap-analysis.md`.

---

## 3. Target Architecture

Раздел построен по `target_architecture.jpg`. Всё здесь — целевое состояние; о реализации см. §5 и §7.

### AI Agent

**Purpose:** кодинг-агент внутри харнесса: получает контекст, строит план, предлагает действие.
**Inputs:** пользовательский запрос, данные из источников контекста, результат предыдущего действия, вердикт OPENMAGI.
**Outputs:** предложенное действие (command / tool call).
**Interactions:** источники контекста → AI Agent; AI Agent → OPENMAGI (Action Analyzer); OPENMAGI → AI Agent (вердикт и обратная связь); External Tools & Environment → AI Agent (результаты выполнения).
**Target status:** Target architecture component (внешняя по отношению к OPENMAGI система).

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
**Interactions:** связан со всеми внутренними блоками OPENMAGI.
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
**Target status:** Target architecture component (вне периметра OPENMAGI).

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

Раздел описывает код, а не схему. Всё ниже проверено чтением исходников 6 сентября 2026;
там, где утверждение опирается на прогон, прогон назван.

### 5.1 Server

Каталог `service/` — самая полная часть системы. Python ≥ 3.12, FastAPI, Postgres (asyncpg),
SQLAlchemy 2 + Alembic, запуск через Docker Compose.

**HTTP API** — `service/agentgate/api/app.py`, функция `create_app`.
*Маршруты (пять):* `POST /v1/decide`, `POST /v1/inspect`, `GET /v1/decisions`,
`GET /v1/profiles/{id}`, `GET /healthz`.
*Поведение:* тело `decide` и `inspect` парсится вручную, чтобы автоматическая валидация
FastAPI никогда не вернула 422; невалидный JSON или схема дают HTTP 200 с
`rule_id: api.invalid-request` (`ask` на `decide`, `drop` на `inspect`), неизвестное
значение `protocol` — `api.unsupported-protocol`, любое исключение из движка —
`api.internal-error`. Единственный не-200 ответ на этих маршрутах — 401. У читающих
маршрутов (`/v1/decisions`, `/v1/profiles/{id}`) семантика обычная: 422 на плохой
параметр, 404 на неизвестный профиль.
*Status:* Implemented.

**Аутентификация и ключи** — `api/deps.py`, `store/keys.py`, `cli.py`.
Bearer проходит, если совпадает со статическим `AGENTGATE_TOKEN` (через
`secrets.compare_digest`) **или** с действующим выданным API-ключом; результат проверки
ключа кэшируется в памяти процесса на короткий TTL. В базе хранится только SHA-256 ключа.
Выпуск — только из CLI (`python -m agentgate keys create|list|revoke`), HTTP-эндпоинта
выпуска нет по замыслу.
*Status:* Implemented. Ограничение: кэш проверки — per-process, поэтому в многоворкерном
деплое отзыв ключа доходит до воркеров независимо.

**Нормализация действия** — `normalize/` (`shell.py`, `paths.py`, `domains.py`, `model.py`).
`tool: shell` разбирается `bashlex` в список `SimpleCommand` (argv, редиректы, `stdin_from`,
`pipeline_id`, тела heredoc) плюс флаги `unparseable / has_eval / has_subst / has_env_assign /
has_heredoc / has_unresolved_expansion`. Для `file_read`/`file_write` резолвятся пути,
для `network` — домены и `method`, для `mcp_call` — блок `mcp`. Ошибка разбора даёт
`unparseable=True` с пустыми списками, а не частичный результат.
*Status:* Implemented. Это код-аналог целевого **Action Analyzer** в части «command / tool call /
данные / destination»; явной величины **Expected Impact** по-прежнему нет.

**Ступень 1** — `rules/`. Один список `STAGE1` в `rules/chain.py`: `RuleChain` из объектов
`Rule`, первый непустой вердикт побеждает, порядок списка и есть вся приоритетная политика.
Состав по порядку: `UnparseableRule` → группа hard-deny (`rules/hard_deny/`: exfil,
pipe-exec, destructive, protected-write, privilege, git-force) → `WrapperUnresolvedRule` →
`ClientRulesRule("deny")` → `ProfilePathRule` → `ProfileDomainRule` → `ProfileMcpRule("refuse")` →
`ClientRulesRule("ask")` → `ClientRulesRule("allow")` → `ProfileMcpRule("allow")` →
`AllowlistRule` → `McpReadonlyRule` → `ProfileDomainTrustedRule` → `PackagesRule`.
Правило может вернуть не вердикт, а **пол** (`Verdict.floor`): пол не останавливает цепочку и
запрещает итогу быть мягче `ask`, не покупая при этом вызова модели. Так устроен `client.ask`.
*Status:* Implemented, кроме `PackagesRule` — это пустой слот, всегда `None`.
Бюджет латентности p50 ≤ 1 мс проверяется тестом `tests/rules/test_latency.py`.

**Ступень 2** — `classify/` (`base.py`, `llm.py`, `prompt.py`, `render.py`, `schema.py`,
`client.py`). Вызывается, только если ступень 1 не дала вердикта. Промпт — закрытый
список: системная роль, профиль, prose-слоты, `[TASK]`, `[HISTORY]`, `[ACTION]`, `[FLAGS]`,
`[STAGE1]`. `metadata` и рассуждения агента не попадают туда ни по какому пути; каждое
значение, до которого дотягивается атакующий, рендерится через `json.dumps`. Один вызов,
один таймаут, без ретраев, structured output. Любой сбой классификатора превращается в
`ask` внутри самого классификатора (`classify/base.py`), а не полагается на внешний
обработчик.
*Status:* Implemented.

**Каскад inspect** — `inspect/` плюс `engine/inspector.py`. Порядок:
кэш по содержимому → сканер секретов (`inspect.secret`, действие `redact`; кандидаты по
энтропии допускаются только там, где секрет правдоподобен по провенансу) → детекторы
(инструкциеподобный текст, `curl … | sh`, длинные блобы, невидимые символы) → применение
маски с приоритетом `redact > clean > mask`, либо `drop` при пороге в половину строк →
построение сегментов вокруг находок из **уже отредактированного** текста → классификатор
по режиму профиля (`off | on-flag | always`) отвечает спанами строк → сервер сверяет спаны
с отправленными сегментами и применяет ту же маску. Ответ несёт `spans` и `redacted`.
Провенанс — обязательное поле запроса шести видов (`file`, `shell`, `web`, `mcp`,
`subagent`, `unknown`), он сохраняется в строке решения и рендерится в промпт.
*Status:* Implemented. В развёрнутом профиле `default` классификатор inspect **выключен**
(`inspect.classifier` по умолчанию `off`), то есть семантический ярус в бою не работает.

**Профили (политика)** — `profiles/`, `service/profiles/default-dev.yaml`. Один активный
YAML-профиль: `allowed_paths`, `protected_paths`, `protected_branches`,
`network.{mode, allowed_domains, trusted_allows}`, `safe_prefixes`, `escalation`,
prose-слоты, `history`, `inspect`, `mcp`, таблица `models`. `${VAR}` / `${VAR:-default}`
интерполируются из окружения. `profile_hash()` пишется в каждое решение.
*Status:* Implemented. Одно поле — `Profile.rules` (список в YAML) — по-прежнему не читается
нигде: правила пользователя приходят в запросе, а не из профиля.

**Состояние сессии, эскалация, кэши** — `session/`, `domain/session.py`.
`SessionState` хранит счётчики решений и окно последних 50; `should_escalate` форсирует
`ask` при N отказах подряд либо при пороге в окне. Эскалация не трогает hard-deny и уже-`ask`.
Кэшируется только `allow`; ключ включает `profile_hash`, `action_hash`, `user_request`,
дайджест истории и дайджест клиентских правил. Workspace привязан к сессии: он берётся из
`cwd` первого запроса и позже не меняется. Повтор по заголовку `Idempotency-Key` живёт в
границах пары «предъявитель + сессия».
*Status:* Implemented. Это частичный аналог целевого **Session State / Audit**: есть Actions и
счётчики отказов; Files, Secrets, Destinations, Uploads, Cost, Risk Score как измерения
состояния сессии отсутствуют.

**Оркестрация** — `engine/gate.py` (`Gate.decide`), `engine/inspector.py`, `engine/decision.py`,
`engine/inspection.py`, `engine/timings.py`. Фиксированный порядок `decide`: резолв профиля и
модели → состояние сессии и workspace → нормализация → allow-кэш → ступень 1 (с полом) →
ступень 2 → эскалация → фиксация состояния сессии → `Decision`. Персистентность вызывается
строго после формирования ответа.
*Status:* Implemented.

**Хранение и логирование** — `store/`, `log/jsonl.py`, `migrations/`. Postgres, четыре
таблицы: `sessions`, `decisions`, `allow_cache`, `api_keys`. Строки `decide` и `inspect`
живут в одной таблице `decisions` и различаются столбцом `kind`. Девять ревизий Alembic
(`0001_init` … `0009_session_idempotency`). Каждая строка дублируется в append-only JSONL.
Запись идёт в `BackgroundTasks` после отправки ответа; её сбой логируется и проглатывается.
*Status:* Implemented. Отдельной подсистемы метрик или трейсинга (Prometheus, OpenTelemetry)
в коде нет: наблюдаемость — это JSONL, таблица решений, `GET /v1/decisions` и `GET /healthz`.

**Контракты** — `contracts/`. `openapi.yaml` и четыре JSON-схемы (`decide_request`,
`decide_response`, `inspect_request`, `inspect_response`) **порождаются из pydantic-моделей**
(`service/scripts/export_contracts.py`, `export_openapi.py`); `service/tests/test_contracts.py`
падает при расхождении. Плюс `curl-examples.md`, `deny_message_template.md`, каталог
`examples/` с реальными ответами развёрнутого сервиса и эталонный клиент `hook_client.py`.
*Status:* Implemented.

### 5.2 Client / Harness Integration Layer

**Каталог `adapters/` больше не пуст** — это TypeScript-монорепозиторий без сборки
(исполняется Node ≥ 22.6 и Bun). Ядро `packages/core` знает HTTP-контракт, нормализацию,
маппинг инструментов, режимы, кэш и политику fail-open/closed; плагины харнессов от него
зависят и не содержат логики решения.

| Харнесс | Точка входа | Нужен ли патч |
|---|---|---|
| opencode 1.x | плагин (`permission.ask`) | да |
| Kilo CLI | тот же плагин, свой патч | да |
| opencode 2.0 | `Plugin.define` (`ctx.tool.hook`) | нет |
| Pi (pi.dev) | extension (`tool_call` / `tool_result`) | нет |
| Codex CLI | плагин с `hooks.json` | нет |
| DeepSeek Harness | cordis `tools/pre-execute` и `tools/post-execute` | нет |

Плюс npx-инсталлер (`packages/installer`: install / status / doctor / mode / uninstall),
заглушка гарда для тестов (`packages/mock-guard`) и три уровня клиентских правил,
отправляемых в поле `rules`.

*Ступени статуса здесь расходятся, поэтому названы отдельно.*
**Implemented** — код всех шести плагинов есть. **Tested** — есть 150 тестовых блоков на
`node --test` (`packages/*/test/*.test.ts`); при аудите они не запускались, Node на машине
аудита нет. **Integrated** — соответствие запросов реальных сборщиков схемам сервиса
проверяется автоматически тестом `benchmark/tests/test_adapter_contracts.py`, который
исполняет TypeScript через Node или Docker. **Evaluated** — бенчмарком измерена ровно одна
интеграция, Claude Code через хуки SDK (адаптер `claude-agentgate`); остальные пять
проверялись ручными прогонами по `adapters/docs/MANUAL-TESTING.md` и в рамках аудита не
воспроизводились.

Расхождение, которое стоит знать: `adapters/README.md` и docstring
`packages/core/src/protocol.ts` до сих пор утверждают, что маршрута `inspect` на стороне
гарда нет и что поле `rules` сервисом игнорируется. **Оба утверждения устарели** — маршрут
реализован с v3, поле читается ступенью 1. Клиентский код при этом уже умеет и то, и другое.

**Эталонный клиент** — `contracts/hook_client.py`, только stdlib. Распознаёт две формы хука
(Claude Code `PreToolUse` и OpenCode `tool.execute.before`), возвращает коды выхода 0/2/3 и
fail-closed на всех путях. Используется e2e-тестом сервиса как настоящий клиент.
*Status:* Implemented.

*Чего интеграционный слой не делает:* не реализует ветку SAFE RECOVERY как отдельный исход и
не имеет собственного UI подтверждения — `ask` отдаётся штатному диалогу харнесса.

### 5.3 Benchmark

Полный конвейер на Python, система под тестом — сам сервис либо конфигурация Claude Code.
Подробности — `06_benchmark_status.md`; здесь только архитектурно значимое.

- **Три набора кейсов:** 120 pre-action (`attacks/cases/`, 15 категорий атак по 6 кейсов плюс
  контрольная группа `benign_utility` на 30), 46 inspect (`attacks/inspect/`), 12 регрессий
  политики (`attacks/policy/`).
- **Четыре цели прогона (`--adapter`):** `server` (наш сервис по HTTP), `claude-code`
  (родной auto mode), `claude-sdk` (обычные права без классификатора), `claude-agentgate`
  (наше ядро в хуках сессии Claude Code). Три последних требуют одноразовой песочницы и
  явного подтверждения флагами.
- **Метрики** считаются в одном месте (`evaluator/metrics.py`) из сырых результатов: ASR,
  Utility, FP, Friction, латентность (клиентская и сервисная раздельно), стоимость с тремя
  различаемыми состояниями, распределение по ступеням.
- **Внешний baseline ActBench** подключён кодом с зафиксированной ревизией
  (`baselines/actbench.lock.json`) и прогнан целиком 7 сентября 2026: 300 пар задач, два
  плеча, ASR 30,4 % без гарда против 24,3 % под `decide-inspect` (парно по 280 задачам,
  точный Макнемар p = 0,0033). Оценка без LLM-судьи, поэтому UGS и завершение задачи —
  `null`. Числа и условия — `../06_benchmark_actbench_status/README.md`.

*Status:* Implemented и **Evaluated** — три измерения: сквозной прогон 6 сентября 2026 против
развёрнутого сервиса, серия по восьми моделям ступени 2 (по пять прогонов на модель) и полный
корпус ActBench 7 сентября. Артефакты сквозного прогона лежат в `benchmark/results/`, который
в `.gitignore`; разбор — `benchmark/docs/reports/task-24-first-full-benchmark-run.md`. Серия и
ActBench исполнены на отдельном сервере, их сырых артефактов в репозитории нет.

### 5.4 Infrastructure, deployment, frontend

- `service/Dockerfile` (python:3.12-slim, `uv sync --frozen`), `service/docker-compose.yml`
  (Postgres + gate), overlay `docker-compose.deploy.yml` (Caddy как TLS-терминатор и как
  сервер статики), `service/deploy/Caddyfile`, `service/Makefile` (`deploy`, `logs`, `ps`,
  `rollback`, `check-clean`). Ни адресов, ни ключей в репозитории нет — хост резолвится через
  alias в `~/.ssh/config` оператора.
- **Развёртывание подтверждено прогоном при аудите:** `https://api.openmagi.ru/healthz`
  отвечает `{"status":"ok","db":true,"llm":null,"git_sha":"e3c7942…","protocol":1}`;
  `https://api.openmagi.ru/v1/decisions` без токена — 401; `https://openmagi.ru/` — 200.
  Развёрнутая ревизия `e3c7942` отстаёт от `HEAD d24af97` на шесть коммитов, но в `service/`
  за это время изменился только `Makefile`.
- `frontend/site/` — статический лендинг без сборщика, отдаётся тем же Caddy. Часть значений
  в `config.js` — заглушки из дизайн-хэндоффа (`null` рендерится как «——»), в том числе цифры
  бенчмарка: сайт не показывает измеренные результаты.
- **CI нет.** Ни `.github/`, ни иных конфигураций пайплайна; тесты и деплой запускает человек.

---

## 6. Current End-to-End Flow

Два потока, а не один: `decide` (до действия) и `inspect` (после действия, до модели).

### 6.1 `POST /v1/decide`

1. Харнесс перехватывает вызов инструмента до исполнения. Это делает плагин из `adapters/`
   (шесть харнессов) либо эталонный `contracts/hook_client.py`.
2. Клиент собирает тело запроса: `tool`, `raw`, `args`, `user_request`, опционально
   `history`, `rules`, `call_id`, `session_id`, `metadata`, заголовок `Idempotency-Key`.
3. Сервис аутентифицирует bearer (статический токен либо выданный ключ); не совпало — 401.
4. Тело парсится вручную; невалидное или с чужим `protocol` — HTTP 200 и `ask`.
5. Повтор по `Idempotency-Key` в границах предъявителя и сессии возвращает прежнее решение
   без второй строки в базе и без сдвига счётчиков.
6. Резолвится профиль и конфигурация модели; неизвестные — `ask`, `stage: 0`.
7. Берётся или создаётся состояние сессии; workspace фиксируется по первому `cwd` сессии.
8. Действие нормализуется в `NormalizedAction`.
9. Проверяется allow-кэш сессии; попадание — `allow`, `stage: 0`, `cached: true`.
10. Ступень 1: список `STAGE1` до первого вердикта; клиентский `ask` при этом не завершает
    цепочку, а поднимает пол строгости.
11. Ступень 2, если ступень 1 промолчала: один вызов модели, один таймаут, structured output;
    любой сбой — `ask`. К её вердикту применяется пол, если он был поднят.
12. Эскалация по истории сессии, если решение не hard-deny и не уже-`ask`.
13. Ответ: `decision`, `reason`, `suggest`, `stage`, `rule_id`, `model`, `latency_ms`,
    `cached`, `decision_id`, `protocol`, `cost` (когда ступень 2 работала).
14. После ответа — JSONL и Postgres; к строке привязывается `key_id` предъявителя.
15. Харнесс исполняет действие, показывает диалог подтверждения либо возвращает агенту текст
    отказа с `reason` и `suggest`.

### 6.2 `POST /v1/inspect`

1. После исполнения инструмента адаптер отправляет результат: `output`, `status`,
   обязательный `provenance`, `call_id`, `tool_name`, опционально `history`.
2. Кэш по содержимому; далее сканер секретов, детекторы, маска или `drop`.
3. Если профиль разрешает — сегменты вокруг находок уходят классификатору, который отвечает
   спанами строк; сервер валидирует спаны против отправленных сегментов.
4. Ответ: `verdict` (`pass | mask | drop`), при `mask` — переписанный `output`, плюс `spans`
   и `redacted`. Fail-closed здесь — `drop`.
5. Адаптер подставляет результат модели: `pass` — как есть, `mask` — замену, `drop` —
   сообщение о блокировке.

Оба потока подтверждаются исполнением: e2e-тест сервиса поднимает uvicorn с настоящей
Postgres и гоняет через него `contracts/hook_client.py`, а прогон бенчмарка 6 сентября
2026 прошёл оба маршрута против развёрнутого сервиса.

**Чего в текущем потоке нет:** проверки данных до попадания в контекст агента в общем виде
(`inspect` покрывает только результаты инструментов, а не все источники контекста),
taint-цепочек между шагами, отдельного исхода SAFE RECOVERY, канала возврата решения
человека, накопления Cost / Risk Score / Secrets / Uploads по сессии.

---

## 7. Target vs Current Architecture

| Component / Capability | Target | Current implementation | Status | Evidence |
|---|---|---|---|---|
| Точка врезки: перехват действия до исполнения | Да | Плагины к шести харнессам плюс эталонный клиент | Implemented; бенчмарком измерена одна интеграция | `adapters/`, `contracts/hook_client.py` |
| Action Analyzer (command / tool call / данные / destination) | Да | Нормализация в AST: argv, редиректы, пути, домены, `method`, MCP, флаги | Implemented | `service/agentgate/normalize/` |
| Action Analyzer → «Expected Impact» как явная величина | Да | Явной оценки воздействия нет; учитывается косвенно правилами и моделью | Target / not implemented | `target_architecture.jpg` |
| Policy & Risk Engine → Hard Rules | Да | Шесть семейств hard-deny, не переопределяются ничем | Implemented | `service/agentgate/rules/hard_deny/` |
| Policy & Risk Engine → Session Rules | Да | Эскалация по окну отказов сессии | Partially implemented | `service/agentgate/session/escalation.py` |
| Policy & Risk Engine → AI Risk Classifier | Да | Ступень 2, закрытый промпт, structured output | Implemented | `service/agentgate/classify/` |
| Политика как конфигурация | Подразумевается | YAML-профиль оператора плюс клиентские правила в запросе | Implemented | `service/profiles/default-dev.yaml`, `service/agentgate/rules/client_rules.py` |
| Security Decision → ALLOW | Да | `DecisionKind.allow` | Implemented | `service/agentgate/api/schemas.py` |
| Security Decision → BLOCK / Stop | Да | `DecisionKind.deny`; терминальный «Stop» исполняет харнесс | Partially implemented | `contracts/deny_message_template.md`, `adapters/packages/core/src/policy.ts` |
| Security Decision → ASK USER | Да | `DecisionKind.ask`; диалог подтверждения — штатный у харнесса, адаптеры его используют | Partially implemented | `adapters/packages/plugin-v1/src/tui.ts` |
| Security Decision → SAFE RECOVERY | Да | Отдельного исхода нет; есть текстовое поле `suggest` внутри `deny`/`ask` | Target / not implemented | `service/agentgate/api/schemas.py` |
| User Decision: Approve / Reject / **Comment** | Да | Канала возврата решения человека в сервис нет, `comment` в контракте нет | Target / not implemented | `contracts/decide_request.schema.json` |
| Context Guard → Prompt Injection Detection | Да | `POST /v1/inspect`: детекторы инструкциеподобного текста, `curl \| sh`, блобов, невидимых символов | Implemented для результатов инструментов | `service/agentgate/inspect/detectors.py` |
| Context Guard → Sensitive Data Detection | Да | Сканер секретов с действием `redact`; кандидаты по энтропии ограничены провенансом | Implemented | `service/agentgate/inspect/secrets.py` |
| Context Guard → Provenance | Да | Обязательное поле запроса шести видов; хранится и рендерится в промпт | Implemented как метка источника | `service/agentgate/api/schemas.py`, `service/agentgate/inspect/classify.py` |
| Context Guard → taint между шагами | Да | Отсутствует: провенанс управляет только допуском кандидатов по энтропии | Target / not implemented | `service/agentgate/inspect/secrets.py`, корневой `CLAUDE.md` |
| Context Guard → Data Tagging | Да | Частично: ответ несёт `spans` с видом находки и `redacted`, но метка не переживает переход к следующему действию | Partially implemented | `service/agentgate/api/schemas.py` |
| Data Sources → Context Guard → AI Agent (все источники) | Да | Только результаты инструментов, и только если адаптер их прогнал. Пользовательский промпт, файлы проекта и веб напрямую сервис не видит | Partially implemented | `service/agentgate/api/app.py` |
| Session State / Audit: Actions, Audit Logs | Да | Счётчики решений, окно отказов, Postgres + JSONL, лента `GET /v1/decisions` | Implemented | `service/agentgate/store/`, `service/agentgate/log/jsonl.py` |
| Session State / Audit: Files, Secrets, Destinations, Uploads, Cost, Risk Score | Да | Как измерения состояния сессии отсутствуют. Стоимость считается на решение, а не на сессию | Target / not implemented | `service/agentgate/domain/session.py` |
| История диалога в решении | v2 дорожной карты | Реализована: `history` в запросе, дайджест в ключе кэша, усечение по бюджету профиля | Implemented | `service/agentgate/domain/dialogue.py` |
| Идемпотентность повторного запроса | Требование контракта адаптера | Реализована: `Idempotency-Key` в границах предъявителя и сессии | Implemented | `service/agentgate/session/replay.py`, `service/migrations/versions/0009_session_idempotency.py` |
| Параллельные проверки (НФТ левой рамки схемы) | Да | Цепочка строго последовательная с коротким замыканием | Target / not implemented | `service/agentgate/engine/gate.py` |
| Модуль пакетов / slopsquatting | Назван в кейсе | Пустой слот в цепочке | Target / not implemented | `service/agentgate/rules/packages.py` |
| Панель / UI, override, обучение | — | Отсутствуют; в v1 не планировались | Out of scope | корневой `CLAUDE.md` |

---

## 8. Key Interfaces and Data Flow

**Harness → адаптер.** Внутри процесса харнесса: плагин получает предложенный вызов
инструмента и его результат через точки расширения самого харнесса (`permission.ask`,
`ctx.tool.hook`, `hooks.json`, `tool_call`/`tool_result`, cordis `pre-execute`/`post-execute`).
*Evidence:* `adapters/docs/ARCHITECTURE.md`, `adapters/packages/plugin-*`.

**Адаптер → сервис, направление «до действия».** `POST /v1/decide`, HTTP/JSON,
`Authorization: Bearer …`, опционально `Idempotency-Key`.
*Request:* `session_id?`, `call_id?`, `harness`, `tool ∈ {shell, file_write, file_read,
network, mcp_call}`, `raw` (обязателен для `shell`), `args {cwd, paths[], domains[], mcp?,
method?}`, `user_request`, `history[]`, `rules?`, `profile_id?`, `model?`, `metadata`,
`protocol`.
*Response:* `decision ∈ {allow, deny, ask}`, `reason`, `suggest`, `stage ∈ {0,1,2}`,
`rule_id?`, `model?`, `latency_ms {stage1?, stage2?, total}`, `cached`, `decision_id` (ULID),
`protocol`, `cost?`.
*Инвариант:* HTTP 200 на любом исходе; 401 — единственный не-200.
*Evidence:* `service/agentgate/api/app.py`, `contracts/openapi.yaml`.

**Адаптер → сервис, направление «после действия».** `POST /v1/inspect`.
*Request:* `output`, `status`, `provenance` (обязателен), `tool`, `tool_name`, `call_id`,
`args`, `user_request`, `history[]`, `session_id?`, `profile_id?`, `metadata`, `protocol`.
*Response:* `verdict ∈ {pass, mask, drop}`, при `mask` — переписанный `output`, плюс
`spans[]`, `redacted`, `reason`, `stage`, `rule_id?`, `latency_ms`, `decision_id`,
`protocol`, `cost?`.
*Evidence:* `contracts/inspect_request.schema.json`, `contracts/inspect_response.schema.json`.

**Сервис → LLM.** Исходящий HTTP к OpenAI-совместимому `base_url` из профиля, structured
output, один вызов, один таймаут, без ретраев. Ключ — из переменной окружения, названной в
профиле. В развёрнутом профиле ступень 2 — `openai/gpt-4.1-mini` через OpenRouter.
*Evidence:* `service/agentgate/classify/client.py`, `service/profiles/default-dev.yaml`.

**Сервис → операторские интерфейсы.** `GET /v1/decisions` (фильтры `session_id`, `model`,
`kind`, `key_id`, `limit ≤ 500`, курсор `before`), `GET /v1/profiles/{id}`,
`GET /healthz` (`{status, db, llm, git_sha, protocol}`, всегда 200; `llm` всегда `null` —
доступность модели не проверяется).
*Evidence:* `service/agentgate/api/app.py`.

**Бенчмарк → система под тестом.** Тот же публичный контракт для адаптера `server`; для трёх
конфигураций Claude Code — Claude Agent SDK в одноразовом контейнере, причём
`claude-agentgate` вызывает наше TypeScript-ядро из `adapters/packages/core` в хуках
`PreToolUse` и `PostToolUse`. Привилегированного доступа внутрь сервиса у бенчмарка нет.
*Evidence:* `benchmark/automode/`, `benchmark/client/security_service.py`.

---

## 9. Decision-Making Architecture

### Current implementation

**Входные данные решения `decide`:** нормализованное действие, профиль оператора, клиентские
правила из запроса, последний запрос пользователя, усечённая история диалога и состояние
сессии. Рассуждения агента и `metadata` в оценке не участвуют никогда.

**Этапы (строгий порядок, `engine/gate.py`):** стадия 0 — валидация и повтор → нормализация →
allow-кэш → ступень 1 с полом строгости → ступень 2 → эскалация.

**Типы решений:** `allow`, `deny`, `ask` на маршруте `decide`; `pass`, `mask`, `drop` на
маршруте `inspect`. Целевого исхода SAFE RECOVERY нет; его роль частично играет текстовое
поле `suggest`.

**Порядок строгости, зафиксированный явно:** hard-deny финален и не переопределяется ни
ступенью 2, ни эскалацией; `client.deny` строже любого запрета сервиса и освобождён от
эскалации; `client.ask` — пол, а не вердикт, и не отменяет ни один `deny`; `client.allow`
никогда не перекрывает hard-deny и запреты профиля. `deny` и `ask` не кэшируются.

**Обработка неопределённости:** честный `ask` вместо угадывания. Опасная форма с
неопределимой целью даёт `ask`, а не `deny`. Неразобранное действие до модели не доходит
вообще. Ответ модели `U` — `ask`.

**Поведение при ошибке:** `ask` на `decide` и `drop` на `inspect`, оба с HTTP 200. Одно
намеренное исключение: сбой классификатора inspect откатывает решение к вердикту ступени 1,
а не к `drop`, — ступень 1 уже дала безопасный ответ.

### Target behavior (отличия)

- Решение опирается на **проверенный контекст целиком**, а не только на результаты
  инструментов: у целевого Context Guard на входе все источники, включая промпт пользователя,
  файлы проекта и веб.
- Четвёртый исход **SAFE RECOVERY** как построение безопасной альтернативы, а не текстовая
  подсказка.
- **User Decision** как полноценный контур с возвратом Approve / Reject / **Comment** в агента.
- Оценка риска опирается на богатое состояние сессии: Files, Secrets, Destinations, Uploads,
  Cost, Risk Score.
- Проверки выполняются **параллельно** ради минимальной задержки.
- По дорожной карте (`docs/superpowers/service/specs/context-versions-roadmap.md`) версии
  v2–v4 закрыты; открытой остаётся v5 — независимость от провайдера модели.

---

## 10. Benchmark Architecture

```
кейс (YAML) → dataset load + validate → runner.executor → адаптер под тестом
  → evaluator.scorer (детерминированный, без LLM-судьи)
  → runner.recorder → SQLite + JSONL → evaluator.metrics → reporting.report
```

Архитектурно значимо следующее.

- **Система под тестом стала переменной.** Раньше это был только наш сервис; теперь адаптер
  выбирается флагом (`server`, `claude-code`, `claude-sdk`, `claude-agentgate`) за одним швом
  `automode/base.py`. Именно это позволяет сравнивать наш гард с родным auto mode на одной
  популяции кейсов.
- **Границы измерения объявлены в схеме результата.** `ExecutionMode` различает «одно решение
  на кейс» и «полный цикл харнесса», `HistoryMode` — прогон с историей и без неё. Замедление
  задачи целиком не считается ни в одном режиме, и метрика прямо говорит почему.
- **Инвариант «никогда не выдумывать данные сервиса»** сохранён: стоимость либо известна,
  либо это измеренный ноль «модель не вызывалась», либо `None` с причиной; состав компонентов
  выводится из `stage`/`rule_id`/`cached` и помечается `derived`.
- **Сравнение двух прогонов** (`cli.py compare`) считает и общие цифры, и попарные — только по
  кейсам, где решение вынесли оба прогона, и перечисляет каждое исключение.
- Бенчмарк **не** production-компонент: в горячем пути он не участвует и ходит в сервис как
  обычный внешний клиент.

---

## 11. Architectural Boundaries

- **OPENMAGI принимает решение, но не является кодинг-агентом.** Он не планирует, не пишет
  код и не вызывает инструменты.
- **OPENMAGI не исполняет и не блокирует действие физически.** Он возвращает вердикт;
  принудить харнесс исполнить решение он не может. Жёсткую границу (OS-сэндбокс,
  egress-фильтр) он не заменяет — прямо сказано в `docs/why-agentgate.md`.
- **OPENMAGI не является сэндбоксом.**
- **Харнесс не знает о внутренностях сервиса.** Адаптер знает URL, токен и опционально
  `profile_id`; профиль политики живёт целиком на стороне сервиса. Обратное тоже верно:
  вся вариативность форматов хуков заканчивается в `adapters/packages/core`.
- **Политика двусторонняя, но несимметричная.** Оператор задаёт профиль на сервере,
  пользователь — правила в запросе; правила пользователя могут только ужесточать.
- **Сервис не читает сырую строку команды для принятия решения** и не пускает в промпт
  ни `metadata`, ни рассуждения агента.
- **Хранилище — только Postgres**, SQLite не поддерживается; ретраев к LLM нет.
- **Бенчмарк — не production-компонент.**

---

## 12. Known Architectural Gaps

1. **Context Guard закрыт частично.** Реализовано направление «результат инструмента →
   модель» (`/v1/inspect`): детекторы, маскирование секретов, провенанс, опциональный
   классификатор. Не реализовано: контроль остальных источников контекста и taint-цепочки
   между шагами. Провенанс есть как метка, но не как распространяемая пометка.
2. **Семантический ярус inspect в бою выключен.** В развёрнутом профиле
   `inspect.classifier: off`; в прогоне 6 сентября 2026 все три кейса `semantic_gap` прошли
   мимо, и это ожидаемое поведение конфигурации, а не дефект детекторов.
3. **Ветка SAFE RECOVERY не существует как исход.** Контракт трёхзначный; «найти безопасный
   путь» сведено к текстовому полю `suggest`, качество которого ничем не измеряется.
4. **User Decision реализован наполовину.** Сервис умеет сказать `ask`, но канала возврата
   решения человека (Approve / Reject / **Comment**) в контракте нет.
5. **Session State беднее целевого.** Files, Secrets, Destinations, Uploads, Cost, Risk Score
   как измерения состояния сессии отсутствуют.
6. **Модуль пакетов — заглушка**, при том что slopsquatting назван в кейсе хакатона.
7. **Конфликт fail-open / fail-closed не решён.** Сервис жёстко fail-closed, клиент по
   умолчанию fail-open. Три варианта решения зафиксированы, выбор владельца продукта не
   сделан. Пока он не сделан, утверждение «`allow` по ошибке невозможен» верно для сервиса,
   но не для системы.
8. **Наблюдаемость минимальна.** JSONL, таблица решений, лента и `/healthz`; метрик,
   трейсинга и алертов нет. `healthz.llm` всегда `null`.
9. **«Параллельные проверки» из НФТ схемы не реализованы** — цепочка строго последовательна.
10. **Состояние сессии живёт в памяти процесса.** Postgres используется как журнал и как
    источник восстановления при старте, но не как общее состояние на горячем пути: несколько
    воркеров дают независимые счётчики эскалации и независимые allow-кэши. Тот же характер у
    кэша проверки API-ключей.
11. **Решение теряется при падении между ответом и персистентностью** — сознательный размен
    латентности на полноту журнала.
12. **`GET /v1/decisions` не разграничен по владельцу ключа.** Фильтр `key_id` есть, но любой
    действующий bearer читает всю ленту целиком, включая `raw` и `user_request`.
13. **Поле профиля `rules` не читается нигде** — молча, без ошибки загрузки.
14. **CI нет.** Прогон тестов и деплой держатся на дисциплине человека.
15. **Документация адаптеров отстала от сервиса**: заявляет отсутствие маршрута `inspect` и
    игнорирование поля `rules`, хотя оба реализованы.

---

## 13. Open Questions

1. Fail-open или fail-closed на стороне адаптера: требуем `GATE_FAIL_CLOSED=1` как условие
   поддерживаемой конфигурации, принимаем fail-open как размен, или меняем умолчание
   контракта? Вопрос открыт с 3 сентября, и от него зависит формулировка главной гарантии.
2. Входит ли **SAFE RECOVERY** в скоуп защиты, и если да — это четвёртое значение `decision`
   или соглашение поверх `deny` + `suggest`?
3. Нужен ли **Comment** от человека агенту как часть контракта?
4. Включать ли `inspect.classifier` в развёрнутом профиле: он закрывает `semantic_gap`, но
   добавляет вызов модели на каждый флагованный результат и латентность к горячему пути.
5. Что делать с результатом прогона 6 сентября: родной auto mode Claude Code на общей
   популяции оказался точнее нашего гарда при равном ASR. Улучшать ступень 2, переносить
   промахи в детерминированные правила или менять модель — решение не принято.
6. Нужно ли повторять ActBench с нативным оценщиком (нужен ключ модели): корпус прогнан
   7 сентября и дал дельту ASR, но без LLM-судьи полезность на исполняемых задачах осталась
   неизмеренной, а плечи `decide` и `decide-inspect` не разделены.
7. Стоит ли расширять состояние сессии до бюджетов автономии — это единственный
   архитектурный ответ на промахи пер-экшн-классификации, и его нет ни у кого.

---

## 14. Architecture Status Summary

| Area | Current status | Target state | Main gap |
|---|---|---|---|
| Server | Implemented и развёрнут: пять маршрутов, каскад decide (1→2) и каскад inspect, профили, клиентские правила, сессии, идемпотентность, Postgres + JSONL, API-ключи | Плюс контроль всех источников контекста, taint, богатое состояние сессии, параллельные проверки | Сервис видит одно действие плюс диалог и один результат инструмента за раз; связей между шагами не строит |
| Harness integration | Implemented: плагины к шести харнессам плюс ядро и инсталлер | Все исходы обрабатываются одинаково во всех харнессах | Бенчмарком измерена одна интеграция из шести; у Codex `PreToolUse` умеет только `deny`; документация адаптеров отстала |
| Decision pipeline | Implemented: пол строгости, hard-deny → правила пользователя → профиль → allowlist → модель → эскалация, fail-closed | Четыре исхода, оценка проверенного контекста, risk score | Нет SAFE RECOVERY, модуль пакетов — заглушка, проверки последовательны |
| Context Guard | Partially implemented: `/v1/inspect` с детекторами, секретами, провенансом и опциональным классификатором | Проверка всех источников до входа в контекст, taint и tagging между шагами | Классификатор в бою выключен; провенанс не распространяется |
| Benchmark | Implemented и Evaluated: 120 + 46 + 12 кейсов, четыре цели прогона, метрики из сырых результатов; сквозной прогон, серия по восьми моделям ступени 2 и полный корпус ActBench (300 пар, ASR 30,4 % → 24,3 %, p = 0,0033) | Регулярные прогоны, внешний baseline с нативным судьёй, сравнение с большим числом решений | Один прогон на своём датасете, ActBench без LLM-судьи (полезность не измерена), результаты не версионируются, артефакты серии и ActBench вне репозитория |
| Observability | Partially implemented: JSONL, `decisions`, лента с фильтрами, `/healthz` | Метрики, трейсинг, аудит по сессии, разграничение ленты | Нет метрик и трейсинга; `healthz.llm` всегда `null`; лента не разграничена |
| Deployment | Implemented: Docker Compose плюс Caddy, подтверждено ответом `api.openmagi.ru/healthz` | Воспроизводимый деплой из CI, несколько воркеров с общим состоянием | CI нет; состояние сессии не общее, поэтому масштабирование воркерами меняет поведение |

**Пять выводов.**

- Фундамент стоит и проверен исполнением: перехват до действия, нормализация в AST,
  детерминированная ступень до модели, трёхзначный вердикт, fail-closed, полная запись
  решений, развёрнутый экземпляр и шесть адаптеров.
- Верхний контур целевой схемы закрыт **частично, а не полностью**: `inspect` — это
  Context Guard для результатов инструментов, но не для всех источников контекста, и без
  taint между шагами.
- Появились собственные измерения, и они не подтвердили превосходства над родным auto mode
  Claude Code. Это результат, а не помеха: именно ради него бенчмарк и строился. Серия по
  восьми моделям ступени 2 добавила к этому важную поправку: сравнение шло на одной модели,
  а ASR на тех же кейсах меняется от 1,1 % до 45,3 % при смене модели — значит, измерена
  конфигурация, а не архитектура, и вывод держится до пересравнения на сильной модели.
- Внешний корпус ActBench (7 сентября) дал первую причинную дельту: 30,4 % → 24,3 % ASR,
  p = 0,0033. Архитектурно это подтверждает, что гард влияет на исход там, где траекторию
  выбирает сам агент, — но без LLM-судьи цена этого влияния в незавершённых задачах не видна.
- Главные незакрытые архитектурные вопросы — не отсутствующие модули, а нерешённые развилки:
  fail-open клиента, включение семантического яруса inspect, наличие или отсутствие бюджетов
  автономии на сессию.

---

## 15. Проверки, на которые опирается этот документ

Выполнены 6 сентября 2026 на Windows-хосте, если не сказано иное.

- `cd service && uv run pytest -q` → **1538 собрано, 1414 passed, 44 failed, 80 skipped**.
  Все 44 падения — среда, а не логика: разделитель пути (`os.path.normpath('/home/u/repo')`
  → `\home\u\repo`), кодировка по умолчанию при чтении контрактов без явного `encoding`
  (`test_contracts.py`; при чтении в UTF-8 схемы совпадают побайтно) и два теста бюджета
  латентности детекторов. 80 skip — из-за незаданного `AGENTGATE_TEST_DB_URL`. Целевая
  среда — Linux в контейнере; зелёный прогон там в рамках аудита не воспроизводился, Docker
  на машине аудита не запущен.
- `cd benchmark && uv run pytest` → **399 passed, 2 skipped, 2 deselected**. Оба skip — из-за
  отсутствия Node 24 и Docker.
- `curl https://api.openmagi.ru/healthz` → `{"status":"ok","db":true,"llm":null,
  "git_sha":"e3c7942…","protocol":1}`; `curl https://api.openmagi.ru/v1/decisions` → 401;
  `curl https://openmagi.ru/` → 200.
- Цифры прогона бенчмарка пересчитаны из `benchmark/results/benchmark.sqlite3` и совпали с
  `benchmark/docs/reports/task-24-first-full-benchmark-run.md`.
- Тесты адаптеров (`node --test`, 150 блоков `it(`) **не запускались**: Node на машине
  аудита нет.
