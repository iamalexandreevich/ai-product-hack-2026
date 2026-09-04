### Task 3: `Rule` + `RuleChain`, и `hard_deny.py` разбирается на правила

Закрывает: F5 (две цепочки одной формы и спецслучай в хвосте), F6 (три места знают про `unparseable`), F14 в части `hard_deny.py` (922 строки, 26 «fix round», 22 «Important N»).

Это самая механическая задача плана и самая заметная судьям: после неё ступень 1 — один список объектов.

**Files:**
- Create: `service/agentgate/rules/__init__.py`, `service/agentgate/rules/base.py`, `service/agentgate/rules/chain.py`, `service/agentgate/rules/unparseable.py`, `service/agentgate/rules/allowlist.py`, `service/agentgate/rules/profile_paths.py`, `service/agentgate/rules/profile_domains.py`, `service/agentgate/rules/packages.py`, `service/agentgate/rules/argv_paths.py`, `service/agentgate/rules/hard_deny/__init__.py`, `service/agentgate/rules/hard_deny/shared.py`, `service/agentgate/rules/hard_deny/exfil.py`, `service/agentgate/rules/hard_deny/pipe_exec.py`, `service/agentgate/rules/hard_deny/destructive.py`, `service/agentgate/rules/hard_deny/protected_write.py`, `service/agentgate/rules/hard_deny/privilege.py`, `service/agentgate/rules/hard_deny/git_force.py`, `service/agentgate/rules/hard_deny/wrapper_unresolved.py`
- Delete: `service/agentgate/stage1/` целиком (`types.py`, `chain.py`, `hard_deny.py`, `allowlist.py`, `profile_check.py`, `packages.py`, `argv_paths.py`)
- Modify: `service/agentgate/engine/gate.py`, `service/agentgate/stage2/run.py` (ветка `unparseable` удаляется)
- Test: `service/tests/rules/test_base.py`, `service/tests/rules/test_chain.py`, `service/tests/rules/test_unparseable.py`, `service/tests/rules/hard_deny/test_rules.py` (перенос табличных тестов), `service/tests/rules/test_allowlist.py`, `service/tests/rules/test_profile_paths.py`, `service/tests/rules/test_profile_domains.py`
- Move: `service/tests/test_stage1_hard_deny.py` → `service/tests/rules/hard_deny/test_rules.py`; `service/tests/test_stage1_chain.py` → `service/tests/rules/test_chain.py`; `service/tests/test_stage1_latency.py` → `service/tests/rules/test_latency.py`
- Docs: `docs/reports/task-5-hard-deny.md` (дополнить историей ревью, вырезанной из комментариев)

**Interfaces:**
- Produces: `agentgate.rules.base.Rule` (Protocol): атрибуты `id: str`, `hard: bool`, метод `evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None`. `agentgate.rules.base.RuleChain` с `evaluate(action, profile) -> Verdict | None`.
- Produces: `agentgate.rules.chain.STAGE1` — собранная `RuleChain` в фиксированном порядке.
- Produces: по одному классу правила в модуле: `UnparseableRule`, `ExfilRule`, `PipeExecRule`, `DestructiveRule`, `ProtectedWriteRule`, `PrivilegeRule`, `GitForceRule`, `WrapperUnresolvedRule`, `ProfilePathRule`, `ProfileDomainRule`, `AllowlistRule`, `PackagesRule`.
- Consumes: `Verdict` (задача 1).

- [ ] **Step 1: Failing-тест для `Rule` и `RuleChain`**

`service/tests/rules/__init__.py`, `service/tests/rules/hard_deny/__init__.py`: пустые файлы.

`service/tests/rules/test_base.py`:

```python
from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.rules.base import RuleChain


class StaticRule:
    """A rule that answers the same thing every time, for chain tests."""

    def __init__(self, rule_id: str, verdict: Verdict | None, hard: bool = False) -> None:
        self.id = rule_id
        self.hard = hard
        self._verdict = verdict
        self.calls = 0

    def evaluate(self, action, profile):
        self.calls += 1
        return self._verdict


def test_empty_chain_says_nothing():
    assert RuleChain([]).evaluate(None, None) is None


def test_chain_returns_the_first_verdict():
    chain = RuleChain([
        StaticRule("a", None),
        StaticRule("b", Verdict.deny("b", "no")),
        StaticRule("c", Verdict.allow("c")),
    ])
    assert chain.evaluate(None, None).rule_id == "b"


def test_chain_stops_at_the_first_verdict():
    later = StaticRule("c", Verdict.allow("c"))
    RuleChain([StaticRule("b", Verdict.deny("b", "no")), later]).evaluate(None, None)
    assert later.calls == 0


def test_chain_says_nothing_when_every_rule_is_silent():
    assert RuleChain([StaticRule("a", None), StaticRule("b", None)]).evaluate(None, None) is None


def test_chain_asks_every_rule_until_one_answers():
    first, second = StaticRule("a", None), StaticRule("b", None)
    RuleChain([first, second]).evaluate(None, None)
    assert (first.calls, second.calls) == (1, 1)
```

Run: `cd service && uv run pytest tests/rules/test_base.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.rules'`.

- [ ] **Step 2: Реализовать `Rule` и `RuleChain`**

`service/agentgate/rules/__init__.py`: пустой файл.
`service/agentgate/rules/hard_deny/__init__.py`: см. шаг 6.

`service/agentgate/rules/base.py`:

```python
"""What a stage 1 rule is, and the one loop that runs them.

A rule answers about one thing and answers three ways: a Verdict that
settles the action, or None meaning "nothing I know about applies".
None never means "I could not tell" -- a rule that recognizes danger it
cannot pin down returns an ask instead, so silence is never mistaken for
safety.

`hard` on the rule declares the strength of the denials it produces:
hard-deny is final and no later step may replace it.
"""

from collections.abc import Sequence
from typing import Protocol

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile


class Rule(Protocol):
    id: str
    hard: bool

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None: ...


class RuleChain:
    def __init__(self, rules: Sequence[Rule]) -> None:
        self._rules = tuple(rules)

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        for rule in self._rules:
            verdict = rule.evaluate(action, profile)
            if verdict is not None:
                return verdict
        return None
```

Run: `cd service && uv run pytest tests/rules/test_base.py -v`
Expected: 5 passed.

- [ ] **Step 3: `UnparseableRule` — единственное место, знающее про неразобранную команду**

`service/tests/rules/test_unparseable.py`:

```python
from agentgate.api.schemas import DecideRequest, DecisionKind
from agentgate.normalize import normalize
from agentgate.rules.unparseable import UnparseableRule
from tests.factories import WORKSPACE, profile


def action(raw: str):
    return normalize(DecideRequest(
        harness="t", tool="shell", raw=raw, args={"cwd": WORKSPACE}, user_request="x",
    ))


def test_says_nothing_about_a_command_it_could_parse():
    assert UnparseableRule().evaluate(action("ls -la"), profile()) is None


def test_asks_about_a_command_it_could_not_parse():
    unparseable = action("ls -la")
    object.__setattr__(unparseable.flags, "unparseable", True)
    verdict = UnparseableRule().evaluate(unparseable, profile())
    assert verdict.decision is DecisionKind.ask


def test_the_ask_is_stage_one_and_names_no_model():
    unparseable = action("ls -la")
    object.__setattr__(unparseable.flags, "unparseable", True)
    verdict = UnparseableRule().evaluate(unparseable, profile())
    assert verdict.stage == 1 and verdict.model is None


def test_the_ask_is_not_hard():
    unparseable = action("ls -la")
    object.__setattr__(unparseable.flags, "unparseable", True)
    assert UnparseableRule().evaluate(unparseable, profile()).hard is False
```

Заметка: `object.__setattr__` здесь нужен только пока `Flags` — обычный dataclass; после задачи 4 (`frozen=True`) тесты этого модуля переписываются на конструирование `Flags(unparseable=True)` в фабрике. Проще сразу завести в `tests/factories.py` функцию:

```python
def unparseable_action(raw: str = "ls -la", cwd: str = WORKSPACE):
    """A NormalizedAction whose command bashlex could not parse."""
    from agentgate.api.schemas import DecideRequest
    from agentgate.normalize import normalize

    return normalize(DecideRequest(
        harness="t", tool="shell", raw="ls -la $(", args={"cwd": cwd}, user_request="x",
    ))
```

и пользоваться реально неразбираемой строкой (`ls -la $(` — незакрытая подстановка) вместо подмены флага. Тесты выше переписать на `unparseable_action()`; проверить в шаге 4, что `flags.unparseable is True` для этой строки, иначе подобрать другую из уже существующих в `tests/test_normalize_shell.py`.

`service/agentgate/rules/unparseable.py`:

```python
"""An action bashlex could not structurally parse.

`commands`, `paths` and `domains` are empty by construction for such an
action, so no later rule can have looked at anything real, and the
classifier would be answering about a command it never saw. First in the
chain, so both facts stay true.
"""

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile


class UnparseableRule:
    id = "unparseable"
    hard = False

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        if not action.flags.unparseable:
            return None
        return Verdict.ask(
            self.id,
            "action could not be structurally parsed and was never verified",
        )
```

- [ ] **Step 4: Прогнать тест `unparseable`**

Run: `cd service && uv run pytest tests/rules/test_unparseable.py -v`
Expected: 4 passed. Если `unparseable_action()` не даёт `flags.unparseable is True`, взять строку из существующего теста в `tests/test_normalize_shell.py`, который уже проверяет этот флаг, и повторить прогон.

- [ ] **Step 5: Перенести шесть hard-deny правил в отдельные модули**

Механический перенос: содержимое `agentgate/stage1/hard_deny.py` разбирается по модулям без изменения логики.

`service/agentgate/rules/hard_deny/shared.py` — всё, чем пользуется больше одного правила: константы `SECRET_PATTERNS`, `NETWORK_COMMANDS`, `DOWNLOADERS`, `SHELLS`, `INTERPRETERS`, `WRITE_COMMANDS`, `FIREWALL`, `_LAST_ARG_WRITE_COMMANDS` (переименовать в `LAST_ARG_WRITE_COMMANDS`), `_EFFECTIVE_WRAPPERS` (→ `EFFECTIVE_WRAPPERS`), и функции `_effective` (→ `effective_argv`), `_wrapper_chain_unresolved` (→ `wrapper_chain_unresolved`), `_consumed_a_possible_command`, `_is_secret` (→ `is_secret`), `_cmd_paths` (→ `command_paths`), `_by_pipeline` (→ `by_pipeline`), `_flag_value`, `_looks_remote`, `_positional_args`, `_match_upload_flag`, `_upload_flag_value_paths`, и константы флагов `_UPLOAD_FLAGS`, `_SHORT_UPLOAD_LETTERS`, `_CLUSTERING_UPLOAD_COMMANDS`, `_IGNORE_VALUE_FLAGS`, `_SCP_RSYNC_VALUE_FLAGS`, `_REMOTE_DEST`. Ведущее подчёркивание снимается только у имён, которые импортирует другой модуль (гайд 4.3); чисто внутренние остаются приватными.

Каждое правило — один модуль с одним классом. Шаблон (на примере exfil; остальные пять по той же форме):

`service/agentgate/rules/hard_deny/exfil.py`:

```python
"""Sending a secret out of the machine.

Direction-aware: a secret merely READ by a command is not an exfil; the
rule fires when a secret is what gets transmitted -- an upload flag's
value, a redirect into a network command, or a pipeline whose sending end
actually consumes the piped stdin.
"""

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.rules.hard_deny import shared


class ExfilRule:
    id = "hard-deny.exfil"
    hard = True

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        ...  # тело _rule_exfil без изменений, с self.id вместо литерала
```

Правила разложения:
- `_rule_exfil` → `exfil.py::ExfilRule`, вместе с `_sent_secret_paths`, `_excluded_read_paths`, `_read_role_paths`, `_consumes_piped_stdin`, `_STDIN_FORWARDING_COMMANDS` (они больше нигде не используются — проверить `grep`).
- `_rule_pipe_exec` → `pipe_exec.py::PipeExecRule`.
- `_rule_destructive` → `destructive.py::DestructiveRule`, вместе с `_FIND_NARROWING_PREDICATES`, `_FIND_TRIVIAL_VALUES`, `_find_has_narrowing_predicate`.
- `_rule_protected_write` → `protected_write.py::ProtectedWriteRule`.
- `_rule_privilege` → `privilege.py::PrivilegeRule`.
- `_rule_git_force` → `git_force.py::GitForceRule`, вместе с `_GIT_GLOBAL_OPTS_WITH_VALUE`, `_git_push_argv`, `_is_force_flag`, `_SYMBOLIC_REFS`, `_normalize_branch_ref`.
- Хвостовой блок `check_hard_deny` (проверка `_wrapper_chain_unresolved` после шести правил) → `wrapper_unresolved.py::WrapperUnresolvedRule` — обычное правило в цепочке, а не спецслучай:

```python
class WrapperUnresolvedRule:
    """A command whose wrapper chain could not be resolved to a real
    command -- too deep to follow, or consumed whole into an option value.

    No rule above could have evaluated such a command, so its silence is
    not evidence of safety.
    """

    id = "ambiguous.wrapper"
    hard = False

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        for command in action.commands:
            why = shared.wrapper_chain_unresolved(command.argv)
            if why == "depth":
                return Verdict.ask("ambiguous.wrapper-depth", _DEPTH_REASON.format(...), _DEPTH_SUGGEST)
            if why == "opaque":
                return Verdict.ask("ambiguous.wrapper-opaque", _OPAQUE_REASON.format(...), _OPAQUE_SUGGEST)
        return None
```

Тексты `reason`/`suggest` перенести буквально из нынешнего `check_hard_deny` — их проверяют существующие тесты. `rule_id` остаётся `"ambiguous.wrapper-depth"` / `"ambiguous.wrapper-opaque"`, поэтому `id` класса — общее имя, а конкретный `rule_id` вердикта задаётся в месте возврата.

**Комментарии.** При переносе из каждого docstring и комментария вырезать всё, что ссылается на процесс: «fix round N», «Important N», «Critical N», «task N», «см. ревью», «mid-round amendment», «verified by direct reproduction», даты. Остаётся инвариант в одну-две фразы (гайд 5.1–5.3). Вырезанный текст не выбрасывается: он дописывается в `docs/reports/task-5-hard-deny.md` разделом «История ревью правил hard-deny», по подразделу на правило.

Проверка полноты:
```bash
cd service && grep -rniE "fix round|important [0-9]|critical [0-9]|task [0-9]|mid-round" agentgate/rules/
```
Expected: пусто.

- [ ] **Step 6: Собрать список hard-deny правил**

`service/agentgate/rules/hard_deny/__init__.py`:

```python
"""The rules that can never be overridden.

Order is the order they run in: the first verdict wins, and every one of
these produces a final deny. WrapperUnresolvedRule closes the set -- it
fires only when none of the six above could have evaluated the command
at all.
"""

from agentgate.rules.hard_deny.destructive import DestructiveRule
from agentgate.rules.hard_deny.exfil import ExfilRule
from agentgate.rules.hard_deny.git_force import GitForceRule
from agentgate.rules.hard_deny.pipe_exec import PipeExecRule
from agentgate.rules.hard_deny.privilege import PrivilegeRule
from agentgate.rules.hard_deny.protected_write import ProtectedWriteRule
from agentgate.rules.hard_deny.wrapper_unresolved import WrapperUnresolvedRule

HARD_DENY_RULES = [
    ExfilRule(),
    PipeExecRule(),
    DestructiveRule(),
    ProtectedWriteRule(),
    PrivilegeRule(),
    GitForceRule(),
    WrapperUnresolvedRule(),
]

__all__ = [
    "HARD_DENY_RULES", "DestructiveRule", "ExfilRule", "GitForceRule", "PipeExecRule",
    "PrivilegeRule", "ProtectedWriteRule", "WrapperUnresolvedRule",
]
```

Циклический импорт: `exfil.py` делает `from agentgate.rules.hard_deny import shared`, а `__init__.py` импортирует `exfil`. Чтобы этого избежать, в правилах импортировать модуль напрямую: `from agentgate.rules.hard_deny.shared import effective_argv, is_secret, ...`.

- [ ] **Step 7: Обернуть три оставшиеся проверки в правила**

`service/agentgate/rules/argv_paths.py` — перенос `stage1/argv_paths.py` без изменений, кроме вычистки процессных ссылок из docstring.

`service/agentgate/rules/profile_paths.py`:

```python
class ProfilePathRule:
    """Mutating filesystem targets must resolve inside the profile's
    allowed paths. Reading outside the workspace is not denied here -- it
    falls through to the classifier.
    """

    id = "profile.path"
    hard = False

    def evaluate(self, action, profile):
        ...  # тело check_profile, часть про пути
```

`service/agentgate/rules/profile_domains.py` — `ProfileDomainRule`, часть `check_profile` про домены. Разделение на два правила — это SRP (гайд 2.3): путь и сеть меняются по разным причинам, и в цепочке они теперь видны как две строки.

`service/agentgate/rules/allowlist.py` — `AllowlistRule`, тело `check_allowlist` без изменений; `READONLY`, `GIT_READONLY`, `_is_readonly`, `_matches_prefix` переезжают вместе с ним.

`service/agentgate/rules/packages.py`:

```python
class PackagesRule:
    """Slot for the slopsquatting / package module. Always silent in v1."""

    id = "packages"
    hard = False

    def evaluate(self, action, profile):
        return None
```

- [ ] **Step 8: Собрать цепочку**

`service/agentgate/rules/chain.py`:

```python
"""Stage 1 in one place: the order the rules run in.

Hard-deny first, so it always wins over any later allow or ask.
UnparseableRule opens the chain -- nothing below it can evaluate an
action bashlex could not parse.
"""

from agentgate.rules.allowlist import AllowlistRule
from agentgate.rules.base import RuleChain
from agentgate.rules.hard_deny import HARD_DENY_RULES
from agentgate.rules.packages import PackagesRule
from agentgate.rules.profile_domains import ProfileDomainRule
from agentgate.rules.profile_paths import ProfilePathRule
from agentgate.rules.unparseable import UnparseableRule

STAGE1 = RuleChain([
    UnparseableRule(),
    *HARD_DENY_RULES,
    ProfilePathRule(),
    ProfileDomainRule(),
    AllowlistRule(),
    PackagesRule(),
])
```

- [ ] **Step 9: Убрать `unparseable` из ступени 2 и из `Gate`**

`service/agentgate/stage2/run.py` — удалить ветку `if action.flags.unparseable:` целиком: до неё дело больше не доходит, `UnparseableRule` останавливает цепочку раньше. Из docstring убрать абзац про `flags.unparseable`.

`service/agentgate/engine/gate.py`:
- Импорт `run_stage1` заменить на `from agentgate.rules.chain import STAGE1`.
- Константу `STAGE1_SKIPPED` удалить (F6: `_NOTE_SKIPPED` исчезает).
- `_evaluate` упрощается:

```python
    async def _evaluate(
        self, request: DecideRequest, action: NormalizedAction, context: _Context, timings: Timings
    ) -> Verdict:
        with timings.stage(1):
            verdict = self._rules.evaluate(action, context.profile)
        if verdict is not None:
            return verdict
        with timings.stage(2):
            client = LLMClient(context.model_name, context.model_config, self._http)
            return await run_stage2(
                action, request.user_request, context.profile,
                context.model_name, client, STAGE1_PASSED,
            )
```

- `Gate.__init__` принимает цепочку параметром (гайд 2.1 — зависимость от абстракции, а не от импорта): `rules: RuleChain`, дефолт не задавать; `bootstrap` в задаче 6 передаёт `STAGE1`. Пока — передавать `STAGE1` из `__main__.build_app` и из тестовой фабрики `gate()`.

- [ ] **Step 10: Перенести табличные тесты**

`git mv service/tests/test_stage1_hard_deny.py service/tests/rules/hard_deny/test_rules.py`
`git mv service/tests/test_stage1_chain.py service/tests/rules/test_chain.py`
`git mv service/tests/test_stage1_latency.py service/tests/rules/test_latency.py`

В `tests/rules/hard_deny/test_rules.py`:
- `from agentgate.stage1.hard_deny import check_hard_deny` → `from agentgate.rules.base import RuleChain` + `from agentgate.rules.hard_deny import HARD_DENY_RULES`, и локальный хелпер:
  ```python
  HARD_DENY = RuleChain(HARD_DENY_RULES)


  def check_hard_deny(action, profile):
      return HARD_DENY.evaluate(action, profile)
  ```
  Так все 546 табличных ожиданий остаются буквально теми же.
- Тест `test_check_alias_matches_check_hard_deny_signature` (импортирует удалённый `stage1.types.Check`) заменить на проверку, что каждое правило удовлетворяет протоколу:
  ```python
  def test_every_hard_deny_rule_declares_itself_hard():
      assert all(rule.hard for rule in HARD_DENY_RULES if rule.id.startswith("hard-deny."))


  def test_every_rule_has_an_id():
      assert all(rule.id for rule in HARD_DENY_RULES)
  ```
- Тест, импортирующий `_rule_exfil`, → `from agentgate.rules.hard_deny import ExfilRule` и `ExfilRule().evaluate(a, PROFILE) is None`.
- Фикстуры `WS`, `PROFILE`, `shell()`, `fw()` заменить на импорт из `tests.factories`, если совпадают; профиль табличных тестов отличается от `factories.profile()` (свои `protected_paths`, `protected_branches`), поэтому добавить в `tests/factories.py` вторую фабрику `hard_deny_profile()` с этим составом и импортировать её.

В `tests/rules/test_chain.py`: `from agentgate.stage1.chain import run_stage1` → `from agentgate.rules.chain import STAGE1`, вызовы `run_stage1(a, p)` → `STAGE1.evaluate(a, p)`. Импорты `from agentgate.stage1.allowlist import check_allowlist` (строки 116, 150, 159) → `from agentgate.rules.allowlist import AllowlistRule` и `AllowlistRule().evaluate(...)`.

В `tests/rules/test_latency.py`: `from tests.test_stage1_chain import P, WS` → `from tests.factories import ...`; `run_stage1` → `STAGE1.evaluate`.

**Ожидаемое изменение в `test_chain.py`.** Если в нём есть кейс на неразобранную команду, ожидавший `None` (падение в ступень 2), теперь он получит `ask` с `rule_id="unparseable"` — это санкционированный рулинг 1. Такой кейс переписать и добавить рядом явный тест:
```python
def test_unparseable_is_settled_by_stage_one():
    verdict = STAGE1.evaluate(unparseable_action(), profile())
    assert verdict.rule_id == "unparseable" and verdict.stage == 1
```
Все остальные ожидания цепочки остаются буквально прежними.

- [ ] **Step 11: Удалить `stage1/` и прогнать всё**

```bash
cd service && git rm -r agentgate/stage1
```

Run:
```bash
cd service && grep -rn "agentgate.stage1\|stage1\." agentgate/ tests/ scripts/ | grep -v "stage1_ms\|stage1=\|latency_stage1\|\"stage1\"\|STAGE1"
```
Expected: пусто — не осталось ссылок на удалённый пакет.

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: всё зелёное, включая `tests/rules/test_latency.py` (бюджет p50 ≤ 1 мс: цепочка объектов вместо цепочки функций добавляет по одному разыменованию атрибута на правило — если тест падает, замерить и записать результат в отчёт, но не ослаблять порог; при необходимости заменить `Protocol` на прямой вызов без изменения структуры).

Run:
```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
```
Expected: пустой diff. `DecideResponse` не меняется — меняется только значение поля `stage` для неразобранных команд, а не его тип.

- [ ] **Step 12: Обновить спеку и закоммитить**

В `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`, §5.1 — заменить строку про отправку неразобранного действия в ступень 2 на:

> Неразобранное действие (`flags.unparseable`) закрывается ступенью 1 правилом `unparseable`: `ask`, `stage: 1`, `model: null`. Классификатор не вызывается — он отвечал бы о команде, которую не видел.

```bash
git add service/agentgate/rules service/agentgate/engine/gate.py service/agentgate/stage2/run.py service/agentgate/__main__.py service/tests/rules service/tests/factories.py docs/reports/task-5-hard-deny.md docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md
git rm -r service/agentgate/stage1
git commit -m "refactor(service): stage 1 becomes one chain of Rule objects

hard_deny.py (922 lines) splits into one module per rule; the tail
special case becomes WrapperUnresolvedRule and the unparseable check
becomes UnparseableRule, so stage 2 no longer refuses actions it was
never meant to see. Review history moves from comments to
docs/reports/task-5-hard-deny.md.

Contract change: an unparseable action now reports stage 1 with
rule_id=unparseable and no model, instead of stage 2 with a model that
was never called. Spec 5.1 updated.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

