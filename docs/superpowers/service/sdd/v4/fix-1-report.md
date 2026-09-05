# v4 fix round 1 — отчёт

STATUS: DONE — 6 коммитов на `v4/fix-1` (база `7840c7b`), полный прогон 1208 passed на БД `agentgate_test_v4`, `git diff --exit-code ../contracts` чист после перегенерации.

## 1. Ключ кэша нёс только `provenance.kind` — `c8605f3`
Падало: `tests/engine/test_inspector.py::test_cache_key_distinguishes_two_shell_commands_with_one_output` (`assert 1 == 2` — один put на два разных `cat`), `::test_a_secret_reading_command_still_redacts_when_a_harmless_one_came_first` (`assert True is False` — `cat .env` получил закэшированный `pass` от `cat config.sample`), плюс два в `tests/session/test_cache_key.py`.
Сделано: `inspect_cache_key` принимает шестым аргументом `entropy_candidates: bool` (в payload `"1"`/`"0"`); `Inspector._resolve` считает `entropy_candidates_allowed(request.provenance, workspace)` один раз, кладёт на `_Context`, оттуда же его берёт `_scan` (сигнатура `_scan(request, policy, entropy_candidates)`). Докстринг `cache_key.py` дополнен.

## 2. Иглы auth-заголовка строже своего шаблона — `54f8614`
Падало: `test_recognized_forms_are_redacted_by_value_and_keep_the_key[x_api_key_spaced_colon]` и `[authorization_spaced_colon]` (строки не находились), и новый инвариантный тест `test_a_needle_prefilter_admits_every_line_its_own_pattern_matches` (`AssertionError: (('authorization:', 'x-api-key:'), 'Authorization : Bearer abcdefghijklmn')`).
Сделано: иглы `AUTH_HEADER` → `("authorization", "x-api-key")`, шаблон не тронут. Инвариант закреплён таблицей: для каждой формы из `NEEDLE_FORMS` строка, которую её шаблон матчит, обязана содержать одну из её игл. `SENSITIVE_NAME` в тест не включена сознательно — она едет на префильтре сепаратора, а не на иглах (её иглы проверяются как `_names_a_secret(line)`, и `> x-api-key: …` их не содержит, но закрывается формой `AUTH_HEADER`).

**Латентность, p50 на 256 КБ, старые → новые иглы** (все восемь корпусов из `tests/inspect/test_secrets.py`, порог 5 мс):
`ordinary_log` 2.384 → 2.412 · `key_equals_dense` 2.875 → 2.957 · `token_hint_dense_lines` 3.179 → 3.202 · `secret_lines_one_in_fifty` 3.293 → 3.302 · `begin_without_end_single_line` 2.889 → 2.747 · `token_hint_dense_single_line` 4.257 → 2.681 · `jwt_hint_dense_single_line` 1.706 → 1.732 · `many_empty_lines` 0.603 → 0.607. Регрессии нет ни на одном, `ordinary_log` в пределах шума.

## 3. Провенанс шёл в промпт нередактированным — `065b576`
Падало: `tests/inspect/test_secrets.py` — `ImportError: cannot import name 'redact_line'`; `tests/inspect/test_classify.py::test_prompt_redacts_a_secret_in_the_provenance`.
Сделано: `secrets.redact_line(line)` — только распознанные формы, никогда энтропийные кандидаты (общий помощник `_apply_forms`, тот же цикл, что у `_scan_line`); `classify._provenance_line` пропускает через него каждое значение. Докстринг модуля `classify.py` теперь говорит про сегменты **и** провенанс, оба редактированные.

## 4. Докстринг `Span` — `721a2c7`
Сказано явно: координаты индексируют `output.split("\n")` **запроса**, а не поле `output` ответа, которое схлопнутый PEM делает короче. `contracts/inspect_response.schema.json` и `contracts/openapi.yaml` перегенерированы и в том же коммите; `tests/test_contracts.py` зелёный.

## 5. Модельный `mask` не поднимает stage-1 `drop` — `82bb1e6`
Ни один из трёх тестов не упал: `reconcile.py` уже вёл себя правильно, тесты закрепляют поведение. `test_mask_cannot_lift_a_stage_one_drop` (со спаном и без — во втором случае `error == "empty-spans"`), `test_classifier_mask_cannot_lift_a_stage_one_drop` (`to_response()` строится), и маршрутный `test_a_model_mask_cannot_lift_a_stage_one_drop_at_the_route` (HTTP 200, `drop`). Правок в `reconcile.py` не потребовалось.

## 6. Мелкие качественные — `2e0ebd7`
- **a.** `MODEL_SPAN_KINDS = tuple(k for k in get_args(SpanKind) if k != "secret")`, и `_ROLE` собирает список видов из неё (текст промпта посимвольно тот же). Чтобы `classify` мог читать константу без цикла импорта (`spans` → `classify` → `spans`), `ModelSpan` переехал в `spans.py` — модуль ровно про спаны модели; обновлены 3 места импорта в тестах. Новый тест `test_the_model_may_ask_for_every_span_kind_but_secret`.
- **b.** `mask._reason` считает строки, а не находки: падало `test_the_reason_counts_rewritten_lines_not_findings` (`'rewrote 1 line(s)'` вместо `5`). Теперь берёт число `mask`+`clean` строк из `by_line`.
- **c.** Докстринг `mask.resolve` объясняет, почему победители опознаются по `id()`, а не по значению.
- **d.** `segments.build` ограничивает головное окно `max_segments * segment_max_lines` строками: падало `test_the_head_window_is_capped_at_what_could_be_kept` (`omitted_segments == 98` вместо `0`); непоказанный хвост по-прежнему попадает в `omitted_lines` (980).
- **e.** Невыполнимый тест про «spans»/«secret» заменён на два — по фразам самого `_ROLE` (`\`secret\` is not a span kind you may return`; `[CANDIDATES] lists …` + `put a line number in \`unredact\``). Убран `assert not hasattr(outcome, "replacement")`.
- **f.** Импорты `Latency` и `Span` подняты в шапку `tests/engine/test_inspection.py` и `tests/store/test_repo.py`.

## Опасения
- **Пункт 2, вне восьми корпусов:** 256 КБ, целиком состоящие из строк `< authorization: Bearer …`, стоят ~24 мс и **до** правки (замерено на старых иглах: 24.7 мс). Правка лишь уравняла с ними написание с пробелом перед двоеточием (2.8 → 23.3 мс). Это свойство шаблона `AUTH_HEADER` на дампе заголовков, а не регрессия префильтра; корпус в тесты не добавлен, потому что он не пройдёт порог 5 мс. Если такой дамп реалистичен, шаблон стоит отдельно оптимизировать — это не входило в раунд.
- **Пункт 6a:** переезд `ModelSpan` из `classify.py` в `spans.py` — единственная структурная правка сверх буквы задания; она была нужна, чтобы `MODEL_SPAN_KINDS` осталась в `spans.py`, как просило задание, и при этом была видна `classify`. `agentgate.inspect.classify.ModelSpan` продолжает импортироваться (реэкспорт через `from … import`), внешние импортёры не ломаются.
- Документы (`service/CLAUDE.md` и прочие) не трогал — по пункту 7.
