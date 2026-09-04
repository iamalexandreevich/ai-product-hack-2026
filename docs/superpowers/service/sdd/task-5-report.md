# Задача 5 — `Policy`: workspace привязывается к сессии

Закрывает **F4** — единственную находку ревью с последствиями для безопасности. Реализует §6 спеки,
которую код не выполнял. Задача сознательно меняет поведение (рулинг 2 в `global-constraints.md`),
поэтому начинается не с рефакторинга, а с красного регрессионного теста.

## 1. RED — четыре теста воспроизводят таблицу F4

Файл: `service/tests/domain/test_workspace_binding.py` (написан первым, до единой правки в
`agentgate/`; единственное, что добавлено в исходники до прогона, — фабрика `gate_for_binding_tests()`
в `tests/factories.py`, которая ничего не чинит, а только собирает `Gate` с профилем
`allowed_paths: ["${WORKSPACE}", "/tmp/agentgate-scratch"]` и `FakeLLM`, всегда отвечающим `A`).

Команда:

```bash
cd service && uv run pytest tests/domain/test_workspace_binding.py -v
```

Результат на текущем коде:

```
tests/domain/test_workspace_binding.py::test_first_request_of_a_session_fixes_the_workspace FAILED [ 25%]
tests/domain/test_workspace_binding.py::test_a_later_cwd_change_does_not_widen_allowed_paths FAILED [ 50%]
tests/domain/test_workspace_binding.py::test_a_sessionless_call_still_uses_its_own_cwd PASSED [ 75%]
tests/domain/test_workspace_binding.py::test_a_new_session_picks_up_its_own_first_cwd PASSED [100%]

_____________ test_first_request_of_a_session_fixes_the_workspace ______________
>       assert decision.verdict.decision is DecisionKind.deny
E       assert <DecisionKind.allow: 'allow'> is <DecisionKind.deny: 'deny'>
E        +  where <DecisionKind.allow: 'allow'> = Verdict(decision=<DecisionKind.allow: 'allow'>,
E             stage=2, rule_id=None, ..., model='m', ...).decision

_____________ test_a_later_cwd_change_does_not_widen_allowed_paths _____________
>       assert decision.verdict.decision is DecisionKind.deny
E       assert <DecisionKind.allow: 'allow'> is <DecisionKind.deny: 'deny'>
E        +  where <DecisionKind.allow: 'allow'> = Verdict(decision=<DecisionKind.allow: 'allow'>,
E             stage=2, rule_id=None, ..., model='m', ...).decision

========================= 2 failed, 2 passed in 0.41s ==========================
```

**Почему это падение и есть баг.** В обоих отказавших случаях второй запрос сессии приходит с
`args.cwd = "/"`. Старый `_resolve` делал `with_workspace(base, request.args.cwd)` на каждый запрос,
поэтому `${WORKSPACE}` подставлялся как `/`, а `allowed_paths` превращались в `["/", "/tmp/agentgate-scratch"]`.
`is_within(<любой абсолютный путь>, ["/"])` истинно всегда, поэтому:

- `rm -rf /home/u/other-project` перестал попадать в `DestructiveRule` (цель «внутри» песочницы,
  и она не равна workspace);
- `cp payload /etc/cron.d/job` перестал попадать в `ProfilePathRule`.

Оба провалились сквозь всю ступень 1 (`stage=2`, `model='m'` в вердикте — то есть решение принял
классификатор, а не правила) и получили `allow` от `FakeLLM`. Это ровно строки таблицы F4.
Важная деталь из вывода: `state=SessionState(..., workspace='/home/u/repo', ...)` — сессия уже
хранила правильный workspace первого запроса, движок его просто не использовал.

Два оставшихся теста — контрольные, они зелёные и до, и после: вызов без `session_id` берёт свой
`cwd`, а новая сессия — свой первый `cwd`. Их назначение — не дать «починить» баг тем, что сломает
эти два свойства.

## 2. GREEN — после реализации

```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
845 passed in 8.38s        # exit code 0
```

Было 836, стало 845: +4 (`test_workspace_binding`), +7 (`test_policy`), +2 (`test_profiles`
разделён, добавлены два узких теста на хэш), −4 (три `test_resolved_*` и
`test_hash_ignores_workspace_and_is_stable` уехали в `tests/domain/test_policy.py`).

Отдельные обязательные прогоны:

- `tests/domain/test_workspace_binding.py`, `tests/domain/test_policy.py`, `tests/equivalence/` —
  237 passed. Корпус эквивалентности **не сдвинулся**: `baseline.json` не трогался, в корпусе один
  фиксированный workspace, а `Policy.bind(profile, WORKSPACE)` даёт ровно те же разрешённые и
  защищённые пути, что давал `with_workspace(profile, WORKSPACE)` (`detect_workspace("/home/u/repo")`
  возвращает сам путь — каталога `.git` там нет).
- `tests/rules/test_latency.py` — passed. Замер p50 тем же кодом: **0.160 мс** (было 0.161).
  Регрессии нет: `Policy.bind` вызывается один раз в `_resolve`, вне измеряемого участка, и заменяет
  4 вызова `resolved_allowed_paths()` + 2 вызова `resolved_protected_paths()` на один расчёт.
- `tests/engine/test_gate.py -k profile_hash` — 2 passed
  (`test_unknown_model_still_records_the_profile_hash`, `test_unknown_profile_records_no_profile_hash`).
  Инвариант из ревью задачи 2 сохранён дословно: ранний отказ читает профиль из `self._profiles`
  и берёт его настоящий хэш; пустая строка остаётся только там, где профиля нет.
- Прогон с `-W error::DeprecationWarning` — 845 passed. Вывод чистый, предупреждений нет.

## 3. Что изменилось в контракте

```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff ../contracts
```

```
 contracts/openapi.yaml | 6 ------
 1 file changed, 6 deletions(-)

@@ -916,12 +916,6 @@ components:
           title: Rules
           type: array
-        workspace:
-          anyOf:
-          - type: string
-          - type: 'null'
-          default: null
-          title: Workspace
       required:
       - id
```

Единственное изменение — из `components.schemas.Profile` пропало поле `workspace`.
`contracts/decide_request.schema.json` и `contracts/decide_response.schema.json` не изменились
(`git diff --stat` перечисляет только `openapi.yaml`) — `DecideRequest`/`DecideResponse` не тронуты,
как и требует ограничение «форма запроса и ответа не меняется до задачи 8».

Обоснование для PR: `GET /v1/profiles/{id}` всегда возвращал `workspace: null`, потому что отдавал
базовый профиль, а не привязанный к сессии. Поле исчезает, а не меняет смысл; ни один клиент не мог
получить из него информацию. Правило `contracts/README.md` (изменение контракта — PR с упоминанием
трёх направлений) относится к мерджу и остаётся за владельцем.

## 4. Что построено

`agentgate/domain/policy.py` — новый frozen dataclass `Policy`: профиль плюс один workspace,
`allowed_paths`/`protected_paths` как кортежи, посчитанные один раз в `Policy.bind`, `profile_hash`,
и свойства-делегаты `id`, `network`, `protected_branches`, `safe_prefixes`, `escalation`, `prose`.
`Profile` остаётся тем, чем и был, — конфигурацией оператора, ничего не знающей о запросе.

`Gate._resolve` теперь берёт workspace из `SessionState.workspace` — то есть из `cwd` первого
запроса сессии, — и только при отсутствии `session_id` считает его из `cwd` текущего запроса.
`SessionStateStore.get_or_create` уже возвращал существующее состояние, не трогая `workspace`, так
что фиксация происходит сама собой; отдельный тест это закрепляет.

Все правила ступени 1 и `build_system_prompt` принимают `Policy` вместо `Profile`. Табличные
ожидания `tests/rules/hard_deny/test_rules.py`, `tests/rules/test_chain.py`,
`tests/normalize/test_shell.py` не изменены ни в одной строке.

### Файлы

Созданы: `service/agentgate/domain/policy.py`, `service/tests/domain/test_policy.py`,
`service/tests/domain/test_workspace_binding.py`, `service/tests/profiles/{__init__,test_loader,test_schema}.py`.

Удалён: `service/tests/test_profiles.py` (разделён на `tests/profiles/test_loader.py` и
`tests/profiles/test_schema.py` — зеркальная структура, гайд 6.3).

Изменены: `service/agentgate/profiles/{schema,loader}.py`, `service/agentgate/engine/gate.py`,
`service/agentgate/rules/` (все 12 модулей), `service/agentgate/stage2/{prompt,run}.py`,
`service/agentgate/api/app.py`, `service/agentgate/normalize/paths.py`,
`service/tests/{factories,test_stage2_prompt,test_stage2_run}.py`, `service/tests/rules/*`,
`service/tests/equivalence/test_equivalence.py`, `contracts/openapi.yaml`,
`docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`, `CLAUDE.md`.

## 5. Три отступления от брифа, с обоснованием

**1. `ConfigDict(ignored_types=(cached_property,))` не добавлен.** Бриф приводит эту строку как
необходимую для `cached_property` на pydantic-модели. Проверено прямо: pydantic v2 поддерживает
`functools.cached_property` без всякой настройки, в том числе для имени с ведущим подчёркиванием
(`_h in m.__dict__` после первого обращения → `True`). Строка была бы шумом (гайд 1.2), поэтому её
нет. Кэширование работает: `test_profile_hash_is_stable_across_calls`.

**2. В промпт по-прежнему идут объявленные, а не резолвленные защищённые пути.** Бриф предписывает
`profile.resolved_protected_paths()` → `policy.protected_paths` в `prompt.py`. Но `prompt.py` никогда
не вызывал `resolved_protected_paths()` — он печатал сырой `profile.protected_paths`. Буквальное
исполнение этого пункта изменило бы содержимое промпта: `~/.ssh/**` превратился бы в
`/Users/<имя>/.ssh/**`, то есть в промпт попал бы домашний каталог хоста. Это изменение поведения,
не санкционированное задачей (глобальное ограничение: меняется только привязка workspace), и оно
ничего не даёт модели. В коде стоит `policy.profile.protected_paths` с комментарием, объясняющим
выбор. `test_system_prompt_contains_profile_and_prose` продолжает видеть `protected=.env*,.git/hooks/**`.

**3. `MINIMAL` вынесен в `tests/factories.py`, а не продублирован.** Бриф делит `test_profiles.py`
на два модуля, оба из которых использовали общий словарь `MINIMAL`. Импорт одного тестового модуля
из другого запрещён собственным docstring `factories.py`, а дублирование словаря — дублирование
знания (гайд 1.3). Добавлена фабрика `minimal_profile_data(**overrides)`.

## 6. Найдено по ходу

**Кэшированный хэш переживает `model_copy` — задокументированная ловушка.** После перехода на
`cached_property` `p.model_copy(update={...}).profile_hash()` возвращает хэш ОРИГИНАЛА:

```python
p = Profile.model_validate(d);  h = p.profile_hash()
p.model_copy(update={'protected_paths': ['*.pem']}).profile_hash() == h   # True
Profile.model_validate(dict(d, protected_paths=['*.pem'])).profile_hash() == h  # False
```

В продакшене недостижимо: профили загружаются из YAML один раз и не копируются — единственный
`model_copy` в `agentgate/` был `with_workspace`, он удалён. Инвариант записан в docstring
`Profile.profile_hash`. Заодно `tests/test_stage2_prompt.py` переписан так, чтобы второй профиль
строился из словаря, а не через `model_copy`, — тест больше не опирается на эту ловушку.

**`detect_workspace` по-прежнему вызывается на каждый запрос.** Бриф в шаге 5 пишет, что обход
файловой системы «теперь вызывается только при создании сессии», но его же образец кода передаёт
`detect_workspace(request.args.cwd)` аргументом в `get_or_create`, то есть считает его всегда.
Чтобы считать лениво, `SessionStateStore.get_or_create` должен был бы принимать фабрику вместо
значения — изменение протокола и обеих его реализаций ради работы, которая и раньше выполнялась
на каждый запрос. Регрессии нет, экономии тоже; оставлено как в брифе (гайд 1.2). Кандидат в
задачу про производительность, если она появится.

## 7. Самопроверка

- Четыре регрессионных теста падали до правки, ровно по причине из таблицы F4 (провал в ступень 2,
  `allow` от классификатора), и зелёные после.
- Workspace сессии берётся из её первого запроса; вызов без `session_id` — из своего `cwd`.
- Оба теста на `profile_hash` зелёные.
- `Policy` frozen (`test_policy_is_frozen`), пути резолвятся один раз в `bind` и лежат кортежами
  (`test_resolved_paths_are_immutable`), хэш не зависит от workspace (`test_hash_does_not_depend_on_the_workspace`).
- `Profile` потерял `workspace`, `_expand`, `resolved_allowed_paths`, `resolved_protected_paths`,
  `public_dict` полностью: `grep -rn` по `agentgate/`, `scripts/`, `tests/` не находит ни одного
  упоминания; остались только исторические ссылки в `docs/`.
- Комментарии — только инварианты, без ссылок на задачи, PR, авторов и дат.
- Вывод тестов чистый, в том числе с `-W error::DeprecationWarning`.

## 8. Что осталось за рамками

- `tests/rules/test_chain.py` содержит доставшиеся от предыдущих задач комментарии вида
  «см. fix round 2, task 6» (строки 18, 122–130, 163), запрещённые глобальным ограничением по стилю.
  Задача 5 правит в этом файле только импорты и имена фабрик; вычищать чужие комментарии здесь —
  шум в diff, который прячет содержательную часть. Кандидат в отдельную уборку.
- `tests/test_stage2_run.py` импортирует `P` из `tests/test_stage2_prompt.py` — нарушение того же
  правила «тесты импортируют только из factories». Существовало до задачи 5; изменён только доступ
  к моделям (`P.profile.models`), переезд фикстуры не делался.
