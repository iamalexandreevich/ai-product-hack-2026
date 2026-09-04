### Task 8: OpenAPI из приложения

Закрывает: F14 в части `scripts/export_openapi.py` (886 строк, из них ≈600 — проза в константах).

**Процессное условие (рулинг 3):** задача меняет `contracts/openapi.yaml`, поэтому её мердж требует PR с упоминанием всех трёх направлений (`contracts/README.md`). Технически она ни от чего не зависит и может уехать отдельно.

**Files:**
- Modify: `service/scripts/export_openapi.py` (≈886 → ≈60 строк), `service/agentgate/api/schemas.py` (`description=` и `examples=` на полях), `service/agentgate/api/app.py` (`summary`, `description`, `responses` на маршрутах)
- Test: `service/tests/test_contracts.py`

- [ ] **Step 1: Тест, требующий, чтобы документ порождался приложением**

`service/tests/test_contracts.py` — добавить:

```python
def test_openapi_yaml_matches_what_the_app_generates(tmp_path):
    from agentgate.api.app import create_app

    generated = _generate_openapi()
    committed = yaml.safe_load(Path("../contracts/openapi.yaml").read_text(encoding="utf-8"))
    assert generated == committed


def test_no_endpoint_is_marked_provisional():
    committed = yaml.safe_load(Path("../contracts/openapi.yaml").read_text(encoding="utf-8"))
    assert "provisional" not in yaml.safe_dump(committed).lower()
```

Второй тест закрывает замечание из `contracts/README.md`: генератор до сих пор помечает реализованные эндпоинты как `provisional`.

- [ ] **Step 2: Описания переезжают на модели**

Перенести прозу из строковых констант `export_openapi.py` в `Field(description=...)` на полях `DecideRequest`, `DecideResponse`, `LatencyMs`, `DecisionView`, `DecisionsPage`, `Health`, `Profile` и в `summary`/`description` декораторов маршрутов. Знание о том, что означает поле, переезжает к самому полю (гайд 1.3) — теперь одна правка вместо двух.

Примеры запросов (`REQUEST_EXAMPLES`) переезжают в `model_config = {"json_schema_extra": {"examples": [...]}}` на `DecideRequest`.

- [ ] **Step 3: Скрипт сокращается**

`service/scripts/export_openapi.py` целиком:

```python
"""Export the OpenAPI document the service actually serves.

Descriptions and examples live on the models and the routes; this script
only renders them, so the document cannot drift from the code.
"""

import asyncio
from pathlib import Path

import yaml

from agentgate.bootstrap import build_service
from agentgate.config import Settings

OUTPUT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"


def main() -> None:
    settings = Settings(db_url="postgresql+asyncpg://export:export@localhost/export")
    service = asyncio.run(build_service(settings, state_store=_NullStore(), writer=_NullWriter()))
    document = service.app.openapi()
    OUTPUT.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=True), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
```

`build_service` при экспорте не должен требовать живой Postgres — `make_engine` соединение не открывает, а `state_store`/`writer` подставляются заглушками. Если `build_service` всё же обращается к БД (через `restore()`), передать `state_store=InMemorySessionStateStore()`, у которого нет `restore`.

- [ ] **Step 4: Сверить документ и закоммитить**

Run:
```bash
cd service && uv run python scripts/export_openapi.py && git diff ../contracts/openapi.yaml
```
Expected: осмысленный diff. Разобрать его целиком: каждое расхождение — это либо описание, потерянное при переносе (вернуть), либо ручная неточность старого генератора (исправление, записать в отчёт). Пути, коды ответов и схемы обязаны совпасть с тем, что было.

Run: `cd service && uv run pytest tests/test_contracts.py -v`
Expected: все зелёные.

```bash
git add service/scripts/export_openapi.py service/agentgate/api contracts/openapi.yaml service/tests/test_contracts.py
git commit -m "refactor(contracts): generate openapi.yaml from the app

Field and route descriptions move onto the models and the decorators, so
the document renders from the code that serves it instead of from 600
lines of prose constants. Endpoints that are implemented no longer
describe themselves as provisional.

Touches contracts/ -- see contracts/README.md: needs sign-off from the
harness, service and benchmark tracks.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

