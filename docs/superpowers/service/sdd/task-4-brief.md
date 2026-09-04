### Task 4: `shell/` — публичные wrapper'ы, `ParsedArgv`, один список секретов, неизменяемое действие

Закрывает: F8 (четыре ручных цикла по argv), F9 в части секретов (два несовпадающих списка), F11 (импорт приватных имён через границу пакета), G2 (аббревиатуры в затронутых модулях), гайд 3.5 (мутация после создания).

Первая задача, которая трогает ядро безопасности. Страховка — корпус эквивалентности, который строится здесь и переиспользуется задачей 7.

**Files:**
- Create: `service/agentgate/shell/__init__.py`, `service/agentgate/shell/wrappers.py`, `service/agentgate/shell/argv.py`, `service/agentgate/shell/secrets.py`, `service/tests/shell/__init__.py`, `service/tests/shell/test_wrappers.py`, `service/tests/shell/test_argv.py`, `service/tests/shell/test_secrets.py`, `service/tests/equivalence/__init__.py`, `service/tests/equivalence/corpus.py`, `service/tests/equivalence/test_equivalence.py`
- Modify: `service/agentgate/normalize/shell.py` (wrapper-часть уезжает, `_Walker` собирает результат один раз), `service/agentgate/normalize/model.py` (`frozen=True`), `service/agentgate/normalize/paths.py` (`_SENSITIVE_BASENAMES` уходит), `service/agentgate/rules/hard_deny/shared.py` и `exfil.py` (переход на `ParsedArgv`)
- Move: `service/tests/test_normalize_shell.py` → `service/tests/normalize/test_shell.py`; `test_normalize_paths.py`, `test_normalize_domains.py`, `test_normalize_init.py` — туда же

**Interfaces:**
- Produces: `agentgate.shell.wrappers` — публичные `WRAPPER_COMMANDS: frozenset[str]`, `WRAPPER_VALUE_FLAGS: dict[str, frozenset[str]]`, `ENV_ASSIGNMENT: re.Pattern`, `resolve_effective_argv(argv, wrapper_commands=WRAPPER_COMMANDS) -> list[str]`, `chain_unresolved(argv, wrapper_commands) -> str | None`.
- Produces: `agentgate.shell.argv.ParsedArgv` — frozen dataclass: `executable: str`, `positionals: tuple[str, ...]`, `options: tuple[Option, ...]`; методы `option(name) -> Option | None`, `has(name) -> bool`, `values_of(*names) -> tuple[str, ...]`; классметод `ParsedArgv.of(argv: Sequence[str], value_flags: frozenset[str] = frozenset()) -> ParsedArgv`. `Option` — frozen dataclass `name: str`, `value: str | None`, `inline: bool`.
- Produces: `agentgate.shell.secrets.SECRET_PATTERNS: tuple[str, ...]` и `is_secret_path(path, workspace) -> bool` — единственный список секретных файлов на сервисе.
- Consumes: `Verdict`, `RuleChain` (задачи 1, 3).

- [ ] **Step 1: Корпус эквивалентности — снять эталон до правок**

`service/tests/equivalence/corpus.py`:

```python
"""Every shell command the table tests already assert on, plus whatever a
real log contributes. Used to prove a refactor of the normalizer and the
rules changed nothing observable.
"""

import json
from pathlib import Path

_TABLE_INPUTS = Path(__file__).with_name("commands.txt")


def commands() -> list[str]:
    return [line for line in _TABLE_INPUTS.read_text(encoding="utf-8").splitlines() if line]


def from_decision_log(path: Path) -> list[str]:
    """Raw commands from a JSONL decision log, if one is present locally.

    The log is developer-local and never committed; an absent file
    contributes nothing rather than failing the run.
    """
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line).get("raw")
        if raw:
            out.append(raw)
    return out
```

Собрать `service/tests/equivalence/commands.txt` из уже существующих табличных входов:

```bash
cd service && uv run python - <<'PY'
import ast, pathlib
out = set()
for path in ["tests/rules/hard_deny/test_rules.py", "tests/rules/test_chain.py", "tests/normalize/test_shell.py"]:
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if " " in text and "\n" not in text and len(text) < 300:
                out.add(text)
pathlib.Path("tests/equivalence/commands.txt").write_text("\n".join(sorted(out)) + "\n", encoding="utf-8")
print(len(out), "commands")
PY
```
Expected: несколько сотен строк. Файл коммитится — это фиксированный корпус, а не сгенерированный артефакт.

Снять эталон текущим кодом:

```bash
cd service && uv run python - <<'PY'
import json, pathlib
from agentgate.api.schemas import DecideRequest
from agentgate.normalize import normalize
from agentgate.rules.chain import STAGE1
from tests.equivalence.corpus import commands
from tests.factories import hard_deny_profile, WORKSPACE

profile = hard_deny_profile()
baseline = {}
for raw in commands():
    request = DecideRequest(harness="t", tool="shell", raw=raw, args={"cwd": WORKSPACE}, user_request="x")
    action = normalize(request)
    verdict = STAGE1.evaluate(action, profile)
    baseline[raw] = {
        "action": action.to_dict(),
        "hash": action.action_hash(),
        "verdict": None if verdict is None else
                   {"decision": verdict.decision.value, "rule_id": verdict.rule_id,
                    "reason": verdict.reason, "suggest": verdict.suggest, "hard": verdict.hard},
    }
pathlib.Path("tests/equivalence/baseline.json").write_text(
    json.dumps(baseline, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
print(len(baseline), "baselined")
PY
```

Строки, которые бросают исключение при нормализации, — записать в baseline как `{"error": type(exc).__name__}` и не терять: молчаливый пропуск сделал бы корпус слабее.

- [ ] **Step 2: Тест эквивалентности**

`service/tests/equivalence/test_equivalence.py`:

```python
"""The refactor changed nothing observable.

Deleted once tasks 4 and 7 are merged -- it exists to make those two
steps safe, not to be maintained.
"""

import json
from pathlib import Path

import pytest

from agentgate.api.schemas import DecideRequest
from agentgate.normalize import normalize
from agentgate.rules.chain import STAGE1
from tests.factories import WORKSPACE, hard_deny_profile

BASELINE = json.loads(Path(__file__).with_name("baseline.json").read_text(encoding="utf-8"))


def _current(raw: str) -> dict:
    action = normalize(DecideRequest(
        harness="t", tool="shell", raw=raw, args={"cwd": WORKSPACE}, user_request="x",
    ))
    verdict = STAGE1.evaluate(action, hard_deny_profile())
    return {
        "action": action.to_dict(),
        "hash": action.action_hash(),
        "verdict": None if verdict is None else
                   {"decision": verdict.decision.value, "rule_id": verdict.rule_id,
                    "reason": verdict.reason, "suggest": verdict.suggest, "hard": verdict.hard},
    }


@pytest.mark.parametrize("raw", sorted(BASELINE), ids=range(len(BASELINE)))
def test_normalization_and_verdict_match_the_baseline(raw):
    assert _current(raw) == BASELINE[raw]
```

`ids=range(...)` — потому что сами команды содержат символы, ломающие `-k` (гайд 6.4).

Run: `cd service && uv run pytest tests/equivalence -q`
Expected: все зелёные (эталон снят с этого же кода — это проверка самой оснастки).

- [ ] **Step 3: `shell/wrappers.py` — публичный модуль**

`service/agentgate/shell/__init__.py`: пустой файл.

`service/agentgate/shell/wrappers.py` — перенос из `normalize/shell.py` без изменения логики: `_WRAPPER_CMDS` → `WRAPPER_COMMANDS`, `_WRAPPER_VALUE_FLAGS` → `WRAPPER_VALUE_FLAGS`, `_ENV_ASSIGNMENT` → `ENV_ASSIGNMENT`, `_TIMEOUT_DURATION` → приватная константа модуля, `resolve_effective_argv` — как есть. Плюс сюда же переезжает `_wrapper_chain_unresolved` из hard-deny под именем `chain_unresolved(argv, wrapper_commands)` — «почему argv не свёлся к настоящей команде» это знание о wrapper'ах, а не о правиле.

Docstring модуля:

```python
"""What a wrapper command does to the argv behind it.

`env`, `sudo`, `timeout`, `nice`, `xargs` and friends all execute
something else; both the normalizer (does this argv reach a shell?) and
stage 1 (what command is actually being run?) need the same answer, so
the answer lives here rather than privately inside either of them.
"""
```

`service/agentgate/normalize/shell.py`: удалить перенесённые имена, импортировать `from agentgate.shell.wrappers import WRAPPER_COMMANDS, resolve_effective_argv`.

`service/agentgate/rules/hard_deny/shared.py`: удалить `from agentgate.normalize.shell import _ENV_ASSIGNMENT, _WRAPPER_CMDS, _WRAPPER_VALUE_FLAGS, resolve_effective_argv` (F11), импортировать из `agentgate.shell.wrappers`. `EFFECTIVE_WRAPPERS` остаётся здесь: это выбор ступени 1 (sudo непрозрачен, xargs прозрачен), а не свойство wrapper'ов.

`service/tests/shell/test_wrappers.py` — перенести из `tests/normalize/test_shell.py` те тесты, что проверяют именно `resolve_effective_argv` и разбор wrapper-цепочек, плюс новый:

```python
def test_chain_unresolved_reports_depth_for_a_chain_deeper_than_the_bound():
    argv = ["env"] * 9 + ["rm", "-rf", "/"]
    assert chain_unresolved(argv, WRAPPER_COMMANDS) == "depth"


def test_chain_unresolved_says_nothing_about_a_plain_command():
    assert chain_unresolved(["rm", "-rf", "/"], WRAPPER_COMMANDS) is None
```

- [ ] **Step 4: `shell/secrets.py` — один список секретов**

`service/tests/shell/test_secrets.py`:

```python
from agentgate.normalize.paths import matches_any
from agentgate.shell.secrets import SECRET_PATTERNS, is_secret_path

WORKSPACE = "/home/u/repo"


def test_dotenv_is_secret():
    assert is_secret_path("/home/u/repo/.env", WORKSPACE)


def test_private_key_is_secret():
    assert is_secret_path("/home/u/repo/deploy.pem", WORKSPACE)


def test_ssh_directory_is_secret():
    assert is_secret_path("/home/u/.ssh/id_rsa", WORKSPACE)


def test_ordinary_source_file_is_not_secret():
    assert not is_secret_path("/home/u/repo/main.py", WORKSPACE)


def test_patterns_are_immutable():
    assert isinstance(SECRET_PATTERNS, tuple)
```

`service/agentgate/shell/secrets.py`:

```python
"""What counts as a secret file. One list, service-wide.

Two lists were two chances to add a pattern to one and forget the other,
and a missed pattern here is a hole in exfil detection.
"""

from agentgate.normalize.paths import matches_any

SECRET_PATTERNS: tuple[str, ...] = (
    ".env*", "*.pem", "id_rsa*", "id_ed25519*", "*.key", "*.p12",
    "credentials", ".netrc", ".git-credentials",
    "~/.ssh/**", "~/.aws/**", "~/.kube/**",
)


def is_secret_path(path: str, workspace: str | None) -> bool:
    return matches_any(path, list(SECRET_PATTERNS), workspace)
```

Слить в него `_SENSITIVE_BASENAMES` из `normalize/paths.py`: выписать текущее содержимое обеих коллекций, объединить без потерь, и **прогнать корпус эквивалентности** — объединение расширяет множество секретов, поэтому какое-то количество команд может изменить вердикт. Каждое такое расхождение разобрать поимённо и записать в отчёт: это либо дыра, которую объединение закрыло (тогда baseline обновляется и в отчёте появляется строка «поведение изменилось намеренно»), либо ложное срабатывание (тогда паттерн уточняется). **Молчаливое обновление baseline запрещено.**

Run: `cd service && uv run pytest tests/shell/test_secrets.py tests/equivalence -q`

- [ ] **Step 5: `ParsedArgv` — один разбор argv вместо четырёх циклов**

`service/tests/shell/test_argv.py`:

```python
from agentgate.shell.argv import ParsedArgv

VALUE_FLAGS = frozenset({"-o", "--output", "-T", "--upload-file"})


def test_executable_is_the_first_token():
    assert ParsedArgv.of(["curl", "-s", "http://x"]).executable == "curl"


def test_positionals_exclude_flags():
    assert ParsedArgv.of(["cp", "-r", "a", "b"]).positionals == ("a", "b")


def test_separate_value_is_not_a_positional():
    parsed = ParsedArgv.of(["curl", "-T", "secret.txt", "http://x"], VALUE_FLAGS)
    assert parsed.positionals == ("http://x",)


def test_separate_value_is_reachable_by_flag_name():
    parsed = ParsedArgv.of(["curl", "-T", "secret.txt", "http://x"], VALUE_FLAGS)
    assert parsed.option("-T").value == "secret.txt"


def test_inline_long_value_is_parsed():
    parsed = ParsedArgv.of(["curl", "--output=out.txt"], VALUE_FLAGS)
    assert parsed.option("--output").value == "out.txt"


def test_inline_value_is_marked_inline():
    assert ParsedArgv.of(["curl", "--output=out.txt"], VALUE_FLAGS).option("--output").inline


def test_missing_option_is_none():
    assert ParsedArgv.of(["curl", "http://x"]).option("-T") is None


def test_has_reports_a_valueless_flag():
    assert ParsedArgv.of(["rm", "-rf", "/"]).has("-rf")


def test_values_of_collects_every_named_flag():
    parsed = ParsedArgv.of(["curl", "-T", "a", "-T", "b"], VALUE_FLAGS)
    assert parsed.values_of("-T") == ("a", "b")


def test_double_dash_ends_option_parsing():
    parsed = ParsedArgv.of(["rm", "--", "-weird-file"])
    assert parsed.positionals == ("-weird-file",)


def test_empty_argv_has_no_executable():
    assert ParsedArgv.of([]).executable == ""
```

Run: `cd service && uv run pytest tests/shell/test_argv.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.shell.argv'`.

`service/agentgate/shell/argv.py`:

```python
"""One structural read of an argv.

Four hand-rolled `while index < len(argv)` loops used to answer four
variations of the same question -- which tokens are flags, which flag
took the next token as its value, what is left over as a positional.
They are one parse now, and the callers ask it questions.

Which flags take a separate value is per-command knowledge the caller
supplies; this module only knows the shapes (`-o value`, `-o=value`,
`--output=value`, `--`).
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Option:
    name: str
    value: str | None
    inline: bool


@dataclass(frozen=True)
class ParsedArgv:
    executable: str
    positionals: tuple[str, ...]
    options: tuple[Option, ...]

    @classmethod
    def of(cls, argv: Sequence[str], value_flags: frozenset[str] = frozenset()) -> "ParsedArgv":
        if not argv:
            return cls("", (), ())
        options: list[Option] = []
        positionals: list[str] = []
        index = 1
        while index < len(argv):
            token = argv[index]
            index += 1
            if token == "--":
                positionals.extend(argv[index:])
                break
            if not token.startswith("-") or token == "-":
                positionals.append(token)
                continue
            name, separator, inline_value = token.partition("=")
            if separator:
                options.append(Option(name, inline_value, inline=True))
                continue
            if name in value_flags and index < len(argv):
                options.append(Option(name, argv[index], inline=False))
                index += 1
                continue
            options.append(Option(name, None, inline=False))
        return cls(argv[0], tuple(positionals), tuple(options))

    def option(self, name: str) -> Option | None:
        for candidate in self.options:
            if candidate.name == name:
                return candidate
        return None

    def has(self, name: str) -> bool:
        return self.option(name) is not None

    def values_of(self, *names: str) -> tuple[str, ...]:
        return tuple(
            option.value for option in self.options
            if option.name in names and option.value is not None
        )
```

Run: `cd service && uv run pytest tests/shell/test_argv.py -v`
Expected: 11 passed.

- [ ] **Step 6: Перевести четыре цикла hard-deny на `ParsedArgv`**

Переписать по одному, прогоняя корпус после каждого:

1. `shared._positional_args(argv, value_flags)` → `ParsedArgv.of(argv, frozenset(value_flags)).positionals`. Удалить функцию.
2. `exfil._sent_secret_paths` — заменить ручной обход на `parsed = ParsedArgv.of(effective, upload_value_flags)` и `parsed.values_of(*UPLOAD_FLAG_NAMES)`. Кластеризованные короткие флаги (`-sT`) обрабатывает `_match_upload_flag`; оставить его, но принимать на вход `Option`, а не сырой токен.
3. `exfil._excluded_read_paths` — то же самое через `parsed.values_of(*IGNORE_VALUE_FLAGS)`.
4. `exfil._consumes_piped_stdin` — через `parsed.positionals` и `parsed.has(...)`.

Run после каждого: `cd service && uv run pytest tests/equivalence tests/rules -q`
Expected: зелено на каждом шаге. Красный корпус означает, что перенос изменил поведение — откатить конкретный цикл и разобрать различие, а не подгонять baseline.

Проверка, что комprehension-дубли ушли:
```bash
cd service && grep -rn 'startswith("-")' agentgate/rules/ | wc -l
```
Expected: заметно меньше нынешних 18; оставшиеся — только там, где это не разбор argv.

- [ ] **Step 7: Неизменяемое действие**

`service/agentgate/normalize/model.py`: `@dataclass` → `@dataclass(frozen=True)` для `Redirect`, `SimpleCommand`, `Flags`, `NormalizedAction`; `list` → `tuple` в полях, `field(default_factory=list)` → `field(default_factory=tuple)`.

`service/agentgate/normalize/shell.py`, `_Walker`: сейчас walker мутирует `action.flags.has_unresolved_expansion = True` и `action.paths = paths` уже после создания объекта, и `action_hash()` считается с мутабельной структуры. Переписать так, чтобы walker копил результат в собственных полях (`self._commands`, `self._paths`, `self._domains`, и набор булевых флагов), а `normalize_shell` собирал `NormalizedAction` один раз в конце:

```python
def normalize_shell(raw: str, cwd: str) -> NormalizedAction:
    walker = _Walker(cwd)
    try:
        walker.run(raw)
    except Exception:  # noqa: BLE001 - any bashlex failure means "not parseable", not a crash
        log.warning("shell normalization failed, treating the action as unparseable", exc_info=True)
        return NormalizedAction(tool=Tool.shell, cwd=cwd, raw=raw, flags=Flags(unparseable=True))
    return walker.result(raw)
```

`walker.result(raw)` возвращает готовый неизменяемый `NormalizedAction`. Молчаливый `except Exception` на строке 483 получает логирование (G1, гайд 7.2) — поведение (`unparseable`) не меняется, но баг в собственном коде перестаёт быть неотличим от мусорного ввода.

Проверить, что `action.to_dict()` даёт ту же форму: `asdict` на frozen-датаклассе с `tuple` вернёт списки только после `json.dumps`; если форма меняется (`tuple` вместо `list` в `to_dict`), привести явно — `action_hash` считается через `json.dumps`, который сериализует и то и другое как массив, но `to_dict()` уходит в JSONB и в baseline. Проверяется корпусом.

Run: `cd service && uv run pytest tests/equivalence tests/normalize tests/rules -q`
Expected: зелено. Любое расхождение в `action.to_dict()` — это изменение формы JSONB-колонки; разобрать и записать.

- [ ] **Step 8: Перенести тесты нормализатора и прогнать всё**

```bash
cd service && mkdir -p tests/normalize && touch tests/normalize/__init__.py
git mv tests/test_normalize_shell.py tests/normalize/test_shell.py
git mv tests/test_normalize_paths.py tests/normalize/test_paths.py
git mv tests/test_normalize_domains.py tests/normalize/test_domains.py
git mv tests/test_normalize_init.py tests/normalize/test_init.py
```

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: всё зелёное.

Run:
```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
```
Expected: пустой diff.

- [ ] **Step 9: Commit**

```bash
git add service/agentgate/shell service/agentgate/normalize service/agentgate/rules service/tests/shell service/tests/normalize service/tests/equivalence service/tests/factories.py
git commit -m "refactor(service): public shell/ package, one argv parse, frozen action

resolve_effective_argv and the wrapper tables move out of the
normalizer's privates into shell/wrappers.py, so stage 1 stops importing
underscored names across a package boundary. ParsedArgv replaces four
hand-rolled argv loops. SECRET_PATTERNS and _SENSITIVE_BASENAMES merge
into shell/secrets.py. NormalizedAction and friends are frozen and built
once.

An equivalence corpus over every table-test command guards the change
and is removed after task 7.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

