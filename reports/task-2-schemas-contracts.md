# Задача 2: Схемы API и `contracts/`

## Что построено

- `service/agentgate/api/__init__.py` — пустой пакет.
- `service/agentgate/api/schemas.py` — pydantic v2 модели `Tool`, `DecisionKind`,
  `McpArgs`, `ActionArgs`, `DecideRequest`, `LatencyMs`, `DecideResponse` и константы
  `USER_REQUEST_MAX_CHARS`, `RAW_MAX_BYTES`, `METADATA_MAX_BYTES` — ровно по спецификации
  брифа, без добавления/переименования полей.
  - `DecideRequest`: `raw` ограничен по размеру в байтах (`RAW_MAX_BYTES`); `metadata`
    ограничена по размеру JSON-сериализации (`METADATA_MAX_BYTES`); `user_request`
    обрезается до `USER_REQUEST_MAX_CHARS` с сохранением хвоста; `model_validator`
    требует непустой `raw` при `tool == shell`.
- `service/scripts/export_contracts.py` — регенерирует `contracts/*.schema.json` из
  `model_json_schema()`.
- `contracts/decide_request.schema.json`, `contracts/decide_response.schema.json` —
  сгенерированные JSON-схемы.
- `contracts/deny_message_template.md` — шаблон сообщения агенту при `deny`.

## Тестирование

TDD: сначала написаны падающие тесты, затем реализация.

### RED

Команда:
```
cd service && uv run pytest tests/test_schemas.py tests/test_contracts.py -v
```
Результат (до создания `agentgate/api`):
```
ModuleNotFoundError: No module named 'agentgate.api'
```
в обоих тестовых модулях при импорте — ожидаемо, так как пакет `agentgate.api` ещё не
существовал.

### GREEN

Команды:
```
cd service && uv run python scripts/export_contracts.py
cd service && uv run pytest tests/test_schemas.py tests/test_contracts.py -v
```
Результат: два файла контрактов записаны, `9 passed`.

Полный набор тестов сервиса (включая наследие задачи 1):
```
cd service && uv run pytest -q
```
Результат: `27 passed` (18 из задачи 1 + 9 новых), без предупреждений. Дополнительно
прогнано с `-W error` — тоже `27 passed`, скрытых pydantic-deprecation warnings нет.

## Находки ревью и решения

Собственный ревью диффа перед коммитом: состав файлов совпадает со списком брифа один в
один, лишних полей/файлов не добавлено, тесты и реализация — дословно по брифу.
Замечаний не найдено.

## Отложено

Ничего не отложено — задача реализована полностью по брифу.

## Отклонение от базового состояния worktree

Worktree на старте был на ветке `worktree-agent-a40ffa1f79edd15c8` в коммите `a9a0edd`,
без коммитов задачи 1 (`a2e0979`, `2215d6a`, `593a461`, `90b8339`), хотя должен был
базироваться на `feat/agentgate-task-1` @ `90b8339`. Коммит `a9a0edd` — прямой предок
`90b8339`, расхождений не было, поэтому ветка синхронизирована через
`git reset --hard feat/agentgate-task-1` до начала работы (без потери какой-либо работы,
так как в worktree не было незакоммиченных изменений).
