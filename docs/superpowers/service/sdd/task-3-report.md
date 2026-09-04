# Задача 3 — `Rule` + `RuleChain`, разбор `hard_deny.py`

Коммит: `cb5da99` — `refactor(service): stage 1 becomes one chain of Rule objects`
Ветка: `refactor/solid-v1.5` (от `bab29cc`).

## Что построено

Ступень 1 — один список объектов в `agentgate/rules/chain.py`:

```
STAGE1 = RuleChain([UnparseableRule(), *HARD_DENY_RULES,
                    ProfilePathRule(), ProfileDomainRule(), AllowlistRule(), PackagesRule()])
HARD_DENY_RULES = [ExfilRule(), PipeExecRule(), DestructiveRule(), ProtectedWriteRule(),
                   PrivilegeRule(), GitForceRule(), WrapperUnresolvedRule()]
```

`agentgate/stage1/` (7 модулей, 1187 строк, из них `hard_deny.py` — 930) удалён целиком.
На его месте 18 модулей `agentgate/rules/`, 1275 строк; самый большой — `exfil.py` (363),
остальные 13–132.

Сделано, помимо переноса:

- `Rule` (Protocol: `id`, `hard`, `evaluate`) и `RuleChain` — один цикл, первый вердикт побеждает.
- `UnparseableRule` — единственное место, знающее про неразобранную команду (было три:
  `chain.py`, `Gate._evaluate`, `run_stage2`). Ветка `if action.flags.unparseable` удалена из
  `stage2/run.py`, константа `STAGE1_SKIPPED` — из `gate.py`.
- Хвостовой спецслучай `check_hard_deny` стал обычным правилом `WrapperUnresolvedRule`.
- `check_profile` разделён на `ProfilePathRule` и `ProfileDomainRule` (SRP: путь и сеть меняются
  по разным причинам).
- `Gate.__init__` принимает `rules: RuleChain` без дефолта; `STAGE1` передаётся из
  `__main__.build_app`, `tests/engine/test_gate.py::gate()` и `tests/test_api.py::build()`.

## Санкционированное изменение поведения (рулинг 1)

Неразобранное действие закрывается ступенью 1: `ask`, `stage: 1`, `rule_id: "unparseable"`,
`model: null`. Раньше — `stage: 2` с именем модели, которую никогда не вызывали.
Форма `DecideResponse` не изменилась (проверено пустым diff по `contracts/`), изменилось
значение поля `stage` для одного случая.

Регрессионные тесты на это: `tests/rules/test_chain.py::test_unparseable_is_settled_by_stage_one`
и `tests/engine/test_gate.py::test_unparseable_is_reported_as_stage_one_naming_no_model`
(сквозь весь Gate, включая `to_response()`).

## Результаты прогонов

```
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://... uv run pytest
572 passed in 8.00s
```

Было 543. Арифметика: −1 (`test_stage2_run.py::test_unparseable_action_short_circuits_without_calling_llm`,
тест удалённой ветки), −1/+2 (`test_check_alias_...` заменён двумя протокольными тестами),
−1/+2 (`test_unparseable_falls_through` заменён двумя), +5 `test_base.py`, +4 `test_unparseable.py`,
+7 `test_allowlist.py`, +5 `test_profile_paths.py`, +6 `test_profile_domains.py`,
+1 `test_gate.py`. Предупреждений нет.

Контракт:
```
uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
→ пустой diff
```

Грепы (оба пустые):
```
grep -rn "agentgate\.stage1\|stage1\." agentgate/ tests/ scripts/ | grep -v "stage1_ms\|stage1=\|latency_stage1\|\"stage1\"\|STAGE1"
grep -rniE "fix round|important [0-9]|critical [0-9]|task [0-9]|mid-round" agentgate/rules/
```

Латентность (`tests/rules/test_latency.py`, бюджет p50 ≤ 1 мс на normalize + ступень 1, 200 команд):

```
p50=0.1404ms  max=0.6700ms
p50=0.1382ms  max=0.2415ms
p50=0.1393ms  max=0.3324ms
```

Регрессии нет, запас 7×. Порог не трогал.

## Доказательства TDD (шаги 1–4)

**Шаг 1 (RED).** `tests/rules/test_base.py` написан до `agentgate/rules/base.py`.

```
$ uv run pytest tests/rules/test_base.py -v
tests/rules/test_base.py:2: in <module>
    from agentgate.rules.base import RuleChain
E   ModuleNotFoundError: No module named 'agentgate.rules'
1 error in 0.11s
```

Ожидаемо: пакета `agentgate.rules` ещё нет.

**Шаг 2 (GREEN).** После `agentgate/rules/base.py`:

```
$ uv run pytest tests/rules/test_base.py -q
5 passed in 0.12s
```

**Шаг 3 (RED).** `tests/rules/test_unparseable.py` до `agentgate/rules/unparseable.py`:

```
$ uv run pytest tests/rules/test_unparseable.py -q
E   ModuleNotFoundError: No module named 'agentgate.rules.unparseable'
1 error in 0.10s
```

**Шаг 4 (GREEN).**

```
$ uv run pytest tests/rules -q
9 passed in 0.18s
```

Тесты написаны сразу на `unparseable_action()` из `tests/factories.py`, без
`object.__setattr__` по флагу (вариант, предложенный в брифе как предпочтительный).
Строка взята не `ls -la $(`, а `echo "unterminated` — обе дают `flags.unparseable is True`
(проверено), вторая уже покрыта `tests/test_normalize_shell.py`.

## Страховка на механическом переносе (шаги 5–8)

Табличные тесты — не единственная сеть. Перед удалением `stage1/` прогнан дифференциальный
прогон старой `check_hard_deny` против новой `RuleChain(HARD_DENY_RULES)` по всем 187
уникальным действиям (`DENY_CASES` + `PASS_CASES` + все ad-hoc кейсы из тест-модуля + 4
`file_write`), со сравнением **всех восьми полей** вердикта
(`decision, stage, rule_id, reason, suggest, hard, model, error`), а не только `rule_id`:

```
compared 187 actions, 0 mismatches
```

Скрипт лежал в scratchpad и в репозиторий не попал.

## Решения shared vs private (грепом, не по памяти)

Правило: помощник, которым пользуется ровно одно правило, уезжает в модуль этого правила и
остаётся приватным; которым пользуются два и больше — в `hard_deny/shared.py`.

**Это расходится со списком в шаге 5 брифа.** Бриф перечислил для `shared.py` ~20 имён;
греп показал, что 14 из них имеют ровно одного клиента — `ExfilRule`. Буквальное следование
списку дало бы «`shared.py`, который есть просто переименованный старый файл» — ровно то, от
чего предостерегала постановка задачи. Ниже — что и почему, с номерами строк исходного
`stage1/hard_deny.py`.

### В `shared.py` (два и более клиента)

| Имя | Клиенты | Греп |
|---|---|---|
| `effective_argv` (`_effective`) | все шесть правил + wrapper | строки 223, 397, 491, 554, 582, 593, 640, 706, 739, 828 |
| `DOWNLOADERS` | `ExfilRule` (`_consumes_piped_stdin`, 527), `PipeExecRule` (584, 595) | 2 модуля |
| `WRITE_COMMANDS` → `LAST_ARG_WRITE_COMMANDS` | `ExfilRule` (`_excluded_read_paths`, 470), `ProtectedWriteRule` (718) | 2 модуля |
| `by_pipeline` (`_by_pipeline`) | `ExfilRule` (551), `PipeExecRule` (579) | 2 модуля |
| `wrapper_chain_unresolved`, `_consumed_a_possible_command`, `_EFFECTIVE_WRAPPERS` | формально один клиент | см. ниже |

`WRITE_COMMANDS` остался приватным (`_WRITE_COMMANDS`): его никто не импортирует, он нужен
только чтобы вывести `LAST_ARG_WRITE_COMMANDS` (гайд 4.3 — подчёркивание снимается только у
того, что импортирует другой модуль). По той же причине `_EFFECTIVE_WRAPPERS` и
`_consumed_a_possible_command` остались приватными внутри `shared.py`.

**Осознанное исключение из правила: `wrapper_chain_unresolved`.** Его клиент один —
`WrapperUnresolvedRule`. Оставил в `shared.py`, как и говорит бриф, по двум причинам:
(1) это то же знание, что и `effective_argv` — какие команды являются обёртками, насколько
глубоко разворачивается цепочка и что значит «развернуть не удалось»; разнесение оставило бы
два модуля с мнением о том, что такое обёртка (гайд 1.3); (2) перенос потребовал бы сделать
`EFFECTIVE_WRAPPERS` публичным ради одного импорта.

### Приватно в модуле правила (ровно один клиент — проверено грепом)

- `exfil.py`: `_SECRET_PATTERNS` (строки 94→213, только `_is_secret`), `_is_secret` (557, 573 —
  обе в `_rule_exfil`), `_NETWORK_COMMANDS` (429, 556), `_UPLOAD_FLAGS`,
  `_SHORT_UPLOAD_LETTERS`, `_CLUSTERING_UPLOAD_COMMANDS`, `_IGNORE_VALUE_FLAGS`,
  `_SCP_RSYNC_VALUE_FLAGS`, `_REMOTE_DEST`, `_STDIN_FORWARDING_COMMANDS`, `_flag_value`,
  `_looks_remote`, `_positional_args`, `_match_upload_flag`, `_upload_flag_value_paths`,
  `_cmd_paths`, `_sent_secret_paths`, `_excluded_read_paths`, `_read_role_paths`,
  `_consumes_piped_stdin`.
- `pipe_exec.py`: `_SHELLS`, `_INTERPRETERS` (`SHELLS` использовалась только для вывода
  `INTERPRETERS` и в 594; `INTERPRETERS` — только в 586).
- `destructive.py`: `_NARROWING_PREDICATES`, `_TRIVIAL_PREDICATE_VALUES`,
  `_has_narrowing_predicate`.
- `privilege.py`: `_FIREWALL` (единственное использование — 747), `_ESCALATORS`,
  `_WORLD_WRITABLE_MODES`.
- `git_force.py`: `_GLOBAL_OPTS_WITH_VALUE`, `_SYMBOLIC_REFS`, `_push_argv`, `_is_force_flag`,
  `_destination_branch`.

Циклических импортов нет: правила импортируют `agentgate.rules.hard_deny.shared` напрямую
(`from ... .shared import ...`), а не через пакет.

## Комментарии

Из `agentgate/rules/` вычищены все ссылки на процесс («fix round N», «Important N»,
«Critical N», «mid-round amendment», «verified by direct reproduction», номера задач). В коде
остался инвариант в одну-две фразы. Вырезанное не выброшено: дописано в
`docs/reports/task-5-hard-deny.md` разделом «История ревью правил hard-deny (перенесено из
комментариев, рефакторинг v1.5)» — по подразделу на модуль (`shared`, `exfil`, `pipe_exec`,
`destructive`, `protected_write`, `privilege`, `git_force`, `wrapper_unresolved`) плюс общий
раздел из модульного docstring (три исхода, разбор `has_unresolved_expansion`).

## Мелкие эквивалентные упрощения при переносе

Все проверены дифференциальным прогоном (0 расхождений):

- `destructive`: два флага `deny_on_ws_equal` и `deny_on_ws_equal_unnarrowed` всегда
  использовались только под `or` и никогда не выставлялись оба — свёрнуты в один
  `deny_on_workspace_equal` на frozen dataclass `_Deletion`; выбор целей вынесен в `_deletion()`.
- `protected_write`: условие `a == "-i" or a.startswith("-i") or a == "--in-place" or
  a.startswith("--in-place=")` → `a.startswith(("-i", "--in-place"))` (то же множество).
- `allowlist`: `r.op.endswith(">") or r.op.endswith(">>")` → `endswith((">", ">>"))`;
  четыре `X not in cmd.argv` → пересечение множеств; ветки file_read/file_write через
  `_paths_are_safe`.
- `privilege`, `git_force`: тела разложены на приватные методы, порядок проверок сохранён.
- `exfil`: `name, val = matched` → `_, val = matched` (переменная не использовалась).
- `wrapper_unresolved`: `" ".join(argv[:4])` вычисляется только когда цепочка действительно
  не развернулась (в исходнике — тоже внутри веток; при первом переносе я поднял его наверх и
  вернул назад при самопроверке).

## Тесты

- `tests/test_stage1_hard_deny.py` → `tests/rules/hard_deny/test_rules.py` (`git mv`).
  **Ни одно ожидание не изменено**: `DENY_CASES` (100), `PASS_CASES` (45) и все assert'ы
  побайтово те же. Изменены только шапка (фикстуры из `tests/factories.py`, локальный
  `check_hard_deny` поверх `RuleChain(HARD_DENY_RULES)`), `shell(` → `shell_action(`,
  и два теста, которые импортировали удалённые символы:
  `test_check_alias_matches_check_hard_deny_signature` (импортировал `stage1.types.Check`) →
  `test_every_hard_deny_rule_declares_itself_hard` + `test_every_rule_has_an_id`;
  `_rule_exfil` → `ExfilRule().evaluate`.
- `tests/test_stage1_chain.py` → `tests/rules/test_chain.py`. `run_stage1(a, p)` →
  `STAGE1.evaluate(a, p)`, `check_allowlist` → `AllowlistRule().evaluate`, локальный
  `make_profile` → `tests.factories.stage1_profile`. Единственное изменённое ожидание —
  `test_unparseable_falls_through` (санкционированный рулинг 1), заменён двумя тестами.
  Починен `parents[1]` → `parents[2]` в пути к `service/profiles` (файл уехал на уровень глубже).
- `tests/test_stage1_latency.py` → `tests/rules/test_latency.py`, импорт из `tests.factories`
  вместо `tests.test_stage1_chain` (правило «тесты не импортируют друг друга»).
- Новые: `tests/rules/test_base.py`, `test_unparseable.py`, `test_allowlist.py`,
  `test_profile_paths.py`, `test_profile_domains.py` — по файлу на модуль, как в списке файлов
  брифа. Последние три маленькие и целятся в то, что именно доказывает разделение:
  `ProfilePathRule` молчит про домены, `ProfileDomainRule` молчит про пути.
- `tests/factories.py`: добавлены `stage1_profile()`, `hard_deny_profile()`, `shell_action()`,
  `unparseable_action()`.
- Удалён `tests/test_stage2_run.py::test_unparseable_action_short_circuits_without_calling_llm`
  вместе с хелперами `unparseable_action`/`counting_client` — он тестировал ровно ту ветку,
  которую шаг 9 велел удалить. Его смысл переехал в `tests/rules/test_unparseable.py` и в
  gate-тест про `stage: 1`.

## Отступления и то, что не сделано

1. **Шаг 12, правка спеки — НЕ СДЕЛАНА, нужна от владельца.**
   `service/CLAUDE.md` прямо запрещает: спека «читать можно, менять нельзя», `docs/` (кроме
   `docs/reports/`) вне зоны записи, и «если задача требует файла вне разрешённого списка —
   не делать её молча, а сообщить и остановиться». То же в памяти пользователя («AgentGate
   scope: service/ only»). Проектная инструкция выше указания в брифе, поэтому
   `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md` я не трогал.
   **Строка 174 (§5.1) сейчас описывает старое поведение** («флаг `unparseable=true`, действие
   идёт в ступень 2 напрямую») и требует замены на:

   > Неразобранное действие (`flags.unparseable`) закрывается ступенью 1 правилом
   > `unparseable`: `ask`, `stage: 1`, `model: null`. Классификатор не вызывается — он
   > отвечал бы о команде, которую не видел.

   Это отражено в тексте коммита, чтобы расхождение не потерялось.
2. **Комментарии в тест-файлах не чистил.** В `tests/rules/hard_deny/test_rules.py` и
   `test_chain.py` осталось ~34 строки вида `# --- fix round 2, Important C: ... ---`. Мандат
   на чистку в брифе (шаг 5) и в чек-листе самопроверки ограничен `agentgate/rules/`; правка 34
   комментариев в файле, про который надо доказать, что 546 ожиданий не поехали, усложняет
   ревью ради того, о чём не просили. Если это нужно — отдельным коммитом «comments only»,
   он проверяется одним `git diff --stat`.
3. **Отчёта в `docs/reports/` по этой задаче нет.** Задачи 1 и 2 этого рефакторинга (`449eb8d`,
   `bab29cc`) его тоже не писали, а имя `task-3-*.md` уже занято отчётом задачи 3 из v1
   (`task-3-profiles.md`). В `docs/reports/` дописана только история ревью hard-deny, как
   требовал бриф.
4. **`agentgate/stage2/prompt.py`** — правка одного абзаца docstring, которого нет в списке
   файлов брифа: он утверждал, что неразобранное действие отклоняет `run_stage2`. После шага 9
   это стало ложью.
5. **Процессные комментарии в `agentgate/normalize/`** (`paths.py`, `shell.py`) остались — это
   не территория этой задачи.

## Замечания

- **`exfil.py` — 363 строки**, вдвое больше следующего по размеру модуля. Это вся машинерия
  направления (11 констант, 10 помощников), у неё ровно один клиент, и по правилу
  shared/private она обязана лежать здесь. Читается за один присест с натяжкой. Если нужно
  дробить дальше — естественный шов проходит по разбору флагов выгрузки
  (`_match_upload_flag`, `_upload_flag_value_paths`, `_SHORT_UPLOAD_LETTERS`,
  `_CLUSTERING_UPLOAD_COMMANDS`) против трекера направления в пайпе. Не делал: бриф просит
  один модуль на правило.
- **`AllowlistRule.id == "allowlist"`, а вердикты возвращают `allowlist.readonly` /
  `allowlist.prefix` / `allowlist.file_read` / `allowlist.file_write`.** То же у
  `WrapperUnresolvedRule` (`id = "ambiguous.wrapper"`, вердикты — `ambiguous.wrapper-depth` /
  `-opaque`) и `GitForceRule` (`id = "hard-deny.git-force"`, но ask'и — `ambiguous.git-force`).
  Так предписано брифом и так требуют существующие ожидания; `Rule.id` — имя правила, а не
  обещание про `rule_id` вердикта. Если это станет контрактом, понадобится отдельное решение.
- В рабочем дереве лежит **чужая незакоммиченная правка**
  `docs/superpowers/service/plans/2026-09-04-agentgate-v1.5-solid-refactor.md` (заметка про
  регресс `profile_hash` при `api.unknown-model`, найденный ревью задачи 2). Я её не трогал и
  не коммитил.
