# Задача 13: CLAUDE.md и документация

## Коррекция базового коммита

Рабочее дерево оказалось на `a9a0edd` (предок нужного `d79b5fa`, «Merge API keys: generation,
storage, CLI, hot-path verification»). `git merge-base --is-ancestor HEAD d79b5fa` подтвердил чистый
fast-forward, дерево было чистым — выполнен `git reset --hard d79b5fa`. Все файлы-ориентиры
(`service/agentgate/api/app.py`, `service/agentgate/cli.py`, `service/agentgate/store/models.py`,
`contracts/openapi.yaml`, `service/README.md`, `contracts/README.md`) на месте.

## Что построено

### 1. Корневой `CLAUDE.md` (новый файл)

Единственный файл вне `service/`/`contracts/`, который разрешено создать в этой задаче. Пути из
брифа устарели (план и спека переехали) — исправлено на актуальные:
`docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`,
`docs/superpowers/service/plans/2026-09-03-agentgate-v1.md`. Добавлены ссылки на
`context-versions-roadmap.md`, `api-keys.md`, `deploy.md`, `adapter-contract-gap-analysis.md`.

Содержание: что такое AgentGate и каскад `/v1/decide`; подтверждение, что все четыре HTTP-эндпоинта
(`POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz`) реально
реализованы (проверено по декораторам маршрутов в `service/agentgate/api/app.py`); зоны папок;
зафиксированные в v1 инварианты (fail-closed, hard-deny неотменяем, решения только по
`NormalizedAction`, ступень 2 reasoning-blind с закрытым списком промпт-слотов, allow-only кэш,
только Postgres, без ретраев LLM); раздел «API-ключи», описывающий фактическое (не спека-желаемое)
поведение аутентификации: ключи только из CLI, bearer проходит по статическому токену **или**
действующему ключу (аддитивно), проверка кэшируется в памяти процесса на короткий TTL; правила
работы (перегенерация контрактов при смене схем, покрытие hard-deny обфускацией, тест latency
ступени 1, секреты только через env, английский код/русская документация); раздел «Известные
ограничения / roadmap» (см. ниже).

### 2. `service/README.md` (обновлён)

- Абзац «Запуск (после реализации)» заменён двумя реальными путями: локальный запуск
  (`uv sync` → `alembic upgrade head` → `python -m agentgate` при поднятом отдельно
  `docker compose up -d db`) и полный Docker Compose (`docker compose up -d --build`, с пометкой,
  что `CMD` в `Dockerfile` уже сам прогоняет `alembic upgrade head` при старте контейнера).
- Добавлена таблица переменных окружения: `AGENTGATE_DB_URL`, `AGENTGATE_TOKEN`, `AGENTGATE_BIND`,
  `AGENTGATE_PROFILES_DIR`, `AGENTGATE_LOG_PATH`, `AGENTGATE_DEFAULT_PROFILE`,
  `AGENTGATE_API_KEY_CACHE_TTL_SECONDS`, ключи LLM-провайдеров (имя переменной задаётся в профиле,
  `models.configs.<name>.api_key_env`) — сверено с полями `Settings` в `agentgate/config.py`.
- Добавлен раздел «Тесты»: `uv run pytest -q` без базы против
  `AGENTGATE_TEST_DB_URL=... uv run pytest -q` с базой, с перечислением, какие модули тестов
  требуют базу.
- Добавлен раздел «Профили»: где лежит активный профиль (`AGENTGATE_PROFILES_DIR`, по умолчанию
  `profiles/`, сейчас `profiles/default-dev.yaml`), что профиль описывает, и отдельно — как
  добавить модель (новая запись в `models.configs` с полями `base_url`, `model`, `api_key_env`,
  плюс `timeout_ms`, `structured_output`), как переключить модель на конкретный запрос через поле
  `model` в `POST /v1/decide` (сверено с `agentgate/profiles/schema.py` и веткой
  `api.unknown-model` в `agentgate/pipeline.py`).
- Существующий раздел «API-ключи» (написан в предыдущей задаче) оставлен как есть — он уже
  покрывает выпуск/список/отзыв, «показывается один раз», аддитивность bearer и задержку отзыва по
  TTL, ровно то, что требовал бриф этой задачи.

### 3. `contracts/README.md` (обновлён)

- Исправлен устаревший путь к спеке (`docs/superpowers/specs/...` →
  `docs/superpowers/service/specs/...`).
- Исправлено неверное утверждение, что `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz`
  «ещё не реализованы» — по коду (`service/agentgate/api/app.py`) все четыре маршрута реализованы.
  Вместо этого явно отмечено: генератор `service/scripts/export_openapi.py` до сих пор помечает эти
  три маршрута как `provisional` в прозе `openapi.yaml`, это устаревшая неточность генератора, а не
  README; исправление генератора — вне зоны задачи 13 (это был бы код в `service/scripts/`).
  `openapi.yaml` руками не редактировался — тест `test_openapi_matches_generated_document` сверяет
  закоммиченный файл с выводом генератора и упал бы на ручной правке.
- Добавлен раздел «hook_client.py — пример вызова»: рабочий пример
  `echo '<hook json>' | AGENTGATE_URL=... AGENTGATE_TOKEN=... python3 contracts/hook_client.py
  --user-request ... --profile ...`, построенный по реальному CLI-интерфейсу `hook_client.py`
  (флаги `--user-request`/`--url`/`--profile`, переменные `AGENTGATE_USER_REQUEST`/`AGENTGATE_URL`/
  `AGENTGATE_PROFILE`/`AGENTGATE_TOKEN`), и таблица кодов выхода (`0` allow, `2` deny, `3` ask —
  включая недоступность сервиса и невалидный хук).

### 4. `service/Makefile` (новый файл)

Цели: `deploy`, `logs`, `ps`, `rollback`, плюс служебная `check-clean`, от которой зависит `deploy`.

- `check-clean` — отказывает, если `git status --porcelain -- .` (из `service/`, то есть по
  дереву, которое реально уходит на сервер) не пуст.
- `deploy`: `check-clean` → `rsync -az --delete` текущего каталога на
  `$(DEPLOY_HOST):$(DEPLOY_DIR)/`, исключая `.venv/`, `.env`, `logs/`, `__pycache__/`,
  `.pytest_cache/` и `.previous_gate_image` (служебный файл, который `deploy` сам пишет на
  сервере) → одной SSH-сессией: запомнить id текущего образа `gate` (для отката),
  `docker compose up -d --build`, `docker compose exec -T gate alembic upgrade head` → финальная
  проверка `curl -fsS http://localhost:$(HEALTHZ_PORT)/healthz` на сервере, ненулевой код считает
  деплой неуспешным (ненулевой выход у `make deploy`) с подсказкой про `make logs`/`make ps`/
  `make rollback`.
- `logs`/`ps` — тонкие SSH-обёртки над `docker compose logs -f` / `docker compose ps` в
  `$(DEPLOY_DIR)`.
- `rollback` — best-effort, на одно поколение назад: читает id образа, сохранённый `deploy` перед
  последней сборкой, перетегирует его на имя, которое ожидает `docker compose config --images gate`,
  и перезапускает `gate` с `--no-build` из этого тега, затем повторно проверяет `/healthz`.
  Автоматического отката при неудачном health-check нет — оператор запускает цель руками.
- `DEPLOY_HOST ?= agentgate` (имя алиаса в `~/.ssh/config`, не хост и не IP), `DEPLOY_DIR ?=
  /opt/agentgate`. В шапке файла — комментарий на английском (код и комментарии кода — по правилу
  English), что оператор один раз сам заводит `Host agentgate` в своём `~/.ssh/config`
  (`HostName`, `User`, `IdentityFile`) и один раз выполняет `ssh-copy-id agentgate`; в файле
  никогда не должно быть IP, `IdentityFile` или пароля/токена.

**Проверка отсутствия секретов**: `grep -niE 'password|secret|token|<IPv4>|identityfile'
service/Makefile` находит только упоминания этих слов внутри поясняющих комментариев (описание
правила, а не значение) — ни литерального IP, ни ключа, ни пароля в файле нет. `ssh`/`rsync`
обращаются только к `$(DEPLOY_HOST)`/`$(DEPLOY_DIR)`.

Сам `make deploy` не запускался — сервер за VPN и недоступен из этого окружения, и по заданию
запуск деплоя оставлен пользователю. Синтаксис проверен `make -n deploy` / `make -n check-clean`
(dry run) — переменные разворачиваются корректно, `make` не жалуется на «missing separator»,
значит, в рецептах реальные табы, а не пробелы.

### 5. Исправление docstring `service/agentgate/store/models.py`

Docstring `ApiKeyRow` (около строки 78) утверждал, что `id` «doubles as the public `key_id` used in
… `DecisionRow`/log attribution». Проверено по `agentgate/api/deps.py` и `agentgate/store/keys.py`:
на горячем пути для ключа реально пишется только `last_used_at` (фоновой задачей после ответа).
Ни в персистенции решений в `agentgate/api/app.py`, ни в JSONL-логгере `key_id` не читается и не
пишется. Docstring исправлен: `id` — это `key_id`, которым пользуется только CLI (`keys list`/
`keys revoke`); добавлен абзац `NOTE`, что атрибуция решения к ключу (`key_id` в
`DecisionRow`/JSONL, как того хочет спека) не подключена и остаётся пунктом roadmap. Кода это не
касалось — только текст docstring.

## Известные ограничения / roadmap (зафиксировано в корневом `CLAUDE.md`)

- Атрибуция решения к ключу (`key_id` → `DecisionRow`/JSONL) не подключена — пишется только
  `last_used_at`.
- Кэш проверки ключа — per-process (`_KeyVerifier` в `agentgate/api/deps.py`): в многопроцессном
  деплое задержка отзыва — по худшему из воркеров, а не единая на сервис.
- Дорожная карта v2→v4 (история диалога, оценка tool-result, Context Guard) не реализована —
  ссылка на `context-versions-roadmap.md`.
- Конфликт fail-open (контракт адаптера по умолчанию) vs fail-closed (наш сервис) —
  `adapter-contract-gap-analysis.md`, не решено владельцем продукта на момент задачи.
- `AGENTGATE_TOKEN` на non-localhost bind по-прежнему принимается наравне с ключами; более строгий
  вариант спеки api-keys.md сознательно не реализован (уже задокументировано в docstring
  `agentgate/api/deps.py`, теперь вынесено и на верхний уровень).

## Результат тестов

`cd service && uv run pytest -q` (без `AGENTGATE_TEST_DB_URL`, Postgres в этом окружении не
поднят): **446 passed, 43 skipped** (все 43 скипа — `AGENTGATE_TEST_DB_URL not set`:
`tests/test_store.py`, `tests/test_main.py`, `tests/test_keys.py`, `tests/test_cli_keys.py`,
`tests/e2e/test_e2e.py`).

**Расхождение с заявленным в задании базовым результатом** («466 passed / 23 skipped без базы»):
фактически 446/43. Суммарно оба варианта дают одно и то же общее число тестов — 489
(446+43 = 466+23 = 489), что совпадает с упомянутыми в задании «489 с `AGENTGATE_TEST_DB_URL`» —
то есть общее число тестов ожиданиям соответствует, расходится только разбивка без базы, ровно на
20 тестов, которые, по всей видимости, стали DB-gated (или появились новыми и сразу DB-gated) уже
после того, как цифра 466/23 была зафиксирована — правдоподобно, что это тесты из задачи 12
(`test_keys.py`, `test_cli_keys.py`, `tests/e2e/`). Тесты этой задачей не менялись, дальше
разбираться не стал — это не входит в скоуп T13 (документация + Makefile + один docstring), но
расхождение зафиксировано, а не проигнорировано.

Postgres в этом окружении поднят не был, поэтому цифру «489 с базой» независимо не перепроверял —
она выведена из арифметики, а не запущена вживую.

## Изменённые файлы

- `CLAUDE.md` (новый, корень репозитория)
- `service/README.md` (изменён)
- `contracts/README.md` (изменён)
- `service/Makefile` (новый)
- `service/agentgate/store/models.py` (только docstring)
- `.superpowers/task-13-report.md` (новый, не коммитится — в `.gitignore`)
- `reports/task-13-docs.md` (этот файл, новый, коммитится)

## `git status --short` (до коммита)

```
 M contracts/README.md
 M service/README.md
 M service/agentgate/store/models.py
?? CLAUDE.md
?? reports/task-13-docs.md
?? service/Makefile
```
