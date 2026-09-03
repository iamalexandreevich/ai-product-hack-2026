# Task 10 — JSONL-лог и конвейер `Gate.decide()`

**Статус:** закрыт. **Ветка:** `feat/agentgate-task-1`. **Базовый коммит:** `925e1f3` (merge task 6, после исправления базы worktree — см. «Решения» ниже).

## Что построено

| Файл | Содержимое |
|---|---|
| `service/agentgate/log/jsonl.py` | `class JsonlLogger(path)`: `write(record: dict) -> None` — создаёт родительский каталог, дописывает одну строку `json.dumps(..., ensure_ascii=False, default=str)`; ошибка записи (`OSError`) ловится и логируется через `logging.warning`, наружу не пробрасывается |
| `service/agentgate/pipeline.py` | `class Gate(profiles, default_profile, state_store, http, persist=None, cache_ttl_seconds=86400)`; `async decide(req) -> tuple[DecideResponse, DecisionRecord, SessionState \| None]` — конвейер, склеивающий задачи 2–9 |

Реализация выполнена по эталонному коду брифа `docs/superpowers/service/sdd/task-10-brief.md` (Step 4–5) почти дословно — с одним намеренным отступлением от текста (раздел «Отступление от брифа» ниже) и одним усилением, продиктованным явным требованием самого задания (обёртка `try/except` вокруг `persist`, см. «Constraint 3»).

Перед написанием кода каждый импортируемый брифом символ сверен с уже смёрженной реализацией задач 2–9: `DecideRequest`/`DecideResponse`/`DecisionKind`/`LatencyMs`/`Tool` (`agentgate.api.schemas`), `Profile`/`ModelsConfig.model_config_for`/`with_workspace` (`agentgate.profiles`), `normalize` (`agentgate.normalize`), `run_stage1`/`Stage1Decision` (`agentgate.stage1.chain`/`types`), `LLMClient`/`run_stage2`/`Stage2Result` (`agentgate.stage2.client`/`run`), `SessionState`/`SessionStateStore`/`InMemorySessionStateStore`/`should_escalate`/`allow_cache_key` (`agentgate.session.*`), `DecisionRecord`/`DecisionRepo`/`SessionRepo` (`agentgate.store.repo`) — расхождений с брифом по сигнатурам не найдено.

## Порядок конвейера — как реализовано

профиль (неизвестный `profile_id` → `ask`, `stage=0`, `api.unknown-profile`) → неизвестная `model` → `ask` (`api.unknown-model`) → `with_workspace` → `normalize` → кэш `allow` → ступень 1 → (если `None`, включая случай `flags.unparseable`) ступень 2 → эскалация (только если решение не hard-deny и ещё не `ask`) → запись состояния сессии (`state.record` + `save`) → кэш `allow` (`cache_put`, только для `decision == allow`) → сборка `DecisionRecord`.

## Пять сквозных требований — как закрыты

1. **`[STAGE1]` — фиксированный словарь, не `Stage1Decision.reason`.** В `pipeline.py` заведены константы `_NOTE_PASSED = "passed: no hard-deny match, not in allowlist"` и `_NOTE_SKIPPED = "skipped: command unparseable"`; в `run_stage2` передаётся `note` — одна из этих двух строк, выбранная только по `action.flags.unparseable`. `s1.reason`/`s1.suggest` идут исключительно в `DecideResponse`, возвращаемый вызывающей стороне, и никогда не строкой `[STAGE1]`.
2. **Ретраев к LLM нет.** `LLMClient(model_name, model_cfg, self._http)` строится на переданном `httpx.AsyncClient` без какой-либо настройки транспортных ретраев; вызов `client.classify` внутри `run_stage2` (код задачи 7, не тронут) делает ровно один HTTP-запрос с одним таймаутом.
3. **Персист после ответа, ошибка персиста не меняет решение.** `_do_persist` вызывается после того, как `resp`/`rec` уже построены, и никогда не ожидается на пути, формирующем решение. В отличие от буквального текста брифа (там `_do_persist` не ловит исключение), здесь добавлена обёртка `try/except Exception` с `log.exception(...)` — прямое следствие явного требования задания «Persist callback's own failures are swallowed (logged), never propagated to the caller». Подтверждено тестом `test_persist_failure_does_not_change_or_raise_the_decision` (см. TDD ниже) — без обёртки тест падает с `RuntimeError`, что доказывает, что защита не заглушка, а реально работающий код.
4. **Порядок FK-записей.** `Gate` сам ничего не пишет в БД — `persist` инжектируется вызывающей стороной (Task 11). Требуемый порядок (`SessionRepo.upsert` → `DecisionRepo.insert` → `AllowCacheRow` только после того, как строка решения уже существует) зафиксирован докстрингами `agentgate.store.repo.DecisionRepo`/`SessionRepo` (не тронуты) и повторён в докстринге `pipeline.py` как обязанность конкретной реализации `persist`, которую Task 11 обязан соблюдать.
5. **Кэшируется только `allow`; hard-deny никогда не кэшируется и не эскалируется.** `cache_put` вызывается только внутри `if decision is DecisionKind.allow`. Эскалация обусловлена `not hard and decision is not DecisionKind.ask` — hard-deny (`s1.hard=True`) физически не может попасть в ветку эскалации. Оба свойства покрыты тестами `test_deny_not_cached` и `test_hard_deny_has_reason_and_is_not_escalated_to_ask`.

## Отступление от брифа — тест на unparseable-действие

Бриф предписывал тест `test_unparseable_goes_to_llm` с утверждением `llm.calls == 1`. При сверке с уже смёрженным и отревьюженным кодом задачи 7 обнаружено прямое противоречие: `agentgate/stage2/run.py::run_stage2` содержит явную защиту —

```python
if action.flags.unparseable:
    return Stage2Result(DecisionKind.ask, "action could not be structurally parsed and was never verified", "", model_name, None, None)
```

— которая закрывает `Stage2Result` **до** построения промпта и **без** единого HTTP-вызова. Это не случайность: коммит `2d3cc47` ("fix(service): close two stage2 prompt injections, refuse unparseable actions before the LLM") и уже существующий именованный тест `tests/test_stage2_run.py::test_unparseable_action_short_circuits_without_calling_llm` (`calls["n"] == 0`, тот же входной пример `'echo "unterminated'`) фиксируют это поведение как сознательное закрытие prompt-injection дыры ревью задачи 7: дать непарсящемуся действию дойти до LLM значило бы доверить классификатору отметку `unparseable=true` в тексте промпта, которую сам классификатор не обязан уважать.

Слепое воспроизведение брифового теста означало бы либо (а) написать тест, который никогда не станет зелёным без отмены фикса задачи 7, либо (б) отменить сам фикс — то и другое напрямую нарушает «fail-closed — стержень конвейера» и «hard-deny/защиты не переопределяются» из вводных этой задачи. Вместо этого тест переименован в `test_unparseable_skips_llm` с `llm.calls == 0`; поведение (результат — `ask`, `rec.normalized["flags"]["unparseable"] is True`) сохранено. Комментарий в теле теста объясняет расхождение и даёт точную ссылку на защищающий его тест задачи 7.

## TDD

- **RED:** `uv run pytest tests/test_log.py tests/test_pipeline.py -v` (оба тестовых файла написаны первыми, до единой строки `jsonl.py`/`pipeline.py`) → ошибка сбора обоих файлов: `ModuleNotFoundError: No module named 'agentgate.log.jsonl'`, `ModuleNotFoundError: No module named 'agentgate.pipeline'` — совпадает с ожиданием брифа (Step 3) дословно.
- **GREEN:** тот же запуск после реализации `log/jsonl.py` и `pipeline.py` → `14 passed`.
- Отдельный RED/GREEN для персиста: добавлен `test_persist_failure_does_not_change_or_raise_the_decision`; временно откачена обёртка `try/except` в `_do_persist` → тест падает с `RuntimeError: db is down` (доказательство, что тест реально проверяет защиту, а не подтверждает всегда-зелёный код); обёртка возвращена → `15 passed`.
- Полный набор: `uv run pytest -q -W error` → `391 passed, 19 skipped` (376 унаследованных из задач 1–9 + 15 новых; 19 skip — тесты хранилища без боевой БД, ожидаемо). С `AGENTGATE_TEST_DB_URL` — `410 passed` (395 + 15). Варнингов нет в обоих случаях.

## Покрытые сценарии (`test_pipeline.py`)

- `test_stage1_allow_skips_llm` — `allow` со ступени 1, `llm.calls == 0`, `model is None`, `latency_ms.stage2 is None`.
- `test_hard_deny_has_reason_and_is_not_escalated_to_ask` — при `deny_consecutive=1` первый `sudo ls` уже поднимает счётчик до порога, но следующий `hard-deny.pipe-exec` не заменяется на `ask` эскалацией.
- `test_gray_zone_goes_to_llm_and_maps` — серая зона уходит в LLM, маппинг `D`→`deny`, `stage=2`, `reason`/`suggest` из ответа модели, `model_raw_response` сохранён.
- `test_llm_failure_is_ask` — HTTP 500 от классификатора → `ask`, `rec.error == "http"`.
- `test_unparseable_skips_llm` — см. «Отступление от брифа» выше.
- `test_allow_cache_hit` — второй идентичный запрос берётся из кэша (`cached=True`, `stage=0`, `llm.calls` не растёт); запрос с другим `user_request` кэш не пробивает.
- `test_deny_not_cached` — `deny` не кэшируется, LLM вызывается заново на идентичном запросе.
- `test_escalation_forces_ask` — два `deny` подряд поднимают эскалацию на третьем запросе (`rule_id="escalation"`), счётчики сбрасываются, следующий безобидный запрос снова `allow`.
- `test_no_session_id_means_no_counters_and_no_cache` — без `session_id` нет ни состояния, ни кэша: LLM вызывается на каждый запрос.
- `test_unknown_profile_and_model_are_ask` — оба ранних выхода (`api.unknown-profile`, `api.unknown-model`), `stage=0`.
- `test_model_override_is_used` — `model="m2"` переопределяет дефолтную модель профиля, запрос уходит на `base_url` из `m2`.
- `test_persist_called_with_record` / `test_persist_failure_does_not_change_or_raise_the_decision` — персист вызывается с готовой записью; его сбой не меняет и не пробрасывает решение (см. Constraint 3 выше).

## Дисциплина по scope

Реализовано ровно то, что перечисляет бриф: `JsonlLogger`, `Gate`, два тестовых файла. Ступень 1, ступень 2, хранилище и сессионная логика не переписаны — `pipeline.py` только импортирует и склеивает готовые `run_stage1`/`run_stage2`/`LLMClient`/`should_escalate`/`allow_cache_key`/`SessionStateStore`. `service/.env` не читался, не печатался, не перемещался. `git status --short` после работы показывает ровно:

```
?? service/agentgate/log/
?? service/agentgate/pipeline.py
?? service/tests/test_log.py
?? service/tests/test_pipeline.py
```

## Отложено (в следующие задачи)

- Реальная реализация `persist`, вызывающая `SessionRepo.upsert` → `DecisionRepo.insert` → `AllowCacheRow` в правильном порядке (FK) — это Task 11 (сборка FastAPI-приложения и продовый персист); здесь только зафиксирован контракт и докстринг-требование к порядку.
- Восстановление `allow_cache` из `SessionRepo.cache_load_valid()` после рестарта, используя `normalized["cache_key"]`, который `_record` уже пишет в каждую запись, — тоже Task 11.

## Изменённые файлы

- `service/agentgate/log/__init__.py` (новый, пустой)
- `service/agentgate/log/jsonl.py` (новый)
- `service/agentgate/pipeline.py` (новый)
- `service/tests/test_log.py` (новый)
- `service/tests/test_pipeline.py` (новый)
- `reports/task-10-pipeline.md` (этот файл)

## Решения, принятые за пользователя

### Исправление базового коммита worktree

Worktree был создан харнессом от `a9a0edd` (docs: план на 13 задач), а не от требуемого `925e1f3` (merge task 6). Рабочее дерево было чистым, `git merge-base --is-ancestor HEAD 925e1f3` подтвердил чистый fast-forward — выполнен `git reset --hard 925e1f3`. После сброса подтверждено: все семь файлов из чек-листа задачи существуют (`stage1/chain.py`, `stage2/run.py`, `session/state.py`, `session/memory.py`, `store/repo.py`, `normalize/__init__.py`, `profiles/loader.py`), `uv run pytest -q` даёт `376 passed, 19 skipped` до начала работы — совпадает с ожиданием брифа дословно.

### Тест на unparseable-действие переименован и его утверждение о `llm.calls` изменено

См. раздел «Отступление от брифа» выше — решение мотивировано прямым противоречием с уже смёрженным и явно протестированным (по имени и содержанию) фиксом задачи 7, а не эстетическим предпочтением.

### Добавлена защита `persist` от исключений

Буквальный код брифа для `_do_persist` не ловит исключение из `persist`. Общее требование задания («Constraint 3: persist callback's own failures are swallowed (logged), never propagated to the caller») и раздел «Fail-closed is the spine» явно требуют такую защиту и явно просят тест на неё («persist that raises»). Добавлена одна `try/except Exception` вокруг `await self._persist(rec, state)` с `log.exception`; RED/GREEN-доказательство приведено в разделе TDD.
