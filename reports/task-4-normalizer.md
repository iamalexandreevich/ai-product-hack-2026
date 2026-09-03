# Task 4 — Нормализатор (AST shell, пути, домены)

**Статус:** закрыт. **Ветка:** `feat/agentgate-task-1`. **Базовый коммит:** `08d518a` (merge task 3).

## Что построено

| Файл | Содержимое |
|---|---|
| `service/agentgate/normalize/model.py` | Датаклассы `Redirect`, `SimpleCommand`, `Flags`, `NormalizedAction` с методами `executables()`, `to_dict()`, `action_hash()` (sha256 от JSON-представления без `raw`) |
| `service/agentgate/normalize/paths.py` | `resolve_path` (раскрытие `~`, join с `cwd`, `normpath`), `looks_like_path` (эвристика по токену argv), `is_within` (через `commonpath`), `matches_any` (basename для паттернов без `/`, абсолютный + относительно workspace для паттернов с `/`, `**` как префикс каталогов) |
| `service/agentgate/normalize/domains.py` | `extract_domains`: URL (`scheme://host[:port]/…`), `git@host:path`, `user@host` для ssh/scp/rsync/sftp — без дублей, в нижнем регистре |
| `service/agentgate/normalize/shell.py` | `normalize_shell(raw, cwd)`: обход AST bashlex, извлечение `SimpleCommand` с редиректами, флаги `has_subst`/`has_env_assign`/`has_eval`/`unparseable`; `PATH_COMMANDS` — команды, все не-флаговые аргументы которых считаются путями |
| `service/agentgate/normalize/__init__.py` | `normalize(req: DecideRequest) -> NormalizedAction`: диспетчер по `Tool` (`shell` → `normalize_shell`, `file_read`/`file_write` → пути, `network` → домены в нижнем регистре, `mcp_call` → `mcp`) |

Реализация выполнена по коду из брифа `docs/superpowers/service/sdd/task-4-brief.md` дословно (датаклассы, сигнатуры, правила нормализации — как предписано), с добавлением докстрингов на английском и одним осознанным отступлением (см. «Решения» ниже).

## TDD

- **RED:** `uv run pytest tests/test_normalize_paths.py tests/test_normalize_domains.py tests/test_normalize_shell.py -v` → 3 ошибки сбора, `ModuleNotFoundError: No module named 'agentgate.normalize'` — до создания пакета `normalize`.
- **GREEN:** тот же запуск после реализации → `17 passed` (все тест-кейсы из брифа, включая adversarial-случаи: подстановки `$(…)`/бэктики, `eval`, редиректы с `/dev/null`, неразбираемый ввод, подшелл + цикл `for`).
- Полный набор: `uv run pytest -W error -q` → `64 passed` (47 унаследованных + 17 новых), варнингов нет.

Обход AST заработал без доработок с первой реализации — сработала ветка `for`/`while`/`until`/`if`/`function` из брифа, запасной путь «посмотреть `node.kind`» из Step 8 не понадобился.

## Ручная проверка (вне тестов брифа)

Бриф не даёт отдельного тестового файла для `normalize()` (диспетчера `__init__.py`) — только описание поведения в разделе Interfaces. Список тестовых файлов в брифе фиксирован («Test: …») — я не стал добавлять новый тестовый файл сверх запрошенного (дисциплина по scope), но прогнал ручную проверку через `uv run python -c "..."` на все четыре ветки (`file_read`, `network`, `mcp_call`, `shell`-делегирование) плюс `to_dict()`/`action_hash()` — поведение соответствует спеке. Также вручную проверены дополнительные adversarial-конструкции: бэктики, `2>&1` (дублирование дескриптора без файла — не попадает в `redirects`), несколько редиректов на одну команду, `fd>` с номером дескриптора, регистронезависимость URL-доменов, пустая/пробельная строка (fail-closed → `unparseable=True`).

## Решения, принятые за пользователя

- **Упрощён `except`-блок в `normalize_shell`.** В брифе — `except (bashlex.errors.ParsingError, Exception)`, что избыточно (кортеж с `Exception` уже перекрывает `ParsingError`). Заменено на `except Exception:` с комментарием — то же fail-closed поведение (любая ошибка парсера → `unparseable=True`, `commands=[]`), без мёртвого кода.
- **Не добавлен отдельный тестовый файл для `agentgate.normalize.normalize()`.** Бриф явно перечисляет три тестовых файла (Files → Test) и не даёт для диспетчера тест-кейсов «слово в слово» — решил не изобретать тесты сверх брифа, чтобы не размывать scope задачи, и вместо этого сделал ручную verification-проверку (см. выше). Задачи 5–7 будут упражнять `normalize()` через `DecideRequest` end-to-end.

## Соответствие глобальным ограничениям

- **Fail-closed:** ошибка парсера bashlex → `flags.unparseable=True`, пустые `commands`/`paths`/`domains` — не «ничего подозрительного», а явный сигнал для ступеней 1/2 на эскалацию.
- **Латентность:** ни один модуль `normalize/` не делает I/O (файловая система, сеть) — только `os.path`, `re`, `fnmatch`, разбор строки bashlex.
- **Решение не по сырой строке:** `normalize()` — единственная точка, где `raw` вообще анализируется; результат — структурированный `NormalizedAction`, `raw` присутствует в нём только для аудита и явно исключён из `action_hash()`.

## Отложено (в финальное ревью)

Ничего не отложено намеренно — реализация покрывает все правила и adversarial-кейсы брифа. Единственная точка внимания: `looks_like_path`/`_collect_paths` могут посчитать путём токен вида `git@host:org/repo.git` (из-за `/` внутри) — это соответствует алгоритму брифа буквально и является «безопасным» избыточным срабатыванием (больше сигнала для ступеней 1/2), а не дефектом.
