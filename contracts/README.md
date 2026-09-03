# contracts

Единственная общая точка трёх направлений. Здесь лежат:

- `decide_request.schema.json`, `decide_response.schema.json` — JSON-схемы `POST /v1/decide`. Генерируются из pydantic-моделей сервиса (`service/agentgate/api/schemas.py`); тест в `service/` проверяет, что закоммиченные схемы совпадают со сгенерированными.
- `deny_message_template.md` — шаблон текста, который адаптер отдаёт агенту при `deny`.
- `hook_client.py` — эталонный клиент: JSON хука на stdin → запрос к сервису → решение на stdout, код выхода `0` allow, `2` deny, `3` ask.

Изменения здесь — только PR-ом с упоминанием направлений service, adapters и benchmark. Описание полей — в спеке `docs/superpowers/specs/2026-09-03-agentgate-v1-design.md`, раздел 4.
