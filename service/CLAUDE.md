# service/ — правила работы

## Границы (жёсткое правило)

Разрешено писать **только** в:

- `service/` — основная зона работы;
- `contracts/` — JSON-схемы, `openapi.yaml`, `deny_message_template.md`, `hook_client.py`;
- `docs/reports/` — отчёты по задачам, decisions-log и контрактные заметки (единое место);
- `CLAUDE.md` в корне — только когда задача прямо этого требует.

Всё остальное вне зоны: `docs/` (кроме `docs/reports/`), `adapters/`, `benchmark/`, корневые `README.md` и `.gitignore`. Не создавать, не редактировать, не удалять в них ничего.

Следствия:
- Никогда не запускать `git add -A`, `git add .`, `git commit -a`, `git stash` или `git checkout .`. Только явные пути, и коммит через `git commit --only <пути>` — индекс общий, обычный коммит забирает и чужое подготовленное.
- В корне репозитория лежит незакоммиченная работа пользователя (`README.md`, `.gitignore`, `docs/*`) и служебный каталог `.superpowers/`. Всё это не наше — не трогать и не коммитить.
- `service/.env` содержит боевые креды. Не читать, не печатать, не перемещать, не коммитить.
- Если задача требует файла вне разрешённого списка — не делать её молча, а сообщить и остановиться.

## Что здесь строится

Ядро AgentGate: FastAPI-сервис `POST /v1/decide`, каскад «детерминированная ступень 1 → LLM-ступень 2».

- Спека: `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md` (читать можно, менять нельзя)
- Дорожная карта версий (v1→v5): `docs/superpowers/service/specs/context-versions-roadmap.md`. v1–v4 идут по оси «сколько контекста видит сервис», v5 — по оси независимости от провайдера модели. **Реализован v1** (одно действие + последнее сообщение пользователя; форма кода — v1.5, карта ниже), **поверх него v2**:
  спека `docs/superpowers/service/specs/2026-09-04-agentgate-v2-design.md` — история диалога в запросе, её дайджест в ключе allow-кэша, блок `[HISTORY]` в промпте, повтор по `Idempotency-Key`, поле `protocol`. Закрытый список содержимого промпта: системный промпт, профиль, prose-слоты, `[TASK]`, `[HISTORY]`, `[ACTION]`, `[FLAGS]`, `[STAGE1]`. Расширять только вместе с docstring `classify/prompt.py` и этим файлом.
  **Реализован v3**: спека `docs/superpowers/service/specs/2026-09-04-agentgate-v3-rules-and-inspect-design.md`, план `docs/superpowers/service/plans/2026-09-04-agentgate-v3-rules-and-inspect.md`, отчёт `docs/reports/task-22-v3-rules-and-inspect.md`. Добавлены `rules` и `call_id` в `DecideRequest` и маршрут `POST /v1/inspect` (детекторы → маска/`drop` → классификатор по флагу). Закрытый список содержимого промпта inspect (с v4): системный промпт, профиль, prose-слоты, `[TASK]`, `[HISTORY]` (только при непустом диалоге), `[PROVENANCE]`, `[FLAGS]`, `[SEGMENTS]`, `[CANDIDATES]` (только при кандидатах по энтропии) — всё через `render.j()`, сегменты и строка провенанса из текста **после** редакции секретов, `metadata` и рассуждения агента — никогда. Расширять только вместе с docstring `inspect/classify.py` и этим файлом.
  **Реализован v4** (Context Guard): спека `docs/superpowers/service/specs/2026-09-05-agentgate-v4-context-guard-design.md`, план `docs/superpowers/service/plans/2026-09-05-agentgate-v4-context-guard.md`, отчёт `docs/reports/task-24-v4-context-guard.md`. Секреты редактируются по значению до сборки промпта (`inspect.secret`, действие `redact`), классификатор отвечает спанами строк, которые сервер валидирует против отправленных сегментов и применяет той же маской (`inspect.semantic`); ключ кэша inspect включает дайджесты задачи, истории и политику кандидатов; `raw` записи — текст после редакции.
- API-ключи (выдача, хранение, проверка): `docs/superpowers/service/specs/api-keys.md`.
- Авто-деплой на сервер (`make deploy` по SSH): `docs/superpowers/service/specs/deploy.md`.
- План v1: `docs/superpowers/service/plans/2026-09-03-agentgate-v1.md`. Ревью и план рефакторинга v1.5: `docs/reports/code-quality-review-and-refactor-plan.md`, отчёт по нему — `docs/reports/task-v1.5-solid-refactor.md`.

## Карта модулей (v1.5)

Таблица ниже — где что лежит, а не утверждение о зависимостях. Три известных исключения из «сверху вниз», которые стоит знать до того, как о них споткнёшься:

- `domain/` **не** дно стека: `DecisionKind` живёт в `api/schemas.py`, поэтому `domain/`, `rules/`, `engine/`, `session/` и `store/` транзитивно тянут pydantic-модуль HTTP-схем.
- `shell/` и `normalize/` ссылаются друг на друга: `shell/secrets.py` и `shell/paths.py` импортируют `normalize/paths.py`, а тот импортирует `shell/secrets` **внутри функции**, чтобы цикл не замкнулся на импорте. Обходной путь описан в docstring обоих модулей. Два `paths.py` в двух пакетах с трафиком в обе стороны — незакрытый долг, а не замысел.
- `engine/` знает только протоколы, `bootstrap.py` знает всех — вот это верно.

| Пакет | Что там лежит |
|---|---|
| `domain/` | Чистые типы без I/O. `verdict.py` — `Verdict`, единственный тип исхода. `policy.py` — `Policy` (профиль, привязанный к одному workspace, пути разрешены один раз); сам `Profile` живёт в `profiles/schema.py`. `session.py` — `SessionState`, протоколы `SessionStateStore` и `RestorableSessionStateStore`. `dialogue.py` — `Dialogue` (ходы как прислал харнесс, дайджест, усечение `fit`). `replay.py` — `Replay` (ответ по проводу плюс личность запроса, которому он принадлежит) и протокол `ReplayStore`; повтор отдаётся только при совпадении дайджеста всего запроса без `metadata`. `client_rules.py` — `ClientRules` (шаблоны пользователя, разделённые по форме на путевые и командные, раскрытие `~` по `HOME`, дайджест без учёта порядка и дублей). `inspect_cache.py` — протокол `InspectCache[T]`, generic, чтобы не заводить второе ребро `domain → engine`. `usage.py` — `Usage` (токены стадии 2, разобранные из сырого ответа провайдера) и `cost_amount`. |
| `shell/` | Синтаксис и семантика shell без политики. `commands.py` — `CommandSpec` и таблица `COMMANDS` (единственный источник знания «что это за команда»), `argv.py` — `ParsedArgv`, `wrappers.py` — `sudo`/`env`/`xargs` и разрешение эффективного argv, `paths.py` — `command_paths(argv, cwd, role)`, `secrets.py` — один список шаблонов секретных файлов. |
| `normalize/` | `DecideRequest` → `NormalizedAction` (`model.py`, `shell.py`, `paths.py`, `domains.py`). Решение по сырой строке запрещено везде — только по `NormalizedAction`. |
| `rules/` | Ступень 1. `base.py` — `Rule` (Protocol) и `RuleChain`. `chain.py` — `STAGE1`, порядок правил и есть вся приоритетная политика ступени. По модулю на правило: `unparseable.py`, `hard_deny/` (шесть правил + `wrapper_unresolved.py` + общий `shared.py`), `profile_paths.py`, `profile_domains.py`, `allowlist.py`, `packages.py` (слот slopsquatting, в v1 всегда молчит). `client_rules.py` — `ClientRulesRule`, один класс на трёх позициях цепочки (`client.deny` сразу после hard-deny, `client.ask` после запретов профиля, `client.allow` перед allowlist). |
| `inspect/` | Каскад inspect. `detectors.py` — `Detector`, `Action` (`mask`/`clean`/`redact`), `Finding` (диапазон строк с `rewritten`, `candidate_key`, `kind`, `confidence`), таблица детекторов (`INJECTION`, `PIPE_EXEC`, `ENCODED`, `INVISIBLE`) и `scan`; `hints`/`precheck` — дешёвые предпроверки перед регулярками. `chain.py` — `INSPECT_STAGE1`, порядок детекторов. `secrets.py` — формы секретов (`FORMS`), кандидаты по энтропии, провенанс-политика (`scan_secrets`, `entropy_candidates_allowed`, `redact_line`); список секретных путей берётся из `shell/secrets.py`. `mask.py` — `apply` с приоритетом `redact > clean > mask` на строке, диапазоны, `redacted_lines` (нумерация сохраняется), спаны и порог `drop` (более половины строк, только `mask`). `segments.py` — окна вокруг находок по `model_budget` профиля, то, что видит модель. `spans.py` — валидация спанов модели (границы, ширина, закрытый список видов, уверенность, только внутри отправленных сегментов, слияние пересечений). `reconcile.py` — капы ступени 2 и слияние находок ступени 1 со спанами модели, `unredact` только для кандидатов. `classify.py` — `InspectCase` (с сегментами), `ModelSpan`, `InspectOutput` (`verdict`, `spans`, `unredact`, `reason`), протокол `InspectClassifier`, `LLMInspectClassifier`, `InspectOutcome` без текста. |
| `classify/` | Ступень 2. `base.py` — `Classifier` (Protocol) и `ReviewCase` (единственный вход классификатора: действие, намерение, усечённый диалог, политика, заметка ступени 1), `llm.py` — `LLMClassifier` и `build_classifiers`, `prompt.py`, `render.py` (общие примитивы рендера промпта — `j`, `history_lines`, `system_prompt` — одни на decide и inspect), `schema.py`, `client.py` (HTTP к OpenAI-совместимому API; `StructuredOutput` — имя схемы, JSON-схема и модель, передаётся явно, без дефолта). |
| `engine/` | `gate.py` — `Gate`, только оркестрация. `decision.py` — `Decision` (исход вызова целиком) и `DecisionRecord` (плоская проекция: строка JSONL, строка Postgres, элемент `GET /v1/decisions` — одна форма, определённая один раз); `DecisionRecord.to_response()` строит `DecideResponse` для решений `Gate` — живое решение и повтор одной функцией; ранние отказы API собирает `_refuse` в `api/app.py`. `inspection.py` — `Inspection`, исход одного вызова inspect, с теми же двумя проекциями (`InspectResponse`, `DecisionRecord`). `inspector.py` — `Inspector`, оркестрация inspect: кэш → детекторы → маска → классификатор по флагу. `timings.py`. |
| `session/` | `memory.py` — in-memory store, `persistent.py` — он же плюс `restore()` из Postgres, `escalation.py`, `cache_key.py`, `replay.py` — `InMemoryReplayStore` и `PersistentReplayStore` (восстановление из Postgres при старте). `ttl_store.py` — `TtlStore`, общая механика TTL/sweep/cap для хранилища повторов и кэша inspect. `inspect_cache.py` — `InMemoryInspectCache`. `cache_key.py` — оба ключа, `allow_cache_key` и `inspect_cache_key`. |
| `store/` | `writer.py` — `DecisionWriter` (Protocol) и три реализации, `protocols.py` — `Stored` (то, что writer'у нужно от исхода, будь то `Decision` или `Inspection`: `to_record`, `allow_cache_entry`, `session_state`, `session_ref`), `repo.py`, `models.py` (в т.ч. столбец `cost` JSONB, добавленный миграцией `migrations/versions/0005_cost.py`), `mapper.py` (единственное место, знающее про `metadata_`), `keys.py`, `db.py`. |
| `api/` | `app.py` (`create_app`), `schemas.py` (в т.ч. `Cost` — токены и денежная стоимость вызова стадии 2, `for_model()` — общий конструктор из `Usage` и `ModelConfig`), `responses.py`, `deps.py` (аутентификация), `examples.py`, `openapi.py`. |
| `profiles/`, `log/` | Загрузка YAML-профилей (`schema.py::ModelConfig` — среди прочего `price_per_1m_input`/`price_per_1m_output`, оба или ни одного); JSONL-лог решений. |
| `bootstrap.py` | Единственный composition root: `build_service(settings, *, http, state_store, writer)`. `__main__.py`, `cli.py` и тестовые фабрики берут его. |

Тесты зеркалят исходники: `agentgate/foo/bar.py` → `tests/foo/test_bar.py`. Общие фабрики и фейки — только в `tests/factories.py`; импорт одного тестового модуля из другого запрещён.

## Куда добавлять

Рабочие примеры с кодом — раздел «Как добавить» в `service/README.md`. Коротко:

- **Правило ступени 1** — новый класс (`id`, `hard`, `evaluate(action, policy) -> Verdict | None`) в `agentgate/rules/`, строка в `STAGE1` (`agentgate/rules/chain.py`). `Gate` не меняется. Hard-deny — в `agentgate/rules/hard_deny/` и в `HARD_DENY_RULES`; дописывать в конец списка безопасно, там все правила жёсткие (`WrapperUnresolvedRule`, отвечающее `ask`, вынесено в цепочку именно поэтому).
- **Модель ступени 2** — если провайдер OpenAI-совместимый, это запись в `models.configs` профиля и ни строки кода. Иначе класс с протоколом `Classifier` и строка в `bootstrap.build_service`.
- **Хранилище сессий** — класс с протоколом `SessionStateStore` (плюс `preload`, если его надо восстанавливать при старте) и строка в `bootstrap.build_service`.
- **Хранилище повторов** — класс с протоколом `ReplayStore` (`agentgate/domain/replay.py`) и строка в `bootstrap.build_service`.
- **Приёмник решений** — класс с протоколом `DecisionWriter` и элемент списка в `CompositeDecisionWriter`.
- **Детектор inspect** — новый `Detector` в `agentgate/inspect/detectors.py` и строка в `INSPECT_STAGE1` (`agentgate/inspect/chain.py`). `Inspector` не меняется. Предпроверки (`hints`, `precheck`) обязаны быть консервативны: отсеивать только строку, которую шаблоны заведомо не совпадут.
- **Форма секрета** — строка в `FORMS` (`agentgate/inspect/secrets.py`); подсказки не строже регулярки, корпус ложных срабатываний и латентные корпуса зелёные.
- **Классификатор inspect** — класс с протоколом `InspectClassifier` (`agentgate/inspect/classify.py`) и строка в `bootstrap.build_service`. `classify` не бросает: сбой — это `InspectOutcome` с `error`.
- **Кэш вердиктов inspect** — класс с протоколом `InspectCache` (`agentgate/domain/inspect_cache.py`) и строка в `bootstrap.build_service`.
- **Команда** — строка в таблице `COMMANDS` (`agentgate/shell/commands.py`). Про readonly-ность таблица знает не всё: `rules/allowlist.py` дополнительно зашивает `echo`, голый `env` и `find` без `-delete`.

## Технические правила

- Python `>=3.12`, зависимости через `uv`. Все команды — из `service/`, вида `uv run …`.
- Код и комментарии — английский. Документация и README — русский. Идентификаторы API не переводятся.
- TDD обязателен: сначала падающий тест, потом реализация. Тест, который не падал до реализации, не считается тестом.
- Комментарии — только неочевидное «почему». Ссылок на задачи, PR, авторов, даты, «fix round N» в коде нет и быть не должно: эта история живёт в `docs/reports/`.
- Fail-closed по всему сервису: любая ошибка, таймаут, невалидный ответ или невалидный запрос → `ask` с HTTP 200. `allow` по ошибке невозможен; на каждый путь отказа есть тест.
- Решение по сырой строке команды запрещено везде; только по `NormalizedAction`.
- `deny` и `ask` не кэшируются; кэшируется только `allow`. Повтор по `Idempotency-Key` обслуживается в API-слое до `Gate`; `Gate.decide` о ключе не знает. Хранилище повторов на горячем пути обёрнуто: его отказ — «повтора нет» и «решение не повторяемо», но никогда 500 (5xx клиент читает как fail-open). Ступень 1 историю не получает: у `Rule.evaluate(action, policy)` нет для неё параметра.
- Запись в Postgres и JSONL — после отправки ответа, через `BackgroundTasks`. Ничего в `Gate.decide` не ждёт базу.
- Хранилище — только Postgres (asyncpg). SQLite не поддерживается.
- Ретраев к LLM нет: один вызов, один таймаут.
- Полный прогон перед коммитом (без переменной тесты, требующие Postgres, скипаются — этого недостаточно):
  ```bash
  cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
  ```
- После правок, способных задеть контракт: `uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts`. Пустой diff — доказательство, что публичный контракт не поехал.

## Отступления от плана

- `Settings.bind` валидируется при создании (pydantic `field_validator`) и требует форму `host:port` с IPv6-хостом в скобках (`[::1]:8400`), в отличие от кода в Step 4 плана — та версия падает с `ValueError` из `bind_port` при `AGENTGATE_BIND=localhost` и молча неверно парсит голый `::1`.
- `DenyWindow.count` и `DenyWindow.of_last` ограничены `ge=1`, и добавлен валидатор, отвергающий `count > of_last` — этого нет в коде Task 3 в плане. Без ограничений профиль с `of_last: 0` грузился, а срез `list(recent)[-0:]` в Python возвращает **весь** список вместо пустого окна: оператор, отключающий оконную проверку, молча получал проверку по всей истории. Комбинация `count > of_last` недостижима и молча выключала эскалацию, оставляя оператора в уверенности, что она работает.
- Поля `NormalizedAction` остались списками, а не кортежами: кортежи ломают `cmd.argv[:len(p)] == p` в `allowlist.py` и молча выключают `safe_prefixes` оператора. Неизменяемость обеспечена `frozen=True` на самих датаклассах.

## Отчёты

После каждой завершённой задачи — отчёт в `docs/reports/task-<N>-<slug>.md` на русском: что построено, доказательства TDD, находки ревью и как закрыты, принятые решения, что отложено.

## Коммиты

Коммит после каждой задачи, только явные пути (`git commit --only <пути>`). Сообщение заканчивается строкой:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```
