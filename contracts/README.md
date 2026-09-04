# contracts

Единственная общая точка трёх направлений. Здесь лежат:

- `openapi.yaml` — OpenAPI 3.1 всего HTTP-контракта v1: `POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz`. Это документ, который отдают внешнему разработчику. Генерируется `service/scripts/export_openapi.py` из документа, который отдаёт само приложение (`app.openapi()`), поэтому правка руками бессмысленна — перезатрётся. Тест `test_openapi_yaml_matches_what_the_app_generates` в `service/tests/test_contracts.py` падает, если закоммиченный файл и сервис расходятся. Описания полей и маршрутов живут на pydantic-моделях и на декораторах роутов, а не в скрипте: одна правка обновляет и код, и документ. Прежняя известная неточность — отметки `provisional` на реализованных маршрутах — закрыта: документ больше не пишется руками, и отметок в нём нет.
- `decide_request.schema.json`, `decide_response.schema.json` — JSON-схемы `POST /v1/decide`. Генерируются из pydantic-моделей сервиса (`service/agentgate/api/schemas.py`); тест `test_contracts.py` проверяет, что закоммиченные схемы совпадают со сгенерированными.
- `deny_message_template.md` — шаблон текста, который адаптер отдаёт агенту при `deny`.
- `hook_client.py` — эталонный клиент, зависимостей кроме stdlib нет: JSON хука харнесса на stdin → запрос `POST /v1/decide` → решение на stdout. Fail-closed: недоступность сервиса, таймаут или любая ошибка разбора хука дают `ask`, никогда не падают трейсбеком (что под семантикой кодов выхода Claude Code PreToolUse читалось бы как fail-open).

## v2: история, протокол, идемпотентность

- `history` в `POST /v1/decide` — список ходов `{role, author, content, tool?, call_id?}`, старые первыми. `role`: `human | assistant | toolcall | toolresult`; `author`: `human | agent | system` — только `human` считается словами пользователя. Не более 200 ходов и 128 КБ UTF-8 в сумме по `content`, `tool` и `call_id`; сверх — `ask` с `rule_id: api.history-too-large`. Пустая история ведёт себя как v1. Скрытые рассуждения агента (thinking, scratchpad) в `content` не должны отправляться: сервис это не проверяет.
- `protocol` в запросе, ответе и `/healthz`. Сервис отвечает `1`; другое значение — `ask` с `rule_id: api.unsupported-protocol`.
- Заголовок `Idempotency-Key`: повтор с тем же ключом возвращает то же решение и тот же `decision_id`, не двигает счётчики сессии и не пишет вторую строку. Ключ должен быть уникален на вызов инструмента; сервис его не разбирает. Три свойства, о которые спотыкаются интеграторы:
  - повтор с тем же ключом, но **другим запросом** не повторяется, а судится заново: личность запроса — sha256 всего тела без `metadata` (единственного поля, которое по контракту не доходит до логики решения), поэтому расхождение в любом поле, включая `args.paths`, `history` и `user_request`, даёт новое решение. Ключ сам по себе не доказывает, что вызов тот же;
  - ключ **длиннее 128 символов игнорируется**: запрос обслуживается обычным порядком, отказа нет;
  - ключ **глобален для сервиса** — не привязан ни к сессии, ни к учётным данным. Привязка к аутентифицированному ключу — отдельная задача.
- Лента `GET /v1/decisions` отдаёт `history` (то, что увидела модель, после усечения), `history_omitted`, `history_digest`, `request_digest`, `protocol`, `idempotency_key`.

По дорожной карте v2 остаётся внутренней до готовности v4 (Context Guard): поле `history` есть в контракте, но адаптерам как поддерживаемое не объявляется.

Изменения здесь — только PR-ом с упоминанием направлений service, adapters и benchmark. Описание полей — в спеке `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`, раздел 4, а для полей v2 (`history`, `protocol`, `Idempotency-Key`) — в `docs/superpowers/service/specs/2026-09-04-agentgate-v2-design.md`.

## hook_client.py — пример вызова

Поддерживает hook-форматы Claude Code (`PreToolUse`, ключ `tool_name`) и OpenCode (`tool.execute.before`, ключ `sessionID`); формат выбирается автоматически по форме входного JSON.

```bash
echo '{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}, "session_id": "s1", "cwd": "/repo"}' \
  | AGENTGATE_URL=http://127.0.0.1:8400 AGENTGATE_TOKEN=dev-token \
    python3 contracts/hook_client.py --user-request "clean up temp files" --profile default
```

Печатает JSON-решение в stdout (`{"decision": "...", "reason": "...", "suggest": "..."}` и т.п.) и завершается с кодом, который харнесс использует как вердикт:

| код выхода | решение | значение для харнесса |
|---|---|---|
| `0` | `allow` | действие разрешено |
| `2` | `deny` | действие заблокировано; `reason`/`suggest` — агенту |
| `3` | `ask` | решение не принято (в т.ч. сервис недоступен, невалидный хук) — харнесс решает сам, обычно как «не блокировать» |

`--url`/`AGENTGATE_URL`, `--profile`/`AGENTGATE_PROFILE`, `--user-request`/`AGENTGATE_USER_REQUEST` — флаг или переменная окружения, флаг приоритетнее. `AGENTGATE_TOKEN`, если задан, идёт в `Authorization: Bearer <token>` — работает как со статическим токеном, так и с выданным API-ключом (`agk_...`, см. `service/README.md`, раздел «API-ключи»).

Регенерация сгенерированных файлов:

```
cd service
uv run python scripts/export_contracts.py
uv run python scripts/export_openapi.py
uv run pytest tests/test_contracts.py
```
