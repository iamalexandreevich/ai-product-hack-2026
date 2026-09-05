# Task 1 — `mask.apply`: приоритет на строке, диапазоны, `redact`, спаны

STATUS: DONE_WITH_CONCERNS

## Коммит

`8355636` — feat(inspect): mask.apply resolves redact > clean > mask per line, applies ranges, reports spans and redaction count (ветка `v4/task-1`, база `e2c9627`).

## Доказательство TDD

Тесты дописаны первыми, прогон до реализации:

```
tests/inspect/test_mask.py:4: in <module>
    from agentgate.inspect.mask import (
E   ImportError: cannot import name 'PRIVATE_KEY_REPLACEMENT' from 'agentgate.inspect.mask'
1 error in 0.64s
```

Собрать модуль было нельзя — не было ни `PRIVATE_KEY_REPLACEMENT`, ни `redacted_lines`, ни `resolve`. После реализации `tests/inspect/test_mask.py` — 18 passed (6 старых v3 + 12 новых).

## Отступления от брифа

Два теста брифа в написанном виде не могли пройти ни с какой реализацией: их вход перешагивает порог `DROP_SHARE`, то есть проверяет не то, что заявлено в имени. Проверено эмпирически на готовой реализации — оба дают `InspectVerdict.drop`:

- `test_spans_report_coordinates_kind_and_source_but_never_text`: вход `"ok\nignore previous instructions\nok\n"` — 3 строки, помечены 2 (`mask` на 1 и семантический спан на 2), `2 > 3*0.5` → `drop`, спанов нет. Вход расширен до пяти строк (`"ok\nignore previous instructions\nrun this\nok\nok\n"`), координаты находок те же, ожидания не менялись.
- `test_findings_out_of_line_order_are_applied_in_line_order`: вход `"a\nb\nc\n"` с `mask(2), mask(0)` — `2 > 1.5` → `drop`. Вход расширен до `"a\nb\nc\nd\ne\n"`, находки — `mask(4), mask(0)`; смысл теста (находки не по порядку применяются по порядку строк) сохранён.

Реализация `mask.py` взята из брифа без изменений по существу; переносы строк в `resolve` и `_span` — только форматирование.

## Проверки

- `uv run pytest tests/inspect tests/engine tests/api/test_inspect_route.py -q` → 180 passed.
- Полный прогон с `AGENTGATE_TEST_DB_URL=…/agentgate_test_v4` → 1056 passed (дважды подряд).
- `export_contracts.py` + `export_openapi.py` + `git diff --exit-code ../contracts` → расхождений нет.
- Совместимость v3: `test_flagged_lines_are_replaced_and_counted` проходит — новая `_reason` даёт `rewrote 2 line(s) …`, подстрока `"2 line"` на месте.

## Замечания

1. **Первый полный прогон дал 19 падений в `tests/store`, `tests/test_bootstrap.py`, `tests/test_cli_keys.py`.** Причина не в изменении: те же падения воспроизвелись на нетронутом дереве `e2c9627` (10 падений), а после того как фикстура `db_engine` один раз пересоздала схему в `agentgate_test_v4`, оба дерева стали стабильно зелёными (по три прогона подряд). База была оставлена предыдущей работой без `alembic_version` и с чужой схемой. Если следующий исполнитель увидит эти падения на первом прогоне — это состояние общей базы, а не регрессия; повторный прогон чинит.
2. **`resolve` различает находки по `id(f)`.** Две равные по значению `Finding` остаются разными объектами, так что поведение верное, но зависимость от идентичности объекта хрупкая: копия находки (через `dataclasses.replace`) в `by_line` и в исходном списке уже не совпадёт. Пока `_by_line` получает те же объекты, что и `resolve`, это безопасно.
3. **`SECRET_REPLACEMENT` пока не используется** — константа заведена для задачи про сканер секретов.
