# Задача 3: Профили политики

## Что построено

- `service/agentgate/profiles/__init__.py` — пустой пакет.
- `service/agentgate/profiles/schema.py` — pydantic v2 модели `NetworkMode`,
  `Network`, `ModelConfig`, `ModelsConfig` (валидатор `default in configs`,
  метод `model_config_for(name) -> (key, ModelConfig)`, `KeyError` на
  неизвестное имя), `DenyWindow`, `Escalation`, `Prose`, `Profile`
  (`id`, `allowed_paths`, `protected_paths`, `protected_branches`,
  `network`, `safe_prefixes`, `models`, `escalation`, `prose`, `rules`,
  `workspace`) с методами `profile_hash()` (sha256 от
  `model_dump_json(exclude={"workspace"})`), `resolved_allowed_paths()`
  и `resolved_protected_paths()` (подстановка `${WORKSPACE}` и `~`),
  `public_dict()`. Ровно по спецификации брифа, без добавления или
  переименования полей.
- `service/agentgate/profiles/loader.py` — `load_profiles(directory)`
  (все `*.yaml` из каталога, ключ словаря — `id`; дубликат `id` и
  невалидный YAML/схема → `ValueError` с именем файла), `detect_workspace(cwd)`
  (ближайший родитель с `.git`, иначе сам `cwd`), `with_workspace(profile, cwd)`
  (копия профиля с заполненным `workspace`).
- `service/profiles/default-dev.yaml` — поставляемый профиль по умолчанию
  (`id: default`), используется тестом `test_shipped_default_profile_loads`
  и в проде — через `Settings.profiles_dir`.
- `service/tests/test_profiles.py` — 9 тестов из брифа, дословно.

## Тестирование (первый раунд)

TDD: сначала написан падающий тест, затем реализация.

### RED

Команда:
```
cd service && uv run pytest tests/test_profiles.py -v
```
Результат (до создания `agentgate/profiles`):
```
ModuleNotFoundError: No module named 'agentgate.profiles'
```
при импорте — ожидаемо, пакет ещё не существовал.

### GREEN

Команда:
```
cd service && uv run pytest tests/test_profiles.py -v
```
Результат: `9 passed`, ровно как указано в брифе.

Полный набор тестов сервиса (включая наследие задачи 1):
```
cd service && uv run pytest -v
```
Результат: `27 passed` (18 из задачи 1 + 9 новых), без предупреждений.

## Отклонение от базового состояния worktree

Worktree на старте был на ветке `worktree-agent-a65c033ff55cb415e` в коммите
`a9a0edd`, без коммитов задачи 1 (`2215d6a`, `593a461`, `90b8339`), хотя должен
был базироваться на `feat/agentgate-task-1` @ `90b8339`. Коммит `a9a0edd` —
прямой предок `90b8339`, расхождений не было (рабочее дерево было чистым),
поэтому ветка синхронизирована через `git reset --hard 90b8339` до начала
работы. Координатор независимо подтвердил корректность этого шага
(`git merge-base --is-ancestor 90b8339 worktree-agent-a65c033ff55cb415e`,
линейная история, коммит добавляет только новые файлы).

## Раунд правок 1 — три важных находки ревью

Ревью подтвердило, что логика `schema.py` и `loader.py` верна и соответствует
брифу; правки затронули только тестовое покрытие и отчётность, поведение
кода не менялось.

### Находка 1 — отсутствовал отчёт в `reports/`

`service/CLAUDE.md` требует отчёт на русском в `reports/task-<N>-<slug>.md`
после каждой завершённой задачи; `reports/` — единственный путь в корне
репозитория, доступный этой задаче для записи. Коммит `5251be4` такого файла
не создавал. Файл, скопированный координатором в
`docs/superpowers/service/sdd/task-3-report.md`, этому требованию не
удовлетворяет (и `docs/` вне зоны ответственности задачи).

Закрыто: создан этот файл, `reports/task-3-profiles.md`.

### Находка 2 — тест дубликата `id` не проверял имя файла в сообщении

`test_load_profiles_duplicate_id` использовал только
`with pytest.raises(ValueError):`, не проверяя, что сообщение называет
файл-виновник — хотя fail-closed требование обязывает называть файл в
сообщении об ошибке, и `loader.py` это делает
(`f"duplicate profile id '{profile.id}' in {path.name}"`), просто это не
было закреплено тестом: рефактор мог бы незаметно потерять имя файла, и
набор тестов остался бы зелёным.

Путь невалидного YAML/схемы (`test_load_profiles_invalid_raises_with_filename`)
уже содержал `match="bad.yaml"` из Step 1 брифа — там пробел отсутствовал.

Закрыто: добавлен `match="b.yaml"` в `test_load_profiles_duplicate_id`
(в тесте пишутся `a.yaml` и `b.yaml` с одинаковым `id`; `load_profiles`
обходит файлы отсортированными по имени, поэтому дубликат обнаруживается
при обработке `b.yaml`, и это имя попадает в сообщение).

### Находка 3 — `resolved_protected_paths()` не имел тестового покрытия

Ни один из девяти тестов не вызывал этот метод, хотя:
- задачи 5, 6 и 7 используют его напрямую;
- поставляемый `service/profiles/default-dev.yaml` использует
  `~/.ssh/**`, `~/.aws/**`, `~/.kube/**` как protected paths — реальный
  боевой контент, чьё раскрытие `~` не было проверено вообще;
- метод лежит на пути hard-deny, который по глобальным ограничениям
  не может быть переопределён — ошибка раскрытия здесь тихо занижает
  или завышает защиту, и ничего бы не заметило.

Закрыто: добавлен `test_resolved_protected_paths_workspace_and_tilde` —
строит профиль с `protected_paths=["${WORKSPACE}/.env*", "~/.ssh/**"]`,
проставляет `workspace` напрямую через `model_copy`, и проверяет, что
`resolved_protected_paths()` возвращает подстановку `${WORKSPACE}` и
раскрытие `~/.ssh/**` через `os.path.expanduser` — то есть против
реального `$HOME` процесса, а не заглушки.

### Дополнительно (решение координатора) — `resolved_allowed_paths()` тестировался только на тривиальном `tmp_path`

`test_resolved_allowed_paths` проверял только случай, когда `allowed_paths`
целиком состоит из `${WORKSPACE}` — оба механизма подстановки
(`${WORKSPACE}` и `~`) не были закреплены вместе, и не были закреплены
на обоих методах.

Закрыто: добавлен `test_resolved_allowed_paths_workspace_and_tilde` — профиль
с `allowed_paths=["${WORKSPACE}/src", "~/.cache/agentgate"]`, проверка через
`os.path.normpath` (как это делает сам метод) обеих подстановок. Вместе с
находкой 3 оба механизма раскрытия путей (`${WORKSPACE}` и `~`) теперь
закреплены тестами на обоих методах — `resolved_allowed_paths()` и
`resolved_protected_paths()`.

### Явно вне рамок этого раунда (по решению координатора)

Не реализовывалось и не упоминается как недостаток: оборачивание `OSError`
в `load_profiles()`; внутренняя нестыковка брифа о том, к какому классу
принадлежит `model_config_for` (`Profile` или `ModelsConfig`) — код и тесты
брифа помещают его в `ModelsConfig`, реализация этому следует, дефект
брифа обрабатывается координатором отдельно.

## Тестирование (раунд правок 1)

TDD этого раунда: три новые/изменённые проверки закрывают уже верное
поведение (регрессионные тесты), поэтому честно фиксирую, что RED здесь не
инсценировался — по указанию координатора это не требовалось, раз ревью уже
подтвердило корректность `schema.py`/`loader.py`.

Команда:
```
cd service && uv run pytest tests/test_profiles.py -v
```
Результат — все 11 тестов, включая три новых/изменённых
(`test_resolved_allowed_paths_workspace_and_tilde`,
`test_resolved_protected_paths_workspace_and_tilde`,
`test_load_profiles_duplicate_id` с `match="b.yaml"`), прошли с первого
запуска:
```
tests/test_profiles.py::test_minimal_profile_defaults PASSED             [  9%]
tests/test_profiles.py::test_default_model_must_exist PASSED             [ 18%]
tests/test_profiles.py::test_hash_ignores_workspace_and_is_stable PASSED [ 27%]
tests/test_profiles.py::test_resolved_allowed_paths PASSED               [ 36%]
tests/test_profiles.py::test_resolved_allowed_paths_workspace_and_tilde PASSED [ 45%]
tests/test_profiles.py::test_resolved_protected_paths_workspace_and_tilde PASSED [ 54%]
tests/test_profiles.py::test_detect_workspace PASSED                     [ 63%]
tests/test_profiles.py::test_load_profiles_dir PASSED                    [ 72%]
tests/test_profiles.py::test_load_profiles_duplicate_id PASSED           [ 81%]
tests/test_profiles.py::test_load_profiles_invalid_raises_with_filename PASSED [ 90%]
tests/test_profiles.py::test_shipped_default_profile_loads PASSED        [100%]

============================== 11 passed in 0.07s ==============================
```

Это ожидаемо и честно отражено: находки 2 и 3 — регрессионные проверки
над поведением, которое ревью уже признало верным; ни один тест не был
искусственно сломан ради инсценировки RED.

Полный набор тестов:
```
cd service && uv run pytest -q
```
```
.............................                                            [100%]
29 passed in 0.09s
```

Дополнительно прогнано с `-W error`:
```
cd service && uv run pytest -q -W error
```
```
.............................                                            [100%]
29 passed in 0.09s
```
Скрытых pydantic/PyYAML deprecation warnings нет.

## Принятые решения

- Имя файла для `match=` в тесте дубликата `id` — `"b.yaml"`, а не
  `"a.yaml"`: `load_profiles` обходит файлы через
  `sorted(Path(directory).glob("*.yaml"))`, поэтому `a.yaml` обрабатывается
  первым и попадает в словарь, а дубликат обнаруживается при обработке
  `b.yaml` — это имя и оказывается в сообщении.
- Для проверки раскрытия `~` использован `model_copy(update={"workspace": ws})`
  напрямую вместо `with_workspace(..., cwd)`, чтобы не зависеть от того,
  окажется ли `tmp_path` под каталогом с `.git` где-то в родителях (что
  сломало бы `detect_workspace`); это не меняет проверяемое поведение
  `resolved_allowed_paths()`/`resolved_protected_paths()`, только исключает
  постороннюю зависимость от структуры файловой системы в момент прогона.
- Раскрытие `~` в новых тестах сверяется через `os.path.expanduser(...)`
  напрямую (реальный `$HOME` процесса), а не через захардкоженный путь —
  тест остаётся верным на любой машине.

## Отложено

Ничего не отложено сверх явно выведенного за рамки координатором
(см. выше) — три важные находки и присоединённое минорное замечание
закрыты полностью в этом раунде.
