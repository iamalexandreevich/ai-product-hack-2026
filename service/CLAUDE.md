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
- Дорожная карта версий (v1→v5): `docs/superpowers/service/specs/context-versions-roadmap.md`. v1–v4 идут по оси «сколько контекста видит сервис», v5 — по оси независимости от провайдера модели. **Реализован v1**: одно действие + последнее сообщение пользователя, без истории диалога. Форма кода — v1.5 (карта ниже), поведение то же.
- **Реализован v2** (спека `docs/superpowers/service/specs/2026-09-04-agentgate-v2-design.md`): история диалога в запросе, её дайджест в ключе allow-кэша, блок `[HISTORY]` в промпте, повтор по `Idempotency-Key`, поле `protocol`. Закрытый список содержимого промпта: системный промпт, профиль, prose-слоты, `[TASK]`, `[HISTORY]`, `[ACTION]`, `[FLAGS]`, `[STAGE1]`. Расширять только вместе с docstring `classify/prompt.py` и этим файлом.
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
| `domain/` | Чистые типы без I/O. `verdict.py` — `Verdict`, единственный тип исхода. `policy.py` — `Policy` (профиль, привязанный к одному workspace, пути разрешены один раз); сам `Profile` живёт в `profiles/schema.py`. `session.py` — `SessionState`, протоколы `SessionStateStore` и `RestorableSessionStateStore`. `dialogue.py` — `Dialogue` (ходы как прислал харнесс, дайджест, усечение `fit`). |
| `shell/` | Синтаксис и семантика shell без политики. `commands.py` — `CommandSpec` и таблица `COMMANDS` (единственный источник знания «что это за команда»), `argv.py` — `ParsedArgv`, `wrappers.py` — `sudo`/`env`/`xargs` и разрешение эффективного argv, `paths.py` — `command_paths(argv, cwd, role)`, `secrets.py` — один список шаблонов секретных файлов. |
| `normalize/` | `DecideRequest` → `NormalizedAction` (`model.py`, `shell.py`, `paths.py`, `domains.py`). Решение по сырой строке запрещено везде — только по `NormalizedAction`. |
| `rules/` | Ступень 1. `base.py` — `Rule` (Protocol) и `RuleChain`. `chain.py` — `STAGE1`, порядок правил и есть вся приоритетная политика ступени. По модулю на правило: `unparseable.py`, `hard_deny/` (шесть правил + `wrapper_unresolved.py` + общий `shared.py`), `profile_paths.py`, `profile_domains.py`, `allowlist.py`, `packages.py` (слот slopsquatting, в v1 всегда молчит). |
| `classify/` | Ступень 2. `base.py` — `Classifier` (Protocol), `llm.py` — `LLMClassifier` и `build_classifiers`, `prompt.py`, `schema.py`, `client.py` (HTTP к OpenAI-совместимому API). |
| `engine/` | `gate.py` — `Gate`, только оркестрация. `decision.py` — `Decision` (исход вызова целиком) и `DecisionRecord` (плоская проекция: строка JSONL, строка Postgres, элемент `GET /v1/decisions` — одна форма, определённая один раз). `timings.py`. |
| `session/` | `memory.py` — in-memory store, `persistent.py` — он же плюс `restore()` из Postgres, `escalation.py`, `cache_key.py`. |
| `store/` | `writer.py` — `DecisionWriter` (Protocol) и три реализации, `repo.py`, `models.py`, `mapper.py` (единственное место, знающее про `metadata_`), `keys.py`, `db.py`. |
| `api/` | `app.py` (`create_app`), `schemas.py`, `responses.py`, `deps.py` (аутентификация), `examples.py`, `openapi.py`. |
| `profiles/`, `log/` | Загрузка YAML-профилей; JSONL-лог решений. |
| `bootstrap.py` | Единственный composition root: `build_service(settings, *, http, state_store, writer)`. `__main__.py`, `cli.py` и тестовые фабрики берут его. |

Тесты зеркалят исходники: `agentgate/foo/bar.py` → `tests/foo/test_bar.py`. Общие фабрики и фейки — только в `tests/factories.py`; импорт одного тестового модуля из другого запрещён.

## Куда добавлять

Рабочие примеры с кодом — раздел «Как добавить» в `service/README.md`. Коротко:

- **Правило ступени 1** — новый класс (`id`, `hard`, `evaluate(action, policy) -> Verdict | None`) в `agentgate/rules/`, строка в `STAGE1` (`agentgate/rules/chain.py`). `Gate` не меняется. Hard-deny — в `agentgate/rules/hard_deny/` и в `HARD_DENY_RULES`; дописывать в конец списка безопасно, там все правила жёсткие (`WrapperUnresolvedRule`, отвечающее `ask`, вынесено в цепочку именно поэтому).
- **Модель ступени 2** — если провайдер OpenAI-совместимый, это запись в `models.configs` профиля и ни строки кода. Иначе класс с протоколом `Classifier` и строка в `bootstrap.build_service`.
- **Хранилище сессий** — класс с протоколом `SessionStateStore` (плюс `preload`, если его надо восстанавливать при старте) и строка в `bootstrap.build_service`.
- **Приёмник решений** — класс с протоколом `DecisionWriter` и элемент списка в `CompositeDecisionWriter`.
- **Команда** — строка в таблице `COMMANDS` (`agentgate/shell/commands.py`). Про readonly-ность таблица знает не всё: `rules/allowlist.py` дополнительно зашивает `echo`, голый `env` и `find` без `-delete`.

## Технические правила

- Python `>=3.12`, зависимости через `uv`. Все команды — из `service/`, вида `uv run …`.
- Код и комментарии — английский. Документация и README — русский. Идентификаторы API не переводятся.
- TDD обязателен: сначала падающий тест, потом реализация. Тест, который не падал до реализации, не считается тестом.
- Комментарии — только неочевидное «почему». Ссылок на задачи, PR, авторов, даты, «fix round N» в коде нет и быть не должно: эта история живёт в `docs/reports/`.
- Fail-closed по всему сервису: любая ошибка, таймаут, невалидный ответ или невалидный запрос → `ask` с HTTP 200. `allow` по ошибке невозможен; на каждый путь отказа есть тест.
- Решение по сырой строке команды запрещено везде; только по `NormalizedAction`.
- `deny` и `ask` не кэшируются; кэшируется только `allow`.
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
