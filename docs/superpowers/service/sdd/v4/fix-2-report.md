# v4 fix round 2 — отчёт

**STATUS: DONE.** Все шесть пунктов закрыты. Полный прогон на `agentgate_test_v4`: **1214 passed**. Контракты после `export_contracts.py` + `export_openapi.py` — `git diff --exit-code ../contracts` пуст.

## 1. CRITICAL — ключ кэша покрывает весь провенанс (`034db50`)

До: `test_cache_key_distinguishes_two_urls_of_one_kind` — `assert 1 == 2` (`cache.puts`): два `web`-провенанса с разными `url` и одним выводом делили одну запись кэша, хотя `classify._provenance_line` рендерит `url` в промпт.
Стало: `inspect_cache_key(profile_hash, provenance_digest, …)`; `Inspector._resolve` считает `_digest(request.provenance.model_dump_json())`. Булев `entropy_candidates` оставлен отдельной компонентой — он зависит и от workspace, которого в провенансе нет. Докстринг `cache_key.py` переписан; тест на смену `kind` проходит по-прежнему.

## 2. `reconcile.py` — причины и rule id (`3c0c968`)

До: `test_pass_that_leaves_a_redaction_keeps_stage_ones_reason` — `assert 'redacted 1 secret value(s)' in 'looks like sample code'`; `test_a_model_drop_over_a_clean_stage_one_is_attributed_to_the_semantic_rule` — `(drop, None) != (drop, 'inspect.semantic')`.
- **a.** Ветка `pass` вынесена в `_lifted`: в `_from_findings` идёт `""`, поэтому уцелевший `mask` описывается причиной ступени 1; если не уцелело ничего и вердикт `pass`, причина возвращается модельная (закреплено тестом `test_pass_that_leaves_nothing_keeps_the_models_reason`, он проходил и до правки).
- **b.** Модельный `drop` берёт `stage1.rule_id or SEMANTIC_RULE`.
- **c.** Ветка `EMPTY_SPANS` больше не пересчитывает `apply`, а возвращает поля `stage1` плюс `validated.rejected`. Проверено, что `_run_stage2` читает оттуда только `error` и `spans_rejected`; `_run_stage2` не менялся.

## 3. `engine/inspector.py` (`8bc7f53`)

- **a.** `_INVISIBLE_RULE`/`_SECRET_RULE` удалены; сравнение идёт с `INVISIBLE.id` и импортированным `SECRET_RULE`.
- **b.** Извлечены `_Stage1` (frozen: `findings`, `outcome`, `redacted_lines`), `Inspector._stage1` и `Inspector._inspection`. `inspect()` теперь только оркестрация. Поведение не менялось — доказательство в том, что 320 тестов `tests/inspect` + `tests/engine` зелёные без правок.
- **c.** Комментарий на `Inspection.redacted_output`: считается из **всех** `redact`-находок, до `unredact`, поэтому отпущенное моделью значение видно агенту в `replacement` и остаётся скрытым в `raw` — осознанный перекос аудита в сторону сокрытия.

## 4. Тесты (`51fb9ab`)

`test_classifier_mask_cannot_lift_a_stage_one_drop` и `test_reconcile.py::test_mask_cannot_lift_a_stage_one_drop` — `@pytest.mark.parametrize` с ids `no_spans` / `with_span`, по одному утверждению на случай.

## 5. Мелочи

`mask._KIND_BY_RULE: dict[str, SpanKind]` (в `8bc7f53`); длинная строка вызова `_drop` в `reconcile.py` разбита — в модуле не осталось строк длиннее 118 (в `3c0c968`).

## 6. Документация (`51fb9ab`)

`service/CLAUDE.md`: `ModelSpan` перенесён в описание `spans.py` и убран из списка `classify.py`; `FORMS` → `NEEDLE_FORMS` в строке `inspect/` и в пункте «Форма секрета», с оговоркой, что `SENSITIVE_NAME` предфильтруется разделителем (`=`/`:` с достаточно длинным значением), а не подсказкой; строка `engine/` описывает `inspector.py` полным каскадом. `service/README.md` — та же замена в разделе «…форму секрета» (пример уже строил `Form` с верными полями).

## Опасения

- Ключ кэша inspect зависит теперь от `model_dump_json()` провенанса: добавление поля в `Provenance` инвалидирует весь кэш. Это дешевле, чем список полей, который разойдётся с промптом, — но стоит знать.
- Ветка `EMPTY_SPANS` теперь игнорирует `unredact` из того же ответа (раньше он применялся к пересчитанному `apply`). Наблюдаемого различия нет — `Inspector` из этой ветки читает только `error` и `spans_rejected`, — но контракт `Reconciled` в этой ветке стал строже: это буквально исход ступени 1.
