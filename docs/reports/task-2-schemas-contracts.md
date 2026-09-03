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

## Fix round 1 (по итогам ревью)

Ревью вернуло два замечания Important — оба про отсутствие тестового покрытия лимитов
запроса, реализация валидаторов признана корректной и не менялась. Плюс два замечания
Minor из той же тестовой поверхности, объединены в один проход.

Добавлено в `service/tests/test_schemas.py` (реализация `service/agentgate/api/schemas.py`
не менялась):

- `_field_names(exc)` — хелпер, достающий из `ValidationError.errors()` имена полей,
  на которые сработал валидатор (`loc[0]`).
- Лимит `raw` (`RAW_MAX_BYTES`, Finding 1): `test_raw_accepted_at_byte_limit` (граница
  снизу — ровно `RAW_MAX_BYTES` ASCII-байт принимается), `test_raw_rejected_over_byte_limit_ascii`
  (`RAW_MAX_BYTES + 1` ASCII-байт отклоняется, поле `raw`),
  `test_raw_rejected_over_byte_limit_multibyte` (кириллица: количество символов заведомо
  меньше `RAW_MAX_BYTES`, но UTF-8 байт больше — отклоняется, поле `raw`; это ловит
  регрессию «строку меряют в символах, а не в байтах»).
- Лимит `metadata` (`METADATA_MAX_BYTES`, Finding 2): старый тест `test_metadata_size_limit`
  использовал payload из ASCII-символов, где число символов и число байт совпадают —
  он прошёл бы и на неверной посимвольной реализации. Пересобран в три теста:
  `test_metadata_accepted_at_byte_limit` (граница снизу — ровно `METADATA_MAX_BYTES` байт
  JSON-сериализации принимается), `test_metadata_rejected_over_byte_limit_ascii`
  (`METADATA_MAX_BYTES + 1` байт ASCII отклоняется, поле `metadata`),
  `test_metadata_rejected_over_byte_limit_multibyte` (кириллица: символов меньше
  `METADATA_MAX_BYTES`, байт UTF-8 — больше, отклоняется, поле `metadata`).
- Minor: `session_id` (`max_length=128`) и `harness` (`max_length=64`) — по паре
  accept-at-limit/reject-over-limit тестов каждый, с проверкой поля в ошибке.

### TDD этого раунда

Тесты написаны и прогнаны первыми, реализация не менялась. Ревью заранее установило, что
реализация корректна — поэтому честный ожидаемый результат первого прогона: все новые
тесты проходят сразу же (они действуют как регрессионные guard'ы, а не как RED). Именно
это и наблюдалось — RED искусственно не создавался.

Команда:
```
cd service && uv run pytest tests/test_schemas.py -v
```
Результат: `16 passed` — включая все 6 старых тестов файла и 10 новых/переписанных, без
единого падения. Разбивка новых по именам: `test_metadata_accepted_at_byte_limit`,
`test_metadata_rejected_over_byte_limit_ascii`,
`test_metadata_rejected_over_byte_limit_multibyte`, `test_raw_accepted_at_byte_limit`,
`test_raw_rejected_over_byte_limit_ascii`, `test_raw_rejected_over_byte_limit_multibyte`,
`test_session_id_accepted_at_max_length`, `test_session_id_rejected_over_max_length`,
`test_harness_accepted_at_max_length`, `test_harness_rejected_over_max_length` — все
`PASSED` на первом прогоне.

Полный набор тестов сервиса, под `-W error`:
```
cd service && uv run pytest -q -W error
```
Результат: `36 passed` (27 было + 9 net new: −1 старый `test_metadata_size_limit`,
+3 metadata, +3 raw, +2 session_id, +2 harness), без предупреждений.

Проверено отдельно, что `contracts/*.schema.json` не разошлись (реализация не менялась):
```
cd service && uv run pytest tests/test_contracts.py -v
```
Результат: `2 passed`.

### Итог

Оба Important и оба Minor замечания закрыты добавлением тестов; поведение валидаторов не
менялось. `git status --short` после этого раунда — изменён только
`service/tests/test_schemas.py`.
