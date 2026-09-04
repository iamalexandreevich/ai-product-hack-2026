# Current State

Status: Intermediate / Work in Progress

> Дата анализа: 2026-09-04, ветка `main`, HEAD `547220f`, рабочее дерево чистое (не отслеживаются только `.idea/`, `benchmark/.idea/`, `docs/project-context/`).
>
> Целевая архитектура: `docs/project-context/04_architecture/architecture_description.md` (§3, §4) и `target_architecture.jpg`. Здесь описано **фактическое** состояние кода, сверенное с целевой архитектурой. Наличие файла, класса или конфигурационного поля само по себе не считалось доказательством готовности: каждый вывод проверялся чтением кода, а часть — исполнением (прогон тестов, попытка импорта модулей бенчмарка).
>
> Проверка исполнением, на которую опираются выводы ниже:
> - `cd service && uv run pytest -q` → **509 тестов собрано, 425 passed, 41 failed, 43 skipped** на Windows-хосте. Все 41 падения — расхождение разделителя пути (`os.path.normpath('/home/u/repo')` → `\home\u\repo`); подробности в разделе Known limitations.
> - `cd benchmark && python -c "import reporting.report"` → `ModuleNotFoundError: No module named 'schemas'`.

---

## Implemented

### Decision server

Всё перечисленное в этом разделе подключено к основному потоку `POST /v1/decide` и покрыто тестами.

**HTTP-слой и fail-closed граница.** `service/agentgate/api/app.py`, функция `create_app`. Четыре маршрута: `POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{profile_id}`, `GET /healthz`. Обработчик `decide` принимает сырой `Request`, а не `DecideRequest`-параметр, — специально, чтобы автоматическая валидация FastAPI никогда не вернула 422: тело парсится и валидируется вручную, невалидный JSON и невалидная схема дают HTTP 200 + `ask` с `rule_id="api.invalid-request"`. Любое исключение, вышедшее из `Gate.decide`, перехватывается и превращается в HTTP 200 + `ask` с `rule_id="api.internal-error"`. Единственный не-200 статус на этих маршрутах — 401 из зависимости авторизации. Функция: внешняя граница, на которой отказ никогда не может стать `allow`.

**Конвейер решения.** `service/agentgate/pipeline.py`, класс `Gate`, метод `decide`. Фиксированный порядок: разрешение профиля (неизвестный → `ask`, `api.unknown-profile`) → разрешение модели (неизвестная → `ask`, `api.unknown-model`) → `with_workspace` → `normalize` → поиск в allow-кэше → ступень 1 → ступень 2 (если ступень 1 вернула `None` или действие непарсибельно) → эскалация → запись состояния сессии → `cache_put` для `allow` → сборка `DecisionRecord`. Персистентность вызывается строго после сборки ответа. Функция: единственная точка, где собирается решение.

**Нормализация в AST.** `service/agentgate/normalize/`. `shell.py` (500 строк) обходит дерево `bashlex` и строит `SimpleCommand` (argv, редиректы, `stdin_from`, `pipeline_id`, тела heredoc), извлекает пути и домены, выставляет флаги `unparseable / has_eval / has_subst / has_env_assign / has_heredoc / has_unresolved_expansion`. Разбирает вложенные heredoc/here-string как код, когда argv[0] — шелл (в том числе за обёрткой `env`/`sudo`/`nohup`/`timeout`/`nice`/`setsid`/`stdbuf`), с ограничением глубины рекурсии `_MAX_NESTING_DEPTH = 8`. Весь пост-парсинг обёрнут одним `try/except`: любая ошибка даёт `unparseable=True` с пустыми `commands/paths/domains`, а не частичный результат. `paths.py` — `resolve_path`, `is_within`, `matches_any`, `looks_unresolved`; `domains.py` — извлечение доменов; `model.py` — `NormalizedAction` с `action_hash()` (SHA-256 по всему объекту без `raw`). Функция: единственное представление действия, по которому разрешено принимать решение.

**Ступень 1, каскад.** `service/agentgate/stage1/chain.py`: фиксированный порядок `[check_hard_deny, check_profile, check_allowlist, check_packages]`, первый не-`None` результат замыкает цепочку. Функция: детерминированная ступень без обращения к LLM.

**Ступень 1, hard-deny.** `service/agentgate/stage1/hard_deny.py` (922 строки). Шесть семейств правил в списке `RULES`: `_rule_exfil` (отправка секретов наружу — `curl/wget/nc/ssh/scp/rsync/socat`, upload-флаги, пайп из чтения секрета в сетевую команду), `_rule_pipe_exec` (`curl … | sh` и его варианты), `_rule_destructive` (`rm -rf`, `find -delete`, `dd`, `shred` вне воркспейса или по корню воркспейса), `_rule_protected_write` (запись в `protected_paths`), `_rule_privilege` (эскалация привилегий, firewall-команды), `_rule_git_force` (`git push --force` в защищённую ветку, с разбором глобальных опций git и `fnmatch` по `protected_branches`). Три исхода вместо двух: `deny(hard=True)` — цель определима и опасна, финально; `ask(hard=False)` — форма опасна, но цель по командной строке неопределима; `None` — не применимо либо цель определима и безопасна. Функция: слой, который не переопределяется ничем.

**Ступень 1, проверка профиля.** `service/agentgate/stage1/profile_check.py`. Мутирующие цели (фиксированный набор команд `MUTATING`, редиректы `>`/`>>`, пути `file_write`) обязаны лежать внутри `resolved_allowed_paths()`, иначе `deny` с `rule_id="profile.path"`. Домены вне allowlist: `deny` (режимы `off`/`allowlist`), `ask` (режим `ask`), пропуск (режим `open`). Чтение вне воркспейса сюда сознательно не попадает и проваливается на ступень 2.

**Ступень 1, allowlist.** `service/agentgate/stage1/allowlist.py`. Разрешает фиксированный набор read-only команд (`READONLY`), read-only подкоманды git (`GIT_READONLY`), `echo`, голый `env`, `find` без `-delete`/`-exec`/`-execdir`/`-ok`, а также операторские префиксы `profile.safe_prefixes`. Разрешает `file_read`/`file_write` внутри allowed и вне protected. Защищённый путь никогда не разрешается автоматически — ни на запись, ни на чтение; перечисление путей идёт через общий `stage1/argv_paths.py:command_argv_paths`, а не через `NormalizedAction.paths`, чтобы команда вне `PATH_COMMANDS` не проскочила мимо проверки.

**Ступень 2, LLM-классификатор.** `service/agentgate/stage2/`. `prompt.py` строит закрытый промпт: системная роль + `[PROFILE]` + prose-слоты + `[TASK]` + `[ACTION]` + `[FLAGS]` + `[STAGE1]`; каждое значение, до которого дотягивается атакующий (`cwd`, пути, домены, `user_request`, argv), рендерится через `json.dumps`, поэтому перевод строки внутри значения не может подделать секцию промпта. `metadata` и сырая строка команды в промпт не попадают ни по какому пути. `client.py` — OpenAI-совместимый `POST /chat/completions`: ровно один вызов, без ретраев, с таймаутом из профиля; пять видов отказа (`timeout | http | invalid_json | invalid_schema | empty`) поднимаются как `Stage2Error`. `schema.py` — `ClassifierOutput` с `extra="forbid"` и strict JSON Schema для structured output. `run.py` — любой отказ, включая непредвиденное исключение, даёт `DecisionKind.ask`; непарсибельное действие отклоняется до построения промпта, LLM его вообще не видит.

**Эскалация по истории сессии.** `service/agentgate/session/escalation.py` + `state.py`. `should_escalate` срабатывает при `deny_consecutive >= profile.escalation.deny_consecutive` (по умолчанию 3) либо при `count` отказов в окне последних `of_last` решений (по умолчанию 10 из 50). В `pipeline.py` эскалация применяется только если `not hard` и решение ещё не `ask`, после чего счётчики обнуляются. Функция: `Session Rules` из целевой схемы в объёме «счётчик отказов».

**Allow-only кэш.** `session/cache_key.py` — ключ `SHA-256(profile_hash + action_hash + user_request)`. `session/memory.py` — in-memory хранилище с TTL по `time.monotonic()`. `deny` и `ask` не кэшируются нигде. Попадание в кэш отвечает `allow`, `stage: 0`, `rule_id: "cache"`, `cached: true`. При старте `__main__.build_app` восстанавливает и состояния сессий, и валидные записи кэша из Postgres.

**Хранилище.** `service/agentgate/store/`. `models.py` — таблицы `sessions`, `decisions`, `allow_cache`, `api_keys`; на `decisions` пять индексов, включая GIN по `metadata`. `repo.py` — `DecisionRepo` (insert, list с курсорной пагинацией по `id` и клампом `limit` в `[1, 1000]`) и `SessionRepo` (upsert через `ON CONFLICT`, `load_all`, `cache_put`, `cache_load_valid`; `cache_put` отвергает naive-datetime). `db.py` — asyncpg-движок с `pool_pre_ping`. Миграции: `migrations/versions/0001_init.py`, `0002_api_keys.py`.

**JSONL-лог.** `service/agentgate/log/jsonl.py`. Пишется в `BackgroundTasks` после ответа; ошибка записи логируется и глотается. Поле `id` записи переименовывается в `decision_id`, чтобы лог сходился с телом ответа.

**Аутентификация.** `service/agentgate/api/deps.py`, `make_require_token`. Bearer проходит, если совпадает со статическим `AGENTGATE_TOKEN` (через `secrets.compare_digest`) **или** с действующим выданным API-ключом. `_KeyVerifier` кэширует результат проверки в памяти процесса по SHA-256 ключа с TTL 45 с; любая ошибка репозитория трактуется как «не совпало» и **не** кэшируется. `last_used_at` обновляется best-effort после ответа.

**API-ключи.** `service/agentgate/store/keys.py` (генерация `agk_` + base64url(32 байта), хранение только SHA-256) и `service/agentgate/cli.py` (`python -m agentgate keys create|list|revoke`, диспетчеризуется из `__main__.main` до старта uvicorn). HTTP-эндпоинта выпуска нет — по замыслу.

**Конфигурация и профили.** `config.py` — `Settings` с префиксом `AGENTGATE_`, валидация `bind` (включая скобочный IPv6), `validate_token_for_bind()` требует токен на non-localhost. `profiles/schema.py` — `Profile` со всеми полями политики и `profile_hash()`; `profiles/loader.py` — загрузка YAML, интерполяция `${VAR}` / `${VAR:-default}` с сохранением `${WORKSPACE}`, определение воркспейса по ближайшему `.git`. Рабочий профиль: `service/profiles/default-dev.yaml`.

**Тесты.** 509 собираемых тестов в `service/tests/`, включая табличные тесты hard-deny с обфускацией, тест бюджета латентности `tests/test_stage1_latency.py` (p50 нормализации + ступени 1 ≤ 1 мс на 200 прогонах) и e2e `tests/e2e/test_e2e.py` (три сценария через реальный подпроцесс сервиса, fake LLM и `contracts/hook_client.py`).

### Harness integration layer

**Эталонный клиент хука.** `contracts/hook_client.py` — единственный работающий код этого слоя. Только stdlib. `to_request()` распознаёт два формата по форме входного JSON: Claude Code `PreToolUse` (ключ `tool_name`) и OpenCode `tool.execute.before` (ключ `sessionID`), и отображает их на `tool` = `shell | file_write | file_read | network | mcp_call`. Коды выхода: `0` allow, `2` deny, `3` ask. Fail-closed на всех путях: пустой stdin, не-JSON, нераспознанный формат хука, недоступность сервиса и таймаут дают `ask`/3, а не трейсбек — под семантикой кодов выхода Claude Code трейсбек читался бы как fail-open. Функция: показать, как харнесс должен разговаривать с сервисом; используется e2e-тестом как реальный клиент.

**Контракт.** `contracts/openapi.yaml` (OpenAPI 3.1, все четыре маршрута), `contracts/decide_request.schema.json`, `contracts/decide_response.schema.json` — генерируются из pydantic-моделей (`service/scripts/export_contracts.py`, `export_openapi.py`), `service/tests/test_contracts.py` падает при расхождении. `contracts/curl-examples.md` — примеры вызовов по всем пяти видам `tool`. `contracts/deny_message_template.md` — шаблон текста для агента при `deny`.

### Benchmark

Реализовано на стороне **сервиса**, а не в `benchmark/`, — но реализовано и работает:

- поле `model` в `DecideRequest` выбирает конфигурацию модели ступени 2 на запрос (`profiles/schema.py: ModelsConfig.model_config_for`), что и есть механика внутреннего бенчмарка «сравнить модели на одном наборе кейсов»;
- `metadata` запроса сохраняется как есть в JSONB с GIN-индексом (`store/models.py`), поэтому `run_id` прогона переживает запись и доступен для выборки;
- `GET /v1/decisions` фильтрует по `session_id` и `model`, пагинируется курсором `before`;
- ответ несёт `latency_ms.stage1 / stage2 / total`, `stage`, `rule_id`, `cached` — метрики, по которым бенчмарк считает latency и распределение по ступеням.

Внутри самого каталога `benchmark/` работоспособного кода нет — см. Partially implemented и In progress.

### Infrastructure / deployment

- `service/Dockerfile` — `python:3.12-slim`, слоистый `uv sync --frozen`, `CMD` сначала `alembic upgrade head`, затем `python -m agentgate`. Задаёт `AGENTGATE_BIND=0.0.0.0:8400`, каталог профилей и путь JSONL.
- `service/docker-compose.yml` — два сервиса: `db` (postgres:16-alpine с healthcheck и монтированием `scripts/init-test-db.sql` для тестовой базы) и `gate` (сборка из локального Dockerfile, ожидание healthy-БД, публикация 8400, именованный том под JSONL). `AGENTGATE_TOKEN` объявлен как `${AGENTGATE_TOKEN:?…}` — compose падает, если токен не задан.
- `service/Makefile` — цели `deploy`, `logs`, `ps`, `rollback`, `check-clean`. Деплой: отказ при грязном дереве в `service/` → `rsync` закоммиченного дерева → `docker compose up -d --build` на сервере → `alembic upgrade head` внутри контейнера → опрос `/healthz` до 30 с. `rollback` восстанавливает образ, записанный в `.previous_gate_image` перед последней сборкой. Ни IP, ни ключа, ни пароля в файле нет: всё резолвится через alias в `~/.ssh/config` оператора.
- Миграции Alembic (`alembic.ini`, `migrations/env.py`, две ревизии) и `scripts/init-test-db.sql`.
- `GET /healthz` реально проверяет БД (`SELECT 1` через `make_db_probe`) и отвечает `ok`/`degraded`, всегда со статусом 200.

---

## Partially implemented

**Слой перехвата и интеграции с харнессами.** Каталог `adapters/` содержит **только** `README.md` — ни одной подпапки харнесса (`opencode/`, `claude-code/`, `codex/`, `kilo/`), ни строчки кода. Есть эталонный клиент `contracts/hook_client.py`, но нигде в репозитории нет его регистрации: ни `.claude/settings.json` с описанием хука, ни плагина OpenCode, ни инструкции по установке в конкретный харнесс. Точка врезки продемонстрирована, но в реальный агент не поставлена. Evidence: `ls adapters/` → один файл; поиск `PreToolUse` / `tool.execute.before` по `*.json`, `*.js`, `*.ts`, `*.toml` даёт только упоминания в документации.

**Обработка `deny` и `ask` на стороне харнесса.** Шаблон `contracts/deny_message_template.md` существует, но ни один код его не подставляет — `hook_client.py` печатает сырой JSON ответа и возвращает код выхода. Ветка `ask` («штатный диалог подтверждения») кода не имеет вовсе: это обязанность адаптера, которого нет.

**Поведение при недоступности сервиса.** `adapters/README.md` описывает настройку `on_unavailable: ask | deny`. В `hook_client.py` такой настройки нет: недоступность всегда даёт `ask`/3. Опция описана, но не реализована.

**Benchmark.** В рабочем дереве отслеживаются шесть файлов: `README.md`, `CLAUDE.md`, `config.py`, `client/security_service.py`, `reporting/report.py`, `tests/test_reporting.py`. Три Python-модуля из четырёх импортируют пакеты, которых в дереве нет (`schemas.case`, `schemas.result`), поэтому **пакет не импортируется и запустить его нельзя**. Отсутствуют: `cli.py`, `schemas/`, `dataset/`, `evaluator/`, `runner/`, `storage/`, `tools/mock_agentgate.py`, `attacks/taxonomy.md`, все 75 YAML-кейсов `attacks/cases/`, `pyproject.toml`, `uv.lock`, `tests/conftest.py` и восемь из девяти тестовых модулей. Evidence: `git ls-files benchmark/`; `python -c "import reporting.report"` → `ModuleNotFoundError: No module named 'schemas'`.

**Состояние сессии в проде — только в памяти процесса.** `__main__.build_app` собирает `InMemorySessionStateStore` и один раз при старте подгружает в него сессии и валидные записи allow-кэша из Postgres. Дальше горячий путь читает и пишет только память; в Postgres состояние уходит фоновой задачей после ответа. Персистентность есть, но общего между процессами состояния нет — при нескольких воркерах uvicorn или нескольких инстансах счётчики эскалации и allow-кэш расходятся.

**Атрибуция решения к API-ключу.** По ключу обновляется только `last_used_at`. `key_id` не попадает ни в `DecisionRow`, ни в JSONL. Прямо зафиксировано в docstring `ApiKeyRow` (`service/agentgate/store/models.py`) как невыполненный пункт спеки.

**Модуль пакетов / slopsquatting.** `service/agentgate/stage1/packages.py` — заглушка: функция `check_packages` всегда возвращает `None`, docstring прямо говорит «Slot for the slopsquatting / package module. Always passes in v1.» Место в каскаде занято, проверки нет.

**Поле профиля `rules`.** Объявлено в `Profile` (`profiles/schema.py`) как `list[dict]` и присутствует в `default-dev.yaml` как `rules: []`, но **не читается нигде** — поиск `.rules` по `service/agentgate/` и `service/tests/` не даёт ни одного использования. Оператор может написать правила, и они молча не сработают.

**`/healthz` и LLM.** Ответ всегда содержит `"llm": null` — доступность модели ступени 2 не проверяется ни при старте, ни по запросу. Evidence: `api/app.py`, обработчик `healthz`.

**Более строгая посадка API-ключей.** Спека `api-keys.md` требует, чтобы на non-localhost bind статический токен игнорировался и принимались только выданные ключи. В коде ключи **аддитивны**: токен принимается наравне с ними. Отклонение сознательное и задокументировано в docstring `api/deps.py`.

---

## In progress

**Реинтеграция бенчмарка после отката.** Полный код бенчмарка (111 файлов, 8771 строка) был добавлен коммитом `4ec0f71`, затем целиком откачен коммитом `8e2cb5f` («Revert "--added codebase of the benchmark"»), после чего слияние `547220f` вернуло в дерево только шесть файлов. Работа явно не завершена: оставшиеся модули ссылаются на удалённые, `__init__.py` в пакетах `client/`, `reporting/`, `tests/` отсутствуют, `pyproject.toml` бенчмарка (в котором задавался `pythonpath = ["."]`, необходимый для импортов по голому имени) удалён вместе с остальным.

**Документация бенчмарка разошлась с кодом.** `benchmark/CLAUDE.md` описывает команды `uv run pytest` («121 unit tests»), `uv run python cli.py validate|benchmark|run|runs|report` и `uv run python tools/mock_agentgate.py` — ни одного из этих файлов в дереве нет. Там же написано «`service/` is not implemented yet, so end-to-end runs currently go through [мок]», что противоречит фактическому состоянию сервиса. Документ описывает состояние до отката.

**Набор документов `docs/project-context/`.** Каталог не отслеживается git (`git status` → `?? docs/project-context/`), содержит кейс хакатона, два исследования конкурентов, сводку, продуктовые заметки и целевую архитектуру. Это активно наполняемый слой контекста проекта, а не зафиксированное состояние.

---

## Target architecture components not yet implemented

Ниже — элементы `04_architecture` (§3, §4), для которых в кодовой базе нет ни реализации, ни подключения. Проверено поиском по `service/agentgate/`, `contracts/`, `benchmark/`, `adapters/` (совпадений нет).

| Компонент целевой архитектуры | Состояние в коде | Где зафиксировано намерение |
|---|---|---|
| **Context Guard** целиком: Provenance, Sensitive Data Detection, Prompt Injection Detection, Validation, Data Tagging | Отсутствует. Сервис не видит источники контекста и не участвует в их фильтрации. Ни одного упоминания в коде | Спека `docs/superpowers/service/specs/v4-context-guard.md`, статус в `context-versions-roadmap.md` — «спроектировать» |
| **Data Sources → Context Guard → AI Agent** (данные идут не напрямую в агента) | Отсутствует. Единственный вход сервиса — одно предложенное действие + последний запрос пользователя | `target_architecture.jpg`; граница v1 задана спекой сознательно |
| **Action Analyzer → Expected Impact** как явная величина | Отсутствует. Воздействие нигде не вычисляется и не хранится; косвенно учитывается правилами и LLM | `target_architecture.jpg` |
| **Security Decision → SAFE RECOVERY** как отдельная ветвь | Отсутствует. Исходов три (`allow` \| `deny` \| `ask`), не четыре. Поле `suggest` в `DecideResponse` несёт текст безопасной альтернативы, но это часть `deny`/`ask`, а не самостоятельный маршрут | `api/schemas.py: DecisionKind` |
| **User Decision** (Approve / Reject / Comment) | Отсутствует. Делегировано харнессу; харнесс-адаптера нет | `adapters/` — только README |
| **Stop** (терминальный узел после BLOCK) | Отсутствует. Сервис только возвращает `deny`; остановку исполняет харнесс | `contracts/deny_message_template.md` |
| **Session State / Audit** в полном объёме: Files, Secrets, Destinations, Uploads, Cost, Risk Score | Реализованы только `Actions` (счётчики решений, окно последних 50) и `Audit Logs` (Postgres + JSONL). Ни файлов, ни секретов, ни назначений, ни выгрузок, ни стоимости, ни агрегированного risk score в `SessionState` нет | `session/state.py`: поля `deny_consecutive`, `deny_total`, `decisions_total`, `recent` |
| **Параллельные проверки** (НФТ левой рамки схемы) | Отсутствует. `Gate.decide` строго последовательный; ступень 2 вызывается только после того, как ступень 1 вернула `None` | `pipeline.py` |
| **История диалога** (v2) | Отсутствует. В `DecideRequest` нет полей под историю; промпт ступени 2 — закрытый список без истории | `specs/v2-dialogue-context.md` |
| **Оценка `tool-result`** (v3), `direction: in` | Отсутствует. Есть только PreToolUse-направление | `specs/v3-tool-result-evaluation.md` |
| **Вердикт `mask`** и переписанный вывод инструмента | Отсутствует | `specs/adapter-contract-gap-analysis.md` |
| **Идемпотентность** по `(harness, session, call_id, direction)` | Отсутствует. Повторный `decide` создаст вторую строку решения и дважды сдвинет счётчики сессии | `specs/adapter-contract-gap-analysis.md`, раздел «Идемпотентность» |
| **Панель / UI, override, обучение** | Отсутствуют; в v1 не планировались | `CLAUDE.md`, раздел «Не в v1» |

---

## Current end-to-end flow

Фактический поток, собранный по коду. Всё, чего нет в этом списке, в потоке не участвует.

1. **Действие возникает в харнессе.** Харнесс должен вызвать хук перед исполнением инструмента. В репозитории такой вызов настроен только в одном месте — e2e-тесте, который запускает `contracts/hook_client.py` как подпроцесс с JSON хука на stdin. Установленного в реальный харнесс адаптера нет.
2. **Клиент отображает хук в запрос.** `hook_client.to_request()` определяет формат (`tool_name` → Claude Code, `sessionID` → OpenCode), выбирает `tool`, кладёт команду в `raw` или путь в `args.paths`, добавляет `cwd`, `session_id`, `user_request` (из флага или переменной окружения), `metadata`. Отправляет `POST /v1/decide` с `Authorization: Bearer …`, если токен задан.
3. **Авторизация.** `require_token` сверяет bearer со статическим токеном либо, если он не подошёл, проверяет его как выданный API-ключ (с кэшем в памяти процесса на 45 с). Не совпало — 401; это единственный не-200 ответ.
4. **Разбор тела вручную.** Невалидный JSON или невалидная схема — HTTP 200, `ask`, `stage: 0`, `rule_id="api.invalid-request"`. Дальше не идёт.
5. **Профиль и модель.** Неизвестный `profile_id` или неизвестное имя модели — HTTP 200, `ask`, `stage: 0`, соответствующий `rule_id`. Профиль копируется с подставленным воркспейсом: `detect_workspace` поднимается от `cwd` до ближайшего каталога с `.git`.
6. **Нормализация.** Для `tool: shell` строка `raw` разбирается `bashlex` в список `SimpleCommand` с редиректами, путями, доменами и флагами. Для `file_read`/`file_write` резолвятся `args.paths`, для `network` — нормализуются домены, для `mcp_call` — переносится блок `args.mcp`. Любая ошибка разбора → `flags.unparseable=True` с пустыми списками. `args.paths`/`args.domains` для `tool: shell` игнорируются: пути и домены сервис извлекает сам.
7. **Allow-кэш.** Если пришёл `session_id`, считается ключ `SHA-256(profile_hash + action_hash + user_request)` и ищется в памяти процесса. Попадание → немедленный ответ `allow`, `stage: 0`, `rule_id: "cache"`, `cached: true`; решение всё равно записывается.
8. **Ступень 1.** Если действие парсибельно, последовательно выполняются hard-deny → проверка профиля → allowlist → заглушка пакетов. Первый не-`None` результат замыкает цепочку. Замер латентности этой ступени попадает в `latency_ms.stage1`; тест держит p50 нормализации + ступени 1 в пределах 1 мс.
9. **Ступень 2.** Вызывается только если ступень 1 вернула `None` или действие непарсибельно. Непарсибельное действие отклоняется до построения промпта: `ask` без обращения к LLM. Иначе строится закрытый промпт (профиль, prose-слоты, `[TASK]`, `[ACTION]`, `[FLAGS]`, `[STAGE1]`), делается **один** HTTP-запрос к OpenAI-совместимому API с таймаутом из профиля и `response_format: json_schema`. Ответ валидируется в `ClassifierOutput`; `A/D/U` → `allow/deny/ask`. Таймаут, не-200, невалидный JSON, невалидная схема, пустой ответ и любое иное исключение → `ask`. Ретраев нет.
10. **Эскалация.** Если есть состояние сессии, решение не hard-deny и не `ask`, и сработало правило (3 отказа подряд либо 10 отказов из последних 50) — решение заменяется на `ask` с `rule_id="escalation"`, счётчик отказов и окно обнуляются. Hard-deny сюда не попадает по условию `not hard`.
11. **Запись состояния и кэша.** Решение учитывается в `SessionState`; `allow` кладётся в память allow-кэша.
12. **Ответ.** HTTP 200 с `decision`, `reason`, `suggest`, `stage`, `rule_id`, `model`, `latency_ms`, `cached`, `decision_id`. Любое исключение, дошедшее до этой точки, конвертируется в `ask` с `rule_id="api.internal-error"`.
13. **Персистентность — после ответа.** Фоновой задачей пишется строка JSONL, затем в Postgres: сначала upsert сессии, потом строка решения, потом (для некэшированного `allow`) строка allow-кэша — в этом порядке из-за внешних ключей. Любая ошибка логируется и глотается.
14. **Обратно в харнесс.** `hook_client.py` печатает JSON ответа и возвращает код выхода `0`/`2`/`3`. Что харнесс делает с этим кодом — вне репозитория: рендеринг `deny`-шаблона, диалог подтверждения на `ask` и остановка действия кодом не покрыты.

---

## Known limitations

Ограничения, подтверждаемые кодом или прогоном.

1. **41 из 509 тестов падают на Windows-хосте.** Все падения — расхождение разделителя пути: `resolve_path("./dist", "/home/u/repo")` возвращает `\home\u\repo\dist`, тесты ожидают POSIX-строку. Затронуты `test_normalize_paths`, `test_normalize_init`, `test_normalize_shell`, `test_stage1_chain`, `test_stage1_hard_deny`, `test_stage2_prompt`. Это не только тестовая проблема: `os.path.normpath` в `Profile.resolved_allowed_paths()` и `is_within` работают в разделителях хоста, поэтому проверки путей на Windows-хосте ведут себя иначе. Целевая среда — Linux в контейнере; по `docs/reports/final-config.md` на POSIX-хосте с поднятой БД прогон давал 507 passed / 0 failed.
2. **Состояние сессии живёт в памяти процесса.** Несколько воркеров uvicorn или несколько инстансов — независимые счётчики эскалации и независимые allow-кэши. Postgres используется как журнал и как источник восстановления при старте, но не как общее состояние на горячем пути.
3. **Отзыв API-ключа не мгновенный и не единый на сервис.** Кэш проверки — per-process, TTL 45 с; отозванный ключ остаётся принятым до истечения TTL в каждом воркере независимо.
4. **`GET /v1/decisions` не разграничен по ключам.** Любой действующий bearer читает всю ленту решений целиком, включая `raw` (сырую команду), `user_request` и `metadata`. Разделения по владельцу ключа нет.
5. **Решение теряется при падении между ответом и персистентностью.** И JSONL, и Postgres пишутся в `BackgroundTasks` после отправки ответа; краш процесса в этом окне оставляет решение неучтённым. Это сознательный размен латентности на полноту журнала.
6. **Нет идемпотентности.** Повтор `decide` (например, после сетевого таймаута у клиента) создаёт вторую строку решения и второй раз двигает счётчики сессии, приближая эскалацию, хотя действие было одно.
7. **Ступень 2 без ретраев и с одним таймаутом (8000 мс в поставляемом профиле).** При недоступности провайдера всё, что не решила ступень 1, становится `ask` — сервис остаётся безопасным, но friction резко растёт.
8. **Allow-кэш чувствителен к тексту запроса пользователя.** `user_request` входит в ключ кэша, поэтому та же команда при иначе сформулированном запросе кэш не переиспользует.
9. **Конфликт fail-open / fail-closed с контрактом адаптера не решён.** Сервис жёстко fail-closed изнутри, контракт Gate↔Guard по умолчанию fail-open на клиенте. Три варианта решения описаны в `specs/adapter-contract-gap-analysis.md`, выбор владельцем продукта на момент анализа не сделан. Наш собственный `hook_client.py` fail-closed.
10. **`AGENTGATE_TOKEN` на non-localhost принимается наравне с ключами**, вопреки более строгой формулировке `specs/api-keys.md`. Отклонение сознательное и задокументировано.
11. **Модуль пакетов — заглушка.** Typosquatting/slopsquatting ловится только ступенью 2, детерминированной проверки нет.
12. **Поле профиля `rules` игнорируется.** Молчаливо: ни ошибки загрузки, ни предупреждения.
13. **`/healthz` не проверяет LLM** — поле `llm` всегда `null`.
14. **CI в репозитории нет.** Ни `.github/`, ни иных конфигураций пайплайна; тесты и деплой запускаются человеком вручную.
15. **Бенчмарк не запускается.** См. Partially implemented: измеренных цифр ASR / Utility / FP / Friction / Latency в репозитории нет, воспроизвести их сейчас нечем.
16. **Целевой сервер и факт развёртывания по репозиторию не проверяемы.** `Makefile` резолвит хост через alias `~/.ssh/config` оператора, никаких адресов в репозитории нет — `Status unclear from repository`.

---

## Demo-ready functionality

Что можно надёжно показать на промежуточной демонстрации, с указанием, чем это подтверждается.

**Живой сервис из одной команды.** `cd service && docker compose up -d --build` поднимает Postgres и гейт, миграции применяются автоматически в `CMD`. `GET /healthz` отвечает `{"status":"ok","db":true,"llm":null}`.

**Три исхода через curl.** `contracts/curl-examples.md` содержит готовые вызовы по всем пяти видам `tool`: безопасная команда → детерминированный `allow` на ступени 1; `curl … | sh` → `deny` с `rule_id="hard-deny.pipe-exec"` без обращения к LLM; неоднозначная команда → уход на ступень 2. Видно, что `deny` и `ask` приходят с HTTP 200, а не как ошибка.

**Скорость детерминированной ступени.** `uv run pytest tests/test_stage1_latency.py` — p50 нормализации плюс ступени 1 ≤ 1 мс на 200 прогонах реальных команд. В ответе те же цифры видны как `latency_ms.stage1`.

**Устойчивость hard-deny к обфускации.** Табличные тесты `tests/test_stage1_hard_deny.py` (24 теста) покрывают `$(…)`, `eval`, переменные, base64, heredoc, обёртки `env`/`sudo`/`xargs`. Показывается прогоном.

**Ступень 2 со structured output.** e2e-сценарий `npm install lodahs` → `deny` от классификатора с `suggest: "npm install lodash"`. Подтверждается `tests/e2e/test_e2e.py::test_llm_deny_and_log`, который заодно проверяет, что решение попало в JSONL.

**Fail-closed демонстрируется отключением LLM.** Достаточно указать в профиле недоступный `base_url`: всё, что не решила ступень 1, приходит как `ask` с `reason: "classifier unavailable: …"`. `allow` по ошибке недостижим ни по одному пути — на это есть отдельные тесты в `tests/test_stage2_run.py` и `tests/test_api.py`.

**Эскалация по сессии.** Три отказа подряд с одним `session_id` → следующий ответ `ask` с `rule_id="escalation"`. Покрыто `tests/test_pipeline.py` и `tests/test_session.py`.

**Allow-кэш.** Повтор того же действия в той же сессии → `allow`, `stage: 0`, `cached: true`, `latency_ms.total` близка к нулю.

**Аудит.** `GET /v1/decisions?session_id=…` отдаёт ленту решений с пагинацией; тот же поток дублируется в JSONL. Хорошо смотрится рядом с демонстрацией — видно, что каждое решение измеримо.

**Выдача и отзыв ключей.** `python -m agentgate keys create --label demo` печатает ключ ровно один раз, `keys list` показывает его без плейнтекста, `keys revoke` отзывает. Демонстрирует, что ключи есть, и что плейнтекст нигде не хранится.

**Интеграция с харнессом — только в виде эталонного клиента.** Показывать следует именно так: JSON хука Claude Code на stdin `contracts/hook_client.py`, решение и код выхода на stdout. Демонстрация «AgentGate внутри работающего Claude Code / OpenCode» **не готова**: адаптера, который регистрирует хук в харнессе, в репозитории нет.

**Что показывать нельзя.** Цифры бенчмарка (пакет не импортируется), Context Guard и защиту от prompt injection в контексте (не реализованы), ветку SAFE RECOVERY и диалог подтверждения на стороне харнесса (кода нет), работу в многоворкерном/многоинстансном режиме (состояние сессии не общее).

---

## Evidence

Опорные точки для каждого существенного вывода.

**Сервер решений**
- HTTP-граница и fail-closed: `service/agentgate/api/app.py` — `create_app`, обработчик `decide`, функция `_ask`, фоновая `persist`.
- Конвейер: `service/agentgate/pipeline.py` — `Gate.decide`, `Gate._finish_early`, `Gate._record`, константы `_NOTE_PASSED` / `_NOTE_SKIPPED`.
- Схемы контракта: `service/agentgate/api/schemas.py` — `Tool`, `DecisionKind`, `DecideRequest`, `DecideResponse`, лимиты `USER_REQUEST_MAX_CHARS` / `RAW_MAX_BYTES` / `METADATA_MAX_BYTES`.
- Нормализация: `service/agentgate/normalize/shell.py` (`normalize_shell`, `PATH_COMMANDS`, `_WRAPPER_CMDS`, `_MAX_NESTING_DEPTH`), `normalize/model.py` (`NormalizedAction.action_hash`), `normalize/paths.py` (`resolve_path`, `is_within`, `matches_any`, `looks_unresolved`), `normalize/domains.py`.
- Ступень 1: `stage1/chain.py` (`CHECKS`, `run_stage1`), `stage1/hard_deny.py` (`RULES`, `_rule_exfil`, `_rule_pipe_exec`, `_rule_destructive`, `_rule_protected_write`, `_rule_privilege`, `_rule_git_force`, `_deny`, `_ask`), `stage1/profile_check.py` (`MUTATING`, `check_profile`), `stage1/allowlist.py` (`READONLY`, `GIT_READONLY`, `check_allowlist`), `stage1/argv_paths.py` (`command_argv_paths`), `stage1/types.py` (`Stage1Decision.hard`).
- Заглушка модуля пакетов: `service/agentgate/stage1/packages.py`, `check_packages` → `None`.
- Ступень 2: `stage2/prompt.py` (`build_system_prompt`, `build_user_message`, `_j`), `stage2/client.py` (`LLMClient.classify`, `Stage2Error`), `stage2/schema.py` (`ClassifierOutput`, `RESPONSE_JSON_SCHEMA`), `stage2/run.py` (`run_stage2`, `_MAP`).
- Сессия и эскалация: `session/state.py` (`SessionState`, `RECENT_MAXLEN`), `session/escalation.py` (`should_escalate`), `session/memory.py` (`InMemorySessionStateStore`), `session/cache_key.py` (`allow_cache_key`).
- Хранилище: `store/models.py` (`SessionRow`, `DecisionRow`, `AllowCacheRow`, `ApiKeyRow` + его docstring про отсутствующую атрибуцию `key_id`), `store/repo.py` (`DecisionRepo`, `SessionRepo`), `store/db.py`, `migrations/versions/0001_init.py`, `0002_api_keys.py`.
- Авторизация и ключи: `api/deps.py` (`make_require_token`, `_KeyVerifier`, `_touch_last_used_safe`), `store/keys.py` (`generate_key`, `hash_key`, `ApiKeyRepo`), `cli.py` (`run_keys_cli`).
- Конфигурация и профили: `config.py` (`Settings`, `_split_bind`, `validate_token_for_bind`), `profiles/schema.py` (`Profile.profile_hash`, `resolved_allowed_paths`, `resolved_protected_paths`, неиспользуемое поле `rules`), `profiles/loader.py` (`interpolate_env`, `detect_workspace`, `with_workspace`), `profiles/default-dev.yaml`.
- Сборка процесса: `service/agentgate/__main__.py` (`build_app`, `make_db_probe`, `main`) — здесь видно, что в прод идёт `InMemorySessionStateStore` с восстановлением из Postgres на старте.

**Слой перехвата**
- `adapters/README.md` — единственный файл каталога; описывает `on_unavailable: ask | deny`, которого нет в коде.
- `contracts/hook_client.py` — `to_request`, `EXIT`, обработка stdin и сетевых ошибок.
- `contracts/openapi.yaml`, `contracts/decide_request.schema.json`, `contracts/decide_response.schema.json`, `contracts/deny_message_template.md`, `contracts/curl-examples.md`.
- `service/scripts/export_contracts.py`, `service/scripts/export_openapi.py`, `service/tests/test_contracts.py`.

**Benchmark**
- `git ls-files benchmark/` — шесть файлов.
- `benchmark/reporting/report.py:11` — `from schemas.result import …` (пакета нет); `benchmark/client/security_service.py:22-24` — `from config import …`, `from schemas.case import …`.
- История: `git show --stat 4ec0f71` (111 файлов добавлено), `git log --oneline` → `8e2cb5f Revert "--added codebase of the benchmark"`, слияние `547220f`.
- `benchmark/CLAUDE.md` — раздел «Commands» перечисляет `cli.py`, `tools/mock_agentgate.py`, «121 unit tests»; там же утверждение «`service/` is not implemented yet».
- Поддержка бенчмарка на стороне сервиса: поле `model` в `DecideRequest`; `ModelsConfig.model_config_for`; индекс `ix_decisions_metadata` (GIN) в `store/models.py`; фильтры `session_id` / `model` в `DecisionRepo.list`.

**Инфраструктура**
- `service/Dockerfile`, `service/docker-compose.yml`, `service/Makefile` (`check-clean`, `deploy`, `rollback`), `service/alembic.ini`, `service/migrations/env.py`, `service/scripts/init-test-db.sql`.
- Отсутствие CI: в корне нет `.github/`, `.gitlab-ci.yml` или аналогов.
- Спека деплоя: `docs/superpowers/service/specs/deploy.md`; отчёт о проверке контейнера: `docs/reports/task-12-docker-e2e.md`; итоговый конфиг и базовая линия тестов: `docs/reports/final-config.md`.

**Целевые компоненты, которых нет**
- Поиск по `service/agentgate/`, `service/profiles/`, `contracts/`, `benchmark/`, `adapters/` (без `.venv`) по строкам `context.guard`, `provenance`, `posttooluse`, `tool_result`, `toolresult`, `risk_score`, `safe.recovery` — совпадений ноль.
- Намерения зафиксированы в `docs/superpowers/service/specs/context-versions-roadmap.md` (таблица v1→v4, статус v2/v3/v4 — «спроектировать»), `v2-dialogue-context.md`, `v3-tool-result-evaluation.md`, `v4-context-guard.md`, `adapter-contract-gap-analysis.md`.

**Прогоны, выполненные при подготовке документа**
- `cd service && uv run pytest -q` → `41 failed, 425 passed, 43 skipped` (Windows; 43 skip — из-за незаданного `AGENTGATE_TEST_DB_URL`).
- `cd service && uv run pytest tests/test_normalize_paths.py::test_resolve_relative_and_home -q` → `assert '\\home\\u\\repo\\dist' == '/home/u/repo/dist'` — подтверждение природы падений.
- `cd benchmark && python -c "import reporting.report"` → `ModuleNotFoundError: No module named 'schemas'`.
