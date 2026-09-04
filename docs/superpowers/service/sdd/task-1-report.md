# Отчёт: Task 1 — `Verdict`, один тип исхода вместо двух

Ветка `refactor/solid-v1.5`, коммит `449eb8d` (родитель `a878160`).

## Что построено

- `service/agentgate/domain/verdict.py` — `Verdict`, frozen dataclass с полями `decision`, `stage`, `rule_id`, `reason`, `suggest`, `hard`, `model`, `raw_response`, `error`; классметоды `allow`/`deny`/`ask` и метод `escalated(hits)`.
- `service/agentgate/engine/timings.py` — `Timings` (секундомер с контекст-менеджером `stage(number)` и `finish() -> Latency`) и `Latency` (frozen dataclass с `to_schema() -> LatencyMs`).
- `service/agentgate/domain/__init__.py`, `service/agentgate/engine/__init__.py`, `service/tests/domain/__init__.py`, `service/tests/engine/__init__.py` — пустые пакеты.
- Ступень 1 (`stage1/types.py`, `hard_deny.py`, `allowlist.py`, `profile_check.py`, `packages.py`, `chain.py`) переведена на `Verdict`: `Stage1Decision` удалён, `_deny`/`_ask` в `hard_deny.py` удалены, каждый вызов заменён на `Verdict.deny(..., hard=True)` / `Verdict.ask(...)`. Возвращаемые аннотации везде `Verdict | None`.
- Ступень 2 (`stage2/run.py`) переведена на `Verdict`: `Stage2Result` удалён, `except Exception` теперь логируется через `log.warning(..., exc_info=True)` (было немым — гайд 7.2).
- `pipeline.py`: распаковка в десять локальных (`decision, reason, suggest, stage, rule_id, model_used, raw_resp, error, stage2_ms, hard`) заменена на работу с одним `verdict`. Эскалация — через `verdict.escalated(hits)`. Локальные `t0/t1/t2/s1/s2/_ms` удалены, используется `Timings`/`Latency`.
- `session/state.py`: добавлен `SessionState.reset_after_escalation()` — пайплайн больше не трогает `deny_consecutive`/`recent` напрямую.

## Тестирование

Полный прогон (после докстринг-фикса, см. «Находки саморевью»):

```
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q
```
```
524 passed in 7.46s
```
524 = 509 исходных + 10 (`test_verdict.py`) + 5 (`test_timings.py`). Вывод без предупреждений (проверено отдельным прогоном с `-W error::DeprecationWarning` — тоже 524 passed).

Контракт:
```
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
```
Пустой diff, код возврата 0 — публичный контракт не изменился.

### TDD-доказательства

**`Verdict` — RED:**
```
$ uv run pytest tests/domain/test_verdict.py -v
...
E   ModuleNotFoundError: No module named 'agentgate.domain.verdict'
=========================== short test summary info ============================
ERROR tests/domain/test_verdict.py
```
Ожидаемо: модуля ещё не существует.

**`Verdict` — GREEN:**
```
$ uv run pytest tests/domain/test_verdict.py -v
...
============================== 10 passed in 0.05s ==============================
```

**`Timings`/`Latency` — RED:**
```
$ uv run pytest tests/engine/test_timings.py -v
...
E   ModuleNotFoundError: No module named 'agentgate.engine.timings'
```
Ожидаемо: модуля ещё не существует.

**`Timings`/`Latency` — GREEN:**
```
$ uv run pytest tests/engine/test_timings.py -v
...
=============================== 5 passed in 0.05s ===============================
```

После этого ступень 1 (`test_stage1_hard_deny.py` — 183 теста, `test_stage1_chain.py`, `test_stage1_latency.py`) и ступень 2 (`test_stage2_run.py`) прогонялись после каждой миграции модуля — все зелёные без правок ожиданий (`Verdict` совпадает по именам полей со старыми типами, как и предполагал бриф).

## Файлы

Создано:
- `service/agentgate/domain/__init__.py`, `service/agentgate/domain/verdict.py`
- `service/agentgate/engine/__init__.py`, `service/agentgate/engine/timings.py`
- `service/tests/domain/__init__.py`, `service/tests/domain/test_verdict.py`
- `service/tests/engine/__init__.py`, `service/tests/engine/test_timings.py`

Изменено:
- `service/agentgate/stage1/types.py`, `hard_deny.py`, `allowlist.py`, `profile_check.py`, `packages.py`, `chain.py`
- `service/agentgate/stage2/run.py`
- `service/agentgate/pipeline.py`
- `service/agentgate/session/state.py`

## Принятые решения по ходу работы

- **Порядок импортов**: `from agentgate.domain.verdict import Verdict` вставлен в алфавитном месте (после `agentgate.api.schemas`, перед `agentgate.normalize.*`) во всех файлах — соответствует уже принятому в репозитории стилю импортов.
- **Неиспользуемый `DecisionKind`**: бриф явно просил убрать неиспользуемый импорт `DecisionKind` в `allowlist.py` и `profile_check.py`. По тому же принципу убран и в `hard_deny.py` — после удаления `_deny`/`_ask` (единственных мест, где он использовался) импорт стал мёртвым; оставлен только `Tool`, который используется.
- **`hard=True` в `Verdict.deny(...)` для hard-deny правил**: бриф формулирует замену как `_deny("<rule>", reason, suggest)` → `Verdict.deny(f"hard-deny.<rule>", reason, suggest, hard=True)` — я применил `hard=True` ко всем десяти вызовам `Verdict.deny` внутри `hard_deny.py` (exfil, pipe-exec×2, destructive, protected-write, privilege×4, git-force), поскольку все они раньше шли через удалённый `_deny`, который жёстко проставлял `hard=True`.
- **Докстринг `_rule_git_force`/`check_hard_deny`/module-level в `hard_deny.py`**: бриф не просил чистить множественные ссылки на «fix round N» / «Important N», разбросанные по этому файлу (их там очень много, в основном в докстрингах). Единственная явная инструкция на чистку такого рода — для `stage2/run.py` («убрав из него ссылку на «Task 4» и «Task 7»»), но в текущей версии файла таких ссылок не оказалось (либо бриф писался по другой версии кода, либо это было исправлено раньше) — инструкция оказалась no-op. Решил не расширять объём задачи чисткой докстрингов `hard_deny.py`, которую бриф не запрашивал — это отдельная, большая по объёму работа, не относящаяся к консолидации типов.
- **Точечные докстринг-правки при переименовании**: в трёх местах, где докстринг явно называл удаляемый тип (`hard_deny.py`: «a Stage1Decision with hard=True is final» → «a Verdict…»; `pipeline.py`: «never Stage1Decision.reason/suggest» → «never Verdict.reason/suggest», в двух местах), заменил имя типа на `Verdict` — без этого докстринг стал бы фактически неверным (ссылался бы на несуществующий тип). Это не «fix round»-правка, а прямое следствие переименования, которое сам бриф выполняет по всему остальному коду.

## Находки саморевью и как закрыты

- При реализации `stage2/run.py` через `Write` (не `Edit`) я по невнимательности заменил символ «—» (em dash) на «--» в фразе «resolves to DecisionKind.ask — never allow, never deny» — бриф требовал сохранить докстринг как есть, кроме переименования типа. Нашёл при построчном self-review диффа (`git diff -- service/agentgate/stage2/run.py`), вернул исходный «—» до коммита.
- Проверил импорты во всех тронутых файлах статическим AST-анализом (imported vs used) — неиспользуемых импортов не осталось.
- Проверил, что в добавленных/изменённых строках коммита нет ссылок на «Task N», «fix round», «Important N» — `git show HEAD | grep -iE "task [0-9]|fix round|important [0-9]"` пусто (кроме `Co-Authored-By`).
- `ruff` в окружении не установлен (`No such file or directory`) — линт не прогонялся; полагался на компиляцию (`py_compile`) и ручной просмотр диффа.

## Отложено / не в объёме этой задачи

- Массовая чистка докстрингов `stage1/hard_deny.py` от исторических пометок «fix round N», «Important N» — их там десятки, разбросаны по модульному докстрингу и докстрингам почти каждой приватной функции. Бриф Task 1 такую чистку не запрашивал явно (в отличие от `stage2/run.py`, где инструкция была явной, но оказалась no-op в текущей версии файла). Похоже на отдельную задачу уборки истории в `docs/reports/`, а не часть консолидации типов.
- Пре-существующая ссылка «task 7 review» в докстринге `pipeline.py` (строка про `[STAGE1]`-заглушку) не тронута — вне зоны правок брифа для этого файла.
- Сборка `DecideResponse`/`DecisionRecord` из `verdict` по одному полю — сознательно оставлена как есть (бриф явно относит полную передачу `Verdict` целиком к задаче 2).
