# contracts

Единственная общая точка трёх направлений. Здесь лежат:

- `openapi.yaml` — OpenAPI 3.1 всего HTTP-контракта v1: `POST /v1/decide` (реализован), `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz` (ещё не реализованы, помечены как provisional). Это документ, который отдают внешнему разработчику. Генерируется `service/scripts/export_openapi.py`, правка руками бессмысленна — перезатрётся.
- `decide_request.schema.json`, `decide_response.schema.json` — JSON-схемы `POST /v1/decide`. Генерируются из pydantic-моделей сервиса (`service/agentgate/api/schemas.py`); тест в `service/` проверяет, что закоммиченные схемы совпадают со сгенерированными.
- `deny_message_template.md` — шаблон текста, который адаптер отдаёт агенту при `deny`.
- `hook_client.py` — эталонный клиент: JSON хука на stdin → запрос к сервису → решение на stdout, код выхода `0` allow, `2` deny, `3` ask.

Изменения здесь — только PR-ом с упоминанием направлений service, adapters и benchmark. Описание полей — в спеке `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`, раздел 4.

Регенерация сгенерированных файлов:

```
cd service
uv run python scripts/export_contracts.py
uv run python scripts/export_openapi.py
uv run pytest tests/test_contracts.py
```
