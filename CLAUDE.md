# CLAUDE.md — AgentGate (ai-product-hack-2026)

Читается первым. Актуальная спека v1: `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`. План: `docs/superpowers/service/plans/2026-09-03-agentgate-v1.md`. Исходные материалы (частично устарели, при расхождении права спека v1): `docs/base.md`, `docs/artifacts/`. Позиционирование: `docs/why-agentgate.md`.

Дорожная карта версий (v1→v5: контекст на v1–v4, независимость от провайдера модели на v5), принятая владельцем продукта: `docs/superpowers/service/specs/context-versions-roadmap.md`. Стоимость решения в ответе (из трёх запрошенных полей два уже есть, нужна только цена): `docs/superpowers/service/specs/response-cost-reporting.md`. Спека API-ключей: `docs/superpowers/service/specs/api-keys.md`. Спека деплоя: `docs/superpowers/service/specs/deploy.md`. Деплой v1.5 за HTTPS и подключение команды: `docs/superpowers/service/specs/2026-09-04-deploy-public-endpoint-design.md`, страница интегратора — `docs/connect.md`. Сверка с контрактом адаптера (Gate↔Guard) и расхождение по fail-open/fail-closed: `docs/superpowers/service/specs/adapter-contract-gap-analysis.md`.

## Что построено (v1 по функциям, v1.5 по форме кода)

Отдельный сервис между кодинг-агентом и ОС. `POST /v1/decide` получает одно действие плюс последний запрос пользователя и возвращает `allow | deny(reason, suggest) | ask`. Каскад: нормализация по AST → ступень 1 (hard-deny, профиль, allowlist, без LLM) → ступень 2 (LLM через OpenAI-совместимый API, structured output) → эскалация. Решения пишутся в Postgres и в JSONL после отправки ответа.

Архитектура после рефакторинга v1.5 (поведение то же; отчёт — `docs/reports/task-v1.5-solid-refactor.md`):

- **Один тип исхода.** `Verdict` (`service/agentgate/domain/verdict.py`) возвращают и правило, и классификатор, и allow-кэш, и ранний отказ API. `Decision` (`engine/decision.py`) — исход одного вызова целиком; `DecideResponse` и `DecisionRecord` — две проекции с него.
- **Ступень 1 — один список правил.** `STAGE1` в `agentgate/rules/chain.py`: `RuleChain` из объектов `Rule`, первый непустой вердикт побеждает. Одно правило — один модуль в `agentgate/rules/`, hard-deny — в `agentgate/rules/hard_deny/`. Порядок списка и есть вся приоритетная политика ступени 1.
- **Четыре протокола-шва:** `Rule` (`rules/base.py`), `Classifier` (`classify/base.py`), `SessionStateStore` (`domain/session.py`), `DecisionWriter` (`store/writer.py`). Всё, что выше них, зависит от протокола, а не от реализации.
- **Один composition root.** `agentgate/bootstrap.py::build_service` — единственное место, знающее, какие реализации идут в прод. `__main__.py`, `cli.py` и тестовые фабрики берут его, а не собирают свою сборку.
- **Одна таблица знаний о командах.** `agentgate/shell/commands.py` (`CommandSpec`, `COMMANDS`) вместо одиннадцати множеств в пяти файлах: добавить команду — одна строка.
- **`contracts/openapi.yaml` порождается приложением** (`service/scripts/export_openapi.py`, 96 строк вместо 886 рукописных); `tests/test_contracts.py` падает, если документ разошёлся с тем, что сервис реально отдаёт.

Реализованные HTTP-эндпоинты: `POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{profile_id}`, `GET /healthz` (см. `service/agentgate/api/app.py`). Аутентификация: статический токен (`AGENTGATE_TOKEN`) и/или выданные API-ключи — см. «API-ключи» ниже.

## Папки и кто в них пишет

- `service/` — ядро сервиса (направление 2). Подробные правила и карта модулей — `service/CLAUDE.md`. Коротко, пакеты `service/agentgate/`:
  - `domain/` — чистые типы без I/O: `verdict.py`, `policy.py` (`Profile` ⊗ workspace), `session.py`.
  - `shell/` — синтаксис и семантика shell без политики: `commands.py`, `argv.py`, `wrappers.py`, `paths.py`, `secrets.py`.
  - `normalize/` — `DecideRequest` → `NormalizedAction`.
  - `rules/` — ступень 1: `base.py` (`Rule`, `RuleChain`), `chain.py` (`STAGE1`), по модулю на правило.
  - `classify/` — ступень 2: `base.py` (`Classifier`), `llm.py`, `prompt.py`, `schema.py`, `client.py`.
  - `engine/` — оркестрация: `gate.py`, `decision.py`, `timings.py`.
  - `session/`, `store/`, `api/`, `profiles/`, `log/` + `bootstrap.py`, `cli.py`, `__main__.py`.
- `adapters/` — плагины харнессов (направление 1).
- `benchmark/` — внутренний и внешний бенчмарк (направление 3).
- `contracts/` — схемы `/v1/decide`, `openapi.yaml`, шаблон deny-сообщения, `hook_client.py`. Меняется только PR-ом с упоминанием всех трёх направлений — см. `contracts/README.md`.

## Зафиксировано в v1

- Fail-closed везде: ошибка, таймаут, невалидный запрос или ответ → `ask`, HTTP 200. Никогда `allow` по ошибке.
- Hard-deny не переопределяется и не заменяется эскалацией.
- Один тип `Verdict` — от правила ступени 1 до строки в Postgres. Новое поле решения добавляется в одном месте, а не в шести.
- Решение по сырой строке запроса запрещено; только по `NormalizedAction` из AST.
- Неразобранное действие (`flags.unparseable`) закрывается ступенью 1 правилом `unparseable`: `ask`, `stage: 1`, `model: null`. Классификатор не вызывается — он отвечал бы о команде, которую не видел.
- Ступень 2 reasoning-blind: в промпт идут только профиль, prose-слоты, `[TASK]`, `[HISTORY]` (с v2, усечённая история диалога из запроса), `[ACTION]`, `[FLAGS]`, `[STAGE1]`. `metadata` и рассуждения агента — никогда. Вывод инструментов попадает только как `toolresult`-ходы истории, экранированный; семантическая защита от инъекций в нём — v4.
- `deny`/`ask` не кэшируются, `allow` кэшируется на сессию (allow-only cache).
- Один YAML-профиль на сервисе; харнессы о нём не знают.
- Workspace привязан к сессии: `${WORKSPACE}` берётся из `cwd` первого запроса сессии и дальше не меняется (без `session_id` — из `cwd` текущего запроса). `Profile` — конфигурация оператора, `Policy` — профиль, привязанный к одному workspace; правила и промпт получают `Policy`, а не `Profile`. Единственное исключение — `classify/prompt.py` берёт `policy.profile.protected_paths`: в промпт идут объявленные оператором шаблоны, иначе туда попал бы домашний каталог хоста.
- Только Postgres (asyncpg). SQLite не поддерживается.
- Ретраев к LLM нет: один вызов, один таймаут.
- Не в v1: PostToolUse/observe, история диалога, модуль пакетов, ступень 3, override, панель, обучение. Дорожная карта по истории — `context-versions-roadmap.md`.

## API-ключи

Ключи выдаются только из CLI (`python -m agentgate keys create|list|revoke`), эндпоинта для выпуска нет — см. `service/README.md`. Проверка на горячем пути дополняет статический токен: bearer проходит, если совпадает с `AGENTGATE_TOKEN` **или** с действующим выданным ключом (аддитивно, не замена). Результат проверки ключа кэшируется в памяти процесса на короткий TTL.

## Правила работы

- Перед изменением схем `DecideRequest`/`DecideResponse` — обновить спеку и перегенерировать `contracts/` (`cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py`).
- Новое правило ступени 1 — новый класс в `agentgate/rules/` и строка в `STAGE1` (`agentgate/rules/chain.py`); `Gate` не меняется. То же для остальных швов: новая модель — класс с протоколом `Classifier`, новое хранилище сессий — класс с протоколом `SessionStateStore`, новый приёмник решений — класс с протоколом `DecisionWriter`; подключаются в `bootstrap.py`. Примеры — раздел «Как добавить» в `service/README.md`.
- Любой код, возвращающий `allow`, имеет тест на путь отказа.
- Табличные тесты hard-deny включают обфускацию (`$(…)`, `eval`, переменные, base64).
- Latency ступени 1 p50 ≤ 1 мс проверяется тестом.
- Секреты только через переменные окружения; `service/.env` в гите нет и не будет — не читать, не коммитить.
- Код и комментарии — английский; документация — русский; идентификаторы API не переводятся.

## Известные ограничения / roadmap

- **Атрибуция решения к ключу не подключена.** По ключу пишется только `last_used_at` (после ответа, best-effort). `key_id` в `DecisionRow`/JSONL не попадает — спека `api-keys.md` этого хочет, код пока нет (см. docstring `ApiKeyRow` в `service/agentgate/store/models.py`).
- **Кэш проверки ключа — per-process.** В многопроцессном деплое (несколько воркеров uvicorn) отзыв ключа доходит до каждого воркера независимо, в пределах TTL каждого — задержка отзыва не единая на весь сервис, а по худшему из воркеров.
- **v2→v4 (история диалога, оценка tool-result, Context Guard)** — не реализованы, порядок и обоснование зафиксированы в `context-versions-roadmap.md`.
- **Fail-open vs fail-closed конфликт с контрактом адаптера** — контракт Gate↔Guard по умолчанию fail-open на клиенте, наш сервис жёстко fail-closed изнутри; разногласие и три варианта решения — в `adapter-contract-gap-analysis.md`. Не решено владельцем продукта на момент написания.
- **`AGENTGATE_TOKEN` на non-localhost bind** по-прежнему обязателен и принимается наравне с ключами; более строгий вариант спеки api-keys.md («non-localhost принимает только ключи, токен игнорируется») в v1 сознательно не реализован — см. docstring `agentgate/api/deps.py`.
- **Вызов без `session_id` берёт workspace из своего `cwd`.** Привязка к сессии закрыла дыру только для сессионных вызовов; бессессионный вызов с `cwd: "/"` по-прежнему расширяет `allowed_paths` до корня на этот один запрос. Закрыть — значит отвергать безсессионные запросы, это изменение контракта и решение владельца.
- **Порт 8400 всё ещё открыт по HTTP** параллельно с HTTPS через Caddy — до тех пор, пока интеграторы не перейдут на `https://api.openmagi.ru`. Старое имя `109.172.95.51.sslip.io` живёт алиасом в `AGENTGATE_PUBLIC_ALIASES` серверного `.env` до той же поры. Закрыть — одна строка в `docker-compose.yml` (`127.0.0.1:8400:8400`) и следующий `make deploy`.
- **Корень `openmagi.ru` и `www` указывают на сервер, но сайта для них в Caddy нет**: по HTTPS не открываются до появления `frontend/` и второго блока в `service/deploy/Caddyfile`.
- **Неизменяемость `NormalizedAction` поверхностная.** Само действие `frozen=True` (переписать поле нельзя), но `commands`/`paths`/`domains` — списки, а не кортежи: переход на кортежи ломает сравнение `cmd.argv[:len(p)] == p` в `allowlist.py` и молча выключает `safe_prefixes` оператора. Отдельная задача с правкой матчера и golden-тестов, не часть рефакторинга.

## Отчёты

После каждой завершённой задачи — отчёт в `docs/reports/task-<N>-<slug>.md` на русском: что построено, доказательства TDD, находки ревью и как закрыты, принятые решения, что отложено.

## Коммиты

Коммит после каждой задачи, только явные пути — и `git commit --only <пути>`, а не `git add <пути> && git commit`: индекс общий, и обычный коммит забирает чужую подготовленную работу вместе со своей (так на ветке `refactor/solid-v1.5` появился коммит с сообщением про докстринг и переименованиями чужого пакета внутри). Сообщение заканчивается строкой:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```
