STATUS: DONE

Коммит: `ab800a5` (ветка `v4/task-8`, от `86a4aa0`).

## Доказательства TDD

- `tests/inspect/test_reconcile.py` (13 тестов) — до реализации: `ModuleNotFoundError: No module named 'agentgate.inspect.reconcile'`; после `reconcile.py` — 13 passed.
- `tests/engine/test_inspector.py` — после дописывания новых тестов и правки одного v3-теста: **14 failed, 26 passed**; после каскада в `Inspector` — 40 passed.
- `tests/api/test_inspect_route.py` — 5 приёмочных сценариев §7.3 добавлены, 12 passed.
- Контракты: `export_contracts.py && export_openapi.py && git diff --exit-code ../contracts` — без diff.
- Полный прогон на `agentgate_test_v4`: **1174 passed** (0 failed, 0 skipped).

## Ступень 1 на 256 КБ

Измеренный p50 (`latency.stage1_ms`, 20 прогонов, корпус 262 033 байт / 3590 строк, из них 359 с секретом): **16 мс** при бюджете 25 мс.
Разбивка: `scan_secrets` 7,0 мс, четыре детектора 7,8 мс, `apply` 1,1 мс, `split` + `redacted_lines` 0,4 мс.

## Отступления от брифа

1. **Тест латентности меряет `result.latency.stage1_ms`, а не wall-clock всего `inspect()`.** Как написано в брифе, тест падал с p50 = 59 мс. Причина измерена и не в ступени 1: `detect_workspace("/home/u/repo")` на этой машине стоит **~25 мс сам по себе** (`posix.stat` по несуществующим путям, ~11,7 мс на вызов в профиле), и он вызывается до ступени 1 и не ограничен размером вывода. Бюджет §7.2 — про ступень 1, поэтому меряется ровно она; ступень 1 в тот же прогон укладывалась в 16 мс.
2. **Цикл сборки корпуса останавливается до превышения `OUTPUT_MAX_BYTES`.** Брифовский `while total < 262_144` перешагивал лимит на последней строке, и `InspectRequest` отвергал вывод ещё в фабрике. Условие переписано на `if total + len(line)+1 > OUTPUT_MAX_BYTES: break`; корпус остался ~256 КБ.
3. **`_scan` без параметра `lines`.** В сигнатуре из брифа он не использовался (`scan_secrets` берёт целиком `request.output`) — убран.

Прочее по брифу без изменений: `_outcome_from` потерял неиспользуемый `stage1`; `_cap_stage2` удалён (оба его капа живут в `reconcile`); `test_classifier_mask_keeps_invisible_cleaning_when_findings_are_mixed` переписан на ответ со спанами, рядом добавлен `test_mask_without_spans_is_a_stage_two_error_that_keeps_stage_one`.

## Замечания

- Запас по бюджету ступени 1 — 1,56× (16 из 25 мс), и он делится между сканером секретов и детекторами примерно поровну. Новая форма секрета с широким needle или новый детектор с широкими `hints` его заметно съедят.
- `test_v3_request_gets_a_v3_shaped_answer_plus_two_fields` подтвердил, что `cost: null` в ответ не попадает; набор полей ответа v3 не изменился, добавились ровно `spans` и `redacted`.
- Ни один существующий тест v3 не пришлось менять по смыслу, кроме одного, который бриф прямо предписал переписать (§4.5: `mask` без спанов — противоречие, то есть ошибка ступени 2).
