# AgentGate v1.5 SOLID Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Переписать форму сервиса AgentGate v1 под OOP + SOLID + DRY, не меняя наблюдаемого поведения: один тип решения вместо четырёх, правила как объекты в одной цепочке, четыре протокола вместо прибитых зависимостей, один composition root.

**Architecture:** Ядро (`domain/`) — чистые неизменяемые типы без I/O: `Verdict`, `Policy`, `NormalizedAction`, `SessionState`. Вокруг него четыре протокола — `Rule`, `Classifier`, `DecisionWriter`, `SessionStateStore` — с конкретными реализациями в `rules/`, `classify/`, `store/`, `session/`. `Gate` в `engine/` только оркестрирует: resolve → normalize → cache → rules → classifier → session, и возвращает один `Decision`. Всё собирается в `bootstrap.py`; ни ядро, ни `Gate` не знают про HTTP, Postgres и httpx.

**Tech Stack:** Python 3.12, uv, FastAPI, pydantic v2, SQLAlchemy 2 (async) + asyncpg, bashlex, httpx, pytest + pytest-asyncio, Postgres 16.

**Spec:** `docs/reports/code-quality-review-and-refactor-plan.md` — ревью текущего кода, находки F1–F16 / L1–L3 / G1–G3, целевая архитектура (раздел 5), порядок шагов (раздел 6) и сверка со стайл-гайдом (раздел 10). Каждая задача ниже ссылается на находки, которые она закрывает.

**Сопутствующие документы:** спека v1 `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md` (§5 конвейер, §5.4 состояние сессии, §6 workspace, §10 масштабирование); `CLAUDE.md` и `service/CLAUDE.md` — инварианты; персональный стайл-гайд в `~/.claude/CLAUDE.md` — правила, на которые ссылаются шаги (номера вида «гайд 3.5»).

---

## Global Constraints

Требования этого раздела действуют в каждой задаче — они не повторяются в шагах.

**Поведение**

- Рефакторинг, а не изменение поведения. **Ни одно табличное ожидание в `tests/test_stage1_hard_deny.py`, `tests/test_stage1_chain.py`, `tests/test_normalize_shell.py` не меняется.** Если шаг требует поменять ожидание — это не рефакторинг; остановиться и вынести отдельно.
- Два исключения из предыдущего пункта, оба санкционированы (см. «Принятые решения» ниже): `stage` для unparseable в задаче 3 и привязка workspace к сессии в задаче 5. Оба меняют контракт видимо и оба несут собственный регрессионный тест.
- Fail-closed везде: ошибка, таймаут, невалидный запрос или ответ → `ask` с HTTP 200. `allow` по ошибке недостижим. Любой код, возвращающий `allow`, имеет тест на путь отказа.
- Hard-deny не переопределяется и не заменяется эскалацией.
- Решение по сырой строке запрещено; только по `NormalizedAction`.
- Ступень 2 reasoning-blind: в промпт идут только профиль, prose-слоты, `[TASK]`, `[ACTION]`, `[FLAGS]`, `[STAGE1]`. `metadata`, выводы инструментов, рассуждения агента — никогда.
- `deny`/`ask` не кэшируются; кэшируется только `allow`.
- Только Postgres (asyncpg). Ретраев к LLM нет: один вызов, один таймаут.
- Latency ступени 1 p50 ≤ 1 мс; `tests/test_stage1_latency.py` падает при регрессии и должен оставаться зелёным в каждой задаче.

**Контракт**

- `DecideRequest` и `DecideResponse` не меняют форму до задачи 8. После каждой задачи 1–7 обязательна проверка:
  ```bash
  cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
  ```
  Пустой diff — доказательство, что публичный контракт не поехал. Задачи 5 и 8 меняют `contracts/` намеренно и говорят об этом явно.
- `contracts/` меняется только PR-ом с упоминанием всех трёх направлений (`contracts/README.md`). Это касается задач 5 и 8.

**Стиль (полный текст — `~/.claude/CLAUDE.md`)**

- Protocol в сигнатурах и полях, конкретика через конструктор (2.1). Один Protocol — одна роль (2.2).
- Value objects вместо связок примитивов, `frozen=True` по умолчанию (3.4, 3.5).
- Функции 5–15 строк, один уровень абстракции (3.1, 3.3). Параметров ≥ 4 — кандидат на value object.
- Имена без аббревиатур, кроме `url`/`id`/`http` (4.2). Переименования из G2 делаются в том модуле, который задача и так переписывает, — отдельного шага «переименовать всё» нет.
- Комментарии только для неочевидного «почему» (5.1, 5.2). **Ни одной ссылки на задачу, PR, автора, дату, «fix round N», «Important N» (5.3).** Такая история переезжает в `docs/reports/`, в коде остаётся инвариант в одну-две фразы.
- Docstring описывает контракт, не реализацию (5.4).
- Fakes вместо `mock.patch`/`monkeypatch.setattr` для зависимостей (6.1). `monkeypatch.setenv` для `Settings` и `interpolate_env` остаётся: окружение — граница системы (7.1).
- Тесты зеркалят исходники: `agentgate/foo/bar.py` → `tests/foo/test_bar.py` (6.3). Каждая задача переносит тесты тех модулей, которые она трогает; тесты, которых задача не касается, остаются на месте.
- `ids=` в `parametrize` — на английском (6.4). Один тест — одно утверждение (6.5); разделяются только те тесты, которые задача и так переносит.
- `except Exception` без логирования или переброса запрещён (7.2).
- Код и комментарии — английский; документация и отчёты — русский; идентификаторы API не переводятся.

**Процесс**

- Ветка `refactor/solid-v1.5`, уже создана от `main` (`6153110`).
- Коммит после каждой задачи, только явные пути в `git add`. Сообщение заканчивается строкой:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```
- Полный прогон перед каждым коммитом: `cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest`. Без переменной тесты, требующие БД, скипаются — этого недостаточно для коммита задач 2, 6, 7.
- Отчёт после каждой задачи: `docs/reports/task-<N>-<slug>.md` на русском — что построено, доказательства TDD, находки ревью и как закрыты, принятые решения, что отложено.

---

## Принятые решения (рулинги по открытым вопросам ревью)

Ревью оставило три вопроса владельцу продукта (раздел 8). Работа не блокируется на них; ниже решения, принятые по умолчанию, и цена, если решение неверно. Владелец может отменить любое — правка локальна в названной задаче.

1. **F6, `stage` для unparseable → `1`.** `UnparseableRule` становится первым правилом цепочки, поэтому неразобранная команда получает `stage: 1`, `rule_id: "unparseable"`, `model: null` вместо нынешних `stage: 2`, `model: "<имя модели>"`. Основание: сейчас ступень 2 объявляется пройденной для действия, которое LLM никогда не видела, — `run_stage2` отказывает до вызова классификатора. `stage: 2` с `model` при нулевом вызове модели — неверная телеметрия. Правится в спеке §5.1 одной строкой. **Цена ошибки:** бенчмарк, который группирует решения по `stage`, увидит сдвиг доли ступени 1; данные не теряются, `rule_id` различает случай однозначно.
2. **F4, workspace привязывается к сессии.** Спека §6 говорит «`${WORKSPACE}` подставляется из `args.cwd` первого запроса сессии», код подставляет из `cwd` каждого запроса. Реализуем спеку. **Цена ошибки:** харнесс, который намеренно меняет `cwd` между вызовами одной сессии, получит политику первого `cwd`; лечится новой сессией. Обратное (текущее) поведение — дыра, воспроизведённая таблицей в F4: агент после `cd /` расширяет песочницу до корня.
3. **Задача 8 (OpenAPI) требует PR с упоминанием трёх направлений** по правилу `contracts/README.md`. Задача написана, но её мердж — процессное решение владельца, а не техническое. Задачи 1–7 от неё не зависят.

---

## Карта файлов

Целевое дерево (раздел 5 ревью). Задача, которая создаёт файл, указана в скобках.

```
service/agentgate/
  domain/                    # чистые типы, без I/O
    verdict.py               #   Verdict — единственный тип решения            (задача 1)
    policy.py                #   Policy — профиль ⊗ workspace                  (задача 5)
    action.py                #   NormalizedAction, SimpleCommand, Flags        (задача 4, из normalize/model.py)
    session.py               #   SessionState                                  (задача 6, из session/state.py)
  engine/
    timings.py               #   Timings (секундомер) → Latency (frozen)       (задача 1)
    decision.py              #   Decision, DecisionView                        (задача 2)
    gate.py                  #   Gate: только оркестрация                      (задача 2, из pipeline.py)
  rules/                     # ступень 1
    base.py                  #   Rule (Protocol), RuleChain                    (задача 3)
    chain.py                 #   STAGE1 = RuleChain([...])                     (задача 3)
    unparseable.py           #                                                 (задача 3)
    hard_deny/               #   exfil, pipe_exec, destructive, protected_write,
                             #   privilege, git_force, wrapper_unresolved      (задача 3)
    profile_paths.py, profile_domains.py, allowlist.py, packages.py            (задача 3)
  shell/
    wrappers.py              #   resolve_effective_argv + таблицы, публично    (задача 4)
    argv.py                  #   ParsedArgv                                    (задача 4)
    secrets.py               #   SECRET_PATTERNS — один список                 (задача 4)
    commands.py              #   CommandSpec — одна таблица команд             (задача 7)
  classify/
    base.py                  #   Classifier (Protocol)                         (задача 6)
    llm.py                   #   LLMClassifier                                 (задача 6, из stage2/run.py)
  session/
    persistent.py            #   память + write-through + restore()            (задача 6)
  store/
    writer.py                #   DecisionWriter, Postgres/Jsonl/Composite      (задача 2)
    mapper.py                #   Decision ↔ DecisionRow, одно место            (задача 6)
  bootstrap.py               #   единственный composition root                 (задача 6)
```

Тесты зеркалят это дерево (`tests/domain/`, `tests/engine/`, `tests/rules/hard_deny/`, `tests/shell/`, `tests/store/`, `tests/classify/`, `tests/session/`).

---

### Task 1: `Verdict` — один тип исхода вместо двух

Закрывает: F1 (частично — тип решения), F5 (жёсткость закодирована дважды), G2 в затронутых модулях.

**Files:**
- Create: `service/agentgate/domain/__init__.py`, `service/agentgate/domain/verdict.py`, `service/agentgate/engine/__init__.py`, `service/agentgate/engine/timings.py`
- Modify: `service/agentgate/stage1/types.py` (остаётся только `Check`), `service/agentgate/stage1/hard_deny.py` (удалить `_deny`/`_ask`), `service/agentgate/stage1/allowlist.py`, `service/agentgate/stage1/profile_check.py`, `service/agentgate/stage1/packages.py`, `service/agentgate/stage1/chain.py`, `service/agentgate/stage2/run.py` (удалить `Stage2Result`), `service/agentgate/pipeline.py`
- Test: `service/tests/domain/test_verdict.py`, `service/tests/engine/test_timings.py`
- Create: `service/tests/domain/__init__.py`, `service/tests/engine/__init__.py`

**Interfaces:**
- Produces: `agentgate.domain.verdict.Verdict` — frozen dataclass с полями `decision: DecisionKind`, `stage: int`, `rule_id: str | None = None`, `reason: str = ""`, `suggest: str = ""`, `hard: bool = False`, `model: str | None = None`, `raw_response: dict | None = None`, `error: str | None = None`; классметоды `Verdict.allow(rule_id, *, stage=1)`, `Verdict.deny(rule_id, reason, suggest="", *, stage=1, hard=False)`, `Verdict.ask(rule_id, reason, suggest="", *, stage=1)`; метод `Verdict.escalated(hits: int) -> Verdict`.
- Produces: `agentgate.engine.timings.Timings` — секундомер с методом-контекстом `stage(number: int)` и `finish() -> Latency`; `agentgate.engine.timings.Latency` — frozen dataclass `total_ms: int`, `stage1_ms: int | None = None`, `stage2_ms: int | None = None`, метод `to_schema() -> LatencyMs`.
- Consumes: `agentgate.api.schemas.DecisionKind`, `agentgate.api.schemas.LatencyMs` (существуют).

- [ ] **Step 1: Проверить, что тесты не конструируют удаляемые типы**

Run:
```bash
cd service && grep -rn "Stage1Decision\|Stage2Result" tests/ ; echo "exit=$?"
```
Expected: ни одного совпадения (`exit=1` от grep). Тесты обращаются к результатам только по атрибутам (`.decision`, `.rule_id`, `.reason`, `.suggest`, `.model`, `.error`, `.raw_response`), а эти имена `Verdict` сохраняет. Если совпадения есть — выписать их и адаптировать в шаге 7 вместе с остальными вызывающими.

- [ ] **Step 2: Пакеты и failing-тест для `Verdict`**

`service/agentgate/domain/__init__.py`: пустой файл.
`service/agentgate/engine/__init__.py`: пустой файл.
`service/tests/domain/__init__.py`: пустой файл.
`service/tests/engine/__init__.py`: пустой файл.

`service/tests/domain/test_verdict.py`:

```python
import dataclasses

import pytest

from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict


def test_allow_carries_rule_and_stage():
    verdict = Verdict.allow("allowlist.readonly")
    assert verdict.decision is DecisionKind.allow
    assert verdict.rule_id == "allowlist.readonly"
    assert verdict.stage == 1


def test_allow_never_carries_reason_or_suggest():
    verdict = Verdict.allow("allowlist.readonly")
    assert verdict.reason == "" and verdict.suggest == ""


def test_deny_is_soft_unless_asked_to_be_hard():
    assert Verdict.deny("profile.path", "outside").hard is False


def test_hard_deny_is_marked_hard():
    assert Verdict.deny("hard-deny.exfil", "secret sent", hard=True).hard is True


def test_ask_is_never_hard():
    assert Verdict.ask("ambiguous.wrapper-depth", "cannot resolve").hard is False


def test_classifier_verdict_carries_model_and_raw_response():
    verdict = Verdict(
        decision=DecisionKind.deny, stage=2, reason="why", suggest="alt",
        model="qwen-4b", raw_response={"choices": []},
    )
    assert verdict.stage == 2 and verdict.model == "qwen-4b"
    assert verdict.raw_response == {"choices": []}


def test_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Verdict.allow("allowlist.readonly").decision = DecisionKind.deny


def test_escalated_turns_any_verdict_into_ask():
    escalated = Verdict.deny("profile.path", "outside", "stay inside").escalated(3)
    assert escalated.decision is DecisionKind.ask
    assert escalated.rule_id == "escalation"
    assert escalated.suggest == ""


def test_escalated_reason_names_the_hit_count():
    assert "3" in Verdict.deny("profile.path", "outside").escalated(3).reason


def test_escalated_keeps_the_stage_of_the_verdict_it_replaces():
    assert Verdict.deny("profile.path", "x", stage=1).escalated(2).stage == 1
```

- [ ] **Step 3: Прогнать тест — он должен падать**

Run: `cd service && uv run pytest tests/domain/test_verdict.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.domain'`.

- [ ] **Step 4: Реализовать `Verdict`**

`service/agentgate/domain/verdict.py`:

```python
"""The single outcome type of the cascade.

Stage 1 rules, the stage 2 classifier, the allow cache and the API's own
early refusals all answer the same question -- what happens to this
action -- so they answer it with one type.

`hard` marks a verdict no later step may replace: escalation refuses to
touch it, and stage 2 is never reached past it.
"""

from dataclasses import dataclass, replace

from agentgate.api.schemas import DecisionKind


@dataclass(frozen=True)
class Verdict:
    decision: DecisionKind
    stage: int
    rule_id: str | None = None
    reason: str = ""
    suggest: str = ""
    hard: bool = False
    model: str | None = None
    raw_response: dict | None = None
    error: str | None = None

    @classmethod
    def allow(cls, rule_id: str, *, stage: int = 1) -> "Verdict":
        return cls(decision=DecisionKind.allow, stage=stage, rule_id=rule_id)

    @classmethod
    def deny(
        cls, rule_id: str, reason: str, suggest: str = "", *, stage: int = 1, hard: bool = False
    ) -> "Verdict":
        return cls(
            decision=DecisionKind.deny, stage=stage, rule_id=rule_id,
            reason=reason, suggest=suggest, hard=hard,
        )

    @classmethod
    def ask(cls, rule_id: str, reason: str, suggest: str = "", *, stage: int = 1) -> "Verdict":
        return cls(
            decision=DecisionKind.ask, stage=stage, rule_id=rule_id,
            reason=reason, suggest=suggest,
        )

    def escalated(self, hits: int) -> "Verdict":
        """The verdict this one becomes when the session has hit the policy
        `hits` times in a row and a human should look at the task.

        Callers must not apply this to a hard verdict -- hard-deny is never
        replaced by an ask.
        """
        return replace(
            self,
            decision=DecisionKind.ask,
            rule_id="escalation",
            reason=f"agent hit the policy {hits} times; a human should review the task",
            suggest="",
        )
```

- [ ] **Step 5: Прогнать тест — он должен пройти**

Run: `cd service && uv run pytest tests/domain/test_verdict.py -v`
Expected: 10 passed.

- [ ] **Step 6: Failing-тест и реализация `Timings`**

`service/tests/engine/test_timings.py`:

```python
from agentgate.engine.timings import Latency, Timings


def test_unmeasured_stages_are_none():
    latency = Timings().finish()
    assert latency.stage1_ms is None and latency.stage2_ms is None


def test_total_is_always_measured():
    assert Timings().finish().total_ms >= 0


def test_measured_stage_is_reported():
    timings = Timings()
    with timings.stage(1):
        pass
    latency = timings.finish()
    assert latency.stage1_ms is not None and latency.stage2_ms is None


def test_stage_is_recorded_even_when_the_body_raises():
    timings = Timings()
    try:
        with timings.stage(2):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert timings.finish().stage2_ms is not None


def test_to_schema_maps_onto_the_wire_model():
    schema = Latency(total_ms=7, stage1_ms=1, stage2_ms=5).to_schema()
    assert (schema.stage1, schema.stage2, schema.total) == (1, 5, 7)
```

Run: `cd service && uv run pytest tests/engine/test_timings.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.engine.timings'`.

`service/agentgate/engine/timings.py`:

```python
"""Wall-clock measurement of one decide() call.

`Timings` is the stopwatch a call carries; `Latency` is the immutable
result it hands to the decision. A stage that was never entered stays
None -- "not measured" and "measured as zero" are different facts, and a
cache hit must not claim it ran the rules in 0 ms.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from agentgate.api.schemas import LatencyMs


@dataclass(frozen=True)
class Latency:
    total_ms: int
    stage1_ms: int | None = None
    stage2_ms: int | None = None

    def to_schema(self) -> LatencyMs:
        return LatencyMs(stage1=self.stage1_ms, stage2=self.stage2_ms, total=self.total_ms)


class Timings:
    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._stages: dict[int, int] = {}

    @contextmanager
    def stage(self, number: int) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self._stages[number] = _elapsed_ms(started)

    def finish(self) -> Latency:
        return Latency(
            total_ms=_elapsed_ms(self._started),
            stage1_ms=self._stages.get(1),
            stage2_ms=self._stages.get(2),
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
```

Run: `cd service && uv run pytest tests/engine/test_timings.py -v`
Expected: 5 passed.

- [ ] **Step 7: Перевести ступень 1 на `Verdict`**

`service/agentgate/stage1/types.py` — целиком заменить на:

```python
from collections.abc import Callable

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile

Check = Callable[[NormalizedAction, Profile], Verdict | None]
```

`service/agentgate/stage1/hard_deny.py`:
- В импортах заменить `from agentgate.stage1.types import Stage1Decision` на `from agentgate.domain.verdict import Verdict`.
- Удалить функции `_deny` и `_ask` целиком (строки 212–224).
- Заменить каждый вызов `_deny("<rule>", reason, suggest)` на `Verdict.deny(f"hard-deny.<rule>", reason, suggest, hard=True)` — литерал, не f-строка: подставить имя правила прямо в строку, например `_deny("exfil", …)` → `Verdict.deny("hard-deny.exfil", …, hard=True)`.
- Заменить каждый вызов `_ask("<rule>", reason, suggest)` на `Verdict.ask("ambiguous.<rule>", reason, suggest)`.
- Заменить аннотации возврата `Stage1Decision | None` на `Verdict | None` во всех шести `_rule_*` и в `check_hard_deny`.

Run для полноты замены:
```bash
cd service && grep -n "_deny(\|_ask(\|Stage1Decision" agentgate/stage1/hard_deny.py
```
Expected: пусто.

`service/agentgate/stage1/allowlist.py`:
- Импорт `from agentgate.stage1.types import Stage1Decision` → `from agentgate.domain.verdict import Verdict`.
- `Stage1Decision(DecisionKind.allow, "allowlist.file_read", "")` → `Verdict.allow("allowlist.file_read")`; аналогично для `allowlist.file_write`, `allowlist.prefix`, `allowlist.readonly`.
- Аннотация `check_allowlist(...) -> Verdict | None`.
- Импорт `DecisionKind` из `agentgate.api.schemas` становится неиспользуемым — оставить только `Tool`.

`service/agentgate/stage1/profile_check.py`:
- Импорт `Stage1Decision` → `Verdict`.
- `Stage1Decision(DecisionKind.deny, "profile.path", f"write outside allowed paths: {p}", "Work inside the workspace")` → `Verdict.deny("profile.path", f"write outside allowed paths: {p}", "Work inside the workspace")`.
- `Stage1Decision(DecisionKind.ask, "profile.domain", f"domain {d} is not in the allowlist", "")` → `Verdict.ask("profile.domain", f"domain {d} is not in the allowlist")`.
- `Stage1Decision(DecisionKind.deny, "profile.domain", …, "Use an allowed registry or ask the user to extend the allowlist")` → `Verdict.deny("profile.domain", …, "Use an allowed registry or ask the user to extend the allowlist")`.
- Аннотация `check_profile(...) -> Verdict | None`. `DecisionKind` больше не нужен в импортах.

`service/agentgate/stage1/packages.py` и `service/agentgate/stage1/chain.py`: заменить `Stage1Decision` на `Verdict` в импортах и аннотациях. В `chain.py` `run_stage1(...) -> Verdict | None`.

- [ ] **Step 8: Перевести ступень 2 на `Verdict`**

`service/agentgate/stage2/run.py` — удалить `@dataclass class Stage2Result` и `from dataclasses import dataclass`, добавить `from agentgate.domain.verdict import Verdict`, заменить тело:

```python
async def run_stage2(
    action: NormalizedAction,
    user_request: str,
    profile: Profile,
    model_name: str,
    client: LLMClient,
    stage1_note: str,
) -> Verdict:
    if action.flags.unparseable:
        return Verdict.ask(
            None,
            "action could not be structurally parsed and was never verified",
            stage=2,
        )._with_model(model_name)

    system = build_system_prompt(profile)
    user = build_user_message(action, user_request, stage1_note)
    try:
        out, raw = await client.classify(system, user)
    except Stage2Error as exc:
        return _unavailable(model_name, exc.kind, f"classifier unavailable: {exc.kind}")
    except Exception as exc:  # noqa: BLE001 - fail closed on anything, not just Stage2Error
        log.warning("classifier raised an unexpected error", exc_info=True)
        return _unavailable(
            model_name, "unexpected", f"classifier unavailable: unexpected ({type(exc).__name__})"
        )

    decision = _MAP[out.decision]
    allowed = decision is DecisionKind.allow
    return Verdict(
        decision=decision,
        stage=2,
        reason="" if allowed else out.reason,
        suggest="" if allowed else out.suggest,
        model=model_name,
        raw_response=raw,
    )


def _unavailable(model_name: str, error: str, reason: str) -> Verdict:
    return Verdict(
        decision=DecisionKind.ask, stage=2, reason=reason, model=model_name, error=error
    )
```

`Verdict._with_model` не вводить — вместо `Verdict.ask(...)._with_model(...)` в ветке `unparseable` написать прямую конструкцию:

```python
    if action.flags.unparseable:
        return Verdict(
            decision=DecisionKind.ask,
            stage=2,
            reason="action could not be structurally parsed and was never verified",
            model=model_name,
        )
```

Добавить в начало модуля `import logging` и `log = logging.getLogger(__name__)` — молчаливый `except Exception` из G1 теперь логируется (гайд 7.2). Docstring модуля сохранить, убрав из него ссылку на «Task 4» и «Task 7» (гайд 5.3).

- [ ] **Step 9: Снять с `pipeline.py` знание о двух типах**

`service/agentgate/pipeline.py`, строки 123–136 — заменить распаковку в десять локальных на работу с одним `Verdict`:

```python
        timings = Timings()
        with timings.stage(1):
            verdict = None if action.flags.unparseable else run_stage1(action, profile)
        if verdict is None:
            note = _NOTE_SKIPPED if action.flags.unparseable else _NOTE_PASSED
            with timings.stage(2):
                client = LLMClient(model_name, model_cfg, self._http)
                verdict = await run_stage2(action, req.user_request, profile, model_name, client, note)
```

и строки 138–147 (эскалация):

```python
        if state is not None and not verdict.hard and verdict.decision is not DecisionKind.ask \
                and should_escalate(state, profile.escalation):
            verdict = verdict.escalated(state.deny_consecutive)
            state.reset_after_escalation()
```

`service/agentgate/session/state.py` — добавить метод в `SessionState` (L1: пайплайн больше не лезет во внутренности состояния):

```python
    def reset_after_escalation(self) -> None:
        """Start counting afresh once a human has been asked.

        Without this the very next call would escalate again immediately.
        """
        self.deny_consecutive = 0
        self.recent.clear()
```

Сборка `DecideResponse` и `_record` в этой задаче ещё берут поля из `verdict` по одному (`verdict.decision`, `verdict.reason`, …) — целиком они уходят в задаче 2. Локальные `t0`, `t1`, `t2`, `s1`, `s2`, `_ms` удалить, использовать `Timings`. `stage` для ответа — `verdict.stage`.

- [ ] **Step 10: Полный прогон и проверка контракта**

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: все тесты зелёные, включая 546 табличных hard-deny, `test_stage2_run.py` (его ассерты `res.decision/.model/.error/.raw_response/.reason/.suggest` работают на `Verdict` без правок) и `test_stage1_latency.py`.

Run:
```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
```
Expected: пустой diff, код возврата 0.

- [ ] **Step 11: Commit**

```bash
git add service/agentgate/domain service/agentgate/engine service/agentgate/stage1 service/agentgate/stage2/run.py service/agentgate/session/state.py service/agentgate/pipeline.py service/tests/domain service/tests/engine
git commit -m "refactor(service): one Verdict type for every stage outcome

Stage1Decision and Stage2Result collapse into domain.verdict.Verdict;
escalation becomes Verdict.escalated() and SessionState owns its own
reset. Timings replaces the loose perf_counter locals.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `Decision`, `DecisionWriter` и разбор `Gate.decide()`

Закрывает: F1 (оркестратор), F2 (два пути персистентности), F16 (тесты импортируют фикстуры друг у друга), L3 (запись сцеплена с нормализацией), G1 (молчаливые `except`).

**Files:**
- Create: `service/agentgate/engine/decision.py`, `service/agentgate/engine/gate.py`, `service/agentgate/store/writer.py`, `service/tests/factories.py`, `service/tests/store/__init__.py`, `service/tests/store/test_writer.py`, `service/tests/engine/test_decision.py`, `service/tests/engine/test_gate.py`
- Delete: `service/agentgate/pipeline.py`, `service/tests/test_pipeline.py`
- Modify: `service/agentgate/api/app.py` (удалить замыкание `persist`, `_ask`, `_CACHE_TTL_SECONDS`), `service/agentgate/__main__.py`, `service/agentgate/config.py` (TTL кэша — поле `Settings`), `service/tests/test_api.py`
- Test: `service/tests/engine/test_gate.py` (замена `tests/test_pipeline.py`), `service/tests/store/test_writer.py`

**Interfaces:**
- Produces: `agentgate.engine.decision.Decision` — frozen dataclass: `id: str`, `ts: datetime`, `request: DecideRequest`, `verdict: Verdict`, `latency: Latency`, `profile_id: str`, `profile_hash: str`, `action: NormalizedAction | None = None`, `state: SessionState | None = None`, `cache_key: str | None = None`, `cached: bool = False`; методы `to_response() -> DecideResponse` и `to_view() -> DecisionView`.
- Produces: `agentgate.engine.decision.DecisionView` — pydantic-модель плоской формы решения (persisted и читаемая через API), поле `id` плюс `@computed_field decision_id`.
- Produces: `agentgate.store.writer.DecisionWriter` (Protocol, `async def write(self, decision: Decision) -> None`, никогда не бросает), `PostgresDecisionWriter(decisions, sessions, cache_ttl_seconds)`, `JsonlDecisionWriter(logger)`, `CompositeDecisionWriter(writers)`.
- Produces: `agentgate.engine.gate.Gate(profiles, default_profile, state_store, http, cache_ttl_seconds)` с `async def decide(self, req: DecideRequest) -> Decision`.
- Produces: `tests.factories` — `WORKSPACE`, `profile(**overrides)`, `decide_request(raw, **overrides)`, `FakeLLM`, `RecordingDecisionWriter`.
- Consumes: `Verdict`, `Timings`, `Latency` (задача 1).

- [ ] **Step 1: `tests/factories.py` — общие фабрики вместо импортов между тестами**

`service/tests/factories.py`:

```python
"""Shared builders for tests. Test modules import from here, never from
each other -- renaming a test module must not break three others.
"""

import json

import httpx

from agentgate.api.schemas import DecideRequest
from agentgate.engine.decision import Decision
from agentgate.profiles.schema import Profile

WORKSPACE = "/home/u/repo"


def profile(**overrides) -> Profile:
    data = {
        "id": "default",
        "allowed_paths": ["${WORKSPACE}"],
        "protected_paths": [".env*"],
        "network": {"mode": "allowlist", "allowed_domains": ["pypi.org"]},
        "models": {
            "default": "m",
            "configs": {
                "m": {"base_url": "http://llm/v1", "model": "q", "timeout_ms": 500},
                "m2": {"base_url": "http://llm2/v1", "model": "q2"},
            },
        },
        "escalation": {"deny_consecutive": 2, "deny_window": {"count": 10, "of_last": 50}},
    }
    data.update(overrides)
    return Profile.model_validate(data)


def decide_request(raw: str, session_id: str | None = "s1", **overrides) -> DecideRequest:
    data = dict(
        session_id=session_id, harness="t", tool="shell", raw=raw,
        args={"cwd": WORKSPACE}, user_request="task",
    )
    data.update(overrides)
    return DecideRequest.model_validate(data)


class FakeLLM:
    """An httpx MockTransport handler standing in for the OpenAI-compatible
    endpoint. Replaced by a Classifier fake in task 6 -- until the protocol
    exists, the transport is the only seam.
    """

    def __init__(self, decision: str = "A", reason: str = "r", suggest: str = "s", status: int = 200) -> None:
        self.calls = 0
        self.decision, self.reason, self.suggest, self.status = decision, reason, suggest, status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.status != 200:
            return httpx.Response(self.status)
        body = {"decision": self.decision, "risk": "none", "reason": self.reason, "suggest": self.suggest}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})


class RecordingDecisionWriter:
    """A DecisionWriter that keeps what it was given, in order."""

    def __init__(self) -> None:
        self.decisions: list[Decision] = []

    async def write(self, decision: Decision) -> None:
        self.decisions.append(decision)


class FailingDecisionWriter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("writer is down")
        self.calls = 0

    async def write(self, decision: Decision) -> None:
        self.calls += 1
        raise self.error
```

- [ ] **Step 2: Failing-тест для `Decision`**

`service/tests/engine/test_decision.py`:

```python
from datetime import datetime, timezone

from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Latency
from tests.factories import decide_request


def decision(**overrides) -> Decision:
    data = dict(
        id="01J0", ts=datetime.now(timezone.utc), request=decide_request("ls -la"),
        verdict=Verdict.allow("allowlist.readonly"),
        latency=Latency(total_ms=3, stage1_ms=1),
        profile_id="default", profile_hash="h" * 64,
    )
    data.update(overrides)
    return Decision(**data)


def test_response_carries_the_verdict():
    response = decision().to_response()
    assert response.decision is DecisionKind.allow
    assert response.rule_id == "allowlist.readonly"
    assert response.stage == 1


def test_response_decision_id_is_the_decision_id():
    assert decision().to_response().decision_id == "01J0"


def test_response_latency_comes_from_the_measured_stages():
    response = decision().to_response()
    assert response.latency_ms.stage1 == 1 and response.latency_ms.stage2 is None


def test_view_exposes_both_id_and_decision_id():
    dumped = decision().to_view().model_dump(mode="json")
    assert dumped["id"] == "01J0" and dumped["decision_id"] == "01J0"


def test_view_normalized_is_empty_when_nothing_was_normalized():
    assert decision(action=None).to_view().normalized == {}


def test_view_does_not_smuggle_the_cache_key_into_normalized():
    view = decision(cache_key="k" * 64).to_view()
    assert "cache_key" not in view.normalized


def test_view_ts_serializes_as_an_iso_string():
    dumped = decision().to_view().model_dump(mode="json")
    assert isinstance(dumped["ts"], str) and dumped["ts"].startswith(str(datetime.now(timezone.utc).year))
```

Run: `cd service && uv run pytest tests/engine/test_decision.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.engine.decision'`.

- [ ] **Step 3: Реализовать `Decision` и `DecisionView`**

`service/agentgate/engine/decision.py`:

```python
"""One decision, and the one flat shape it is stored and read in.

`Decision` is what the engine produces: the request, the verdict, what
was normalized, how long it took. `DecisionView` is the flat projection
every consumer outside the engine sees -- the JSONL line, the Postgres
row, and the items of GET /v1/decisions are the same shape, defined
once, so a field cannot exist in the log and be missing from the API.

The view carries both `id` and `decision_id`: `decision_id` is the name
the public contract uses everywhere else, `id` is what the log and the
row have always been keyed by. One field, two spellings, no second
source of truth.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, computed_field

from agentgate.api.schemas import DecideRequest, DecideResponse, DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.timings import Latency
from agentgate.normalize.model import NormalizedAction
from agentgate.session.state import SessionState

from dataclasses import dataclass


class DecisionView(BaseModel):
    id: str
    session_id: str | None
    ts: datetime
    harness: str
    tool: str
    raw: str
    normalized: dict[str, Any]
    user_request: str
    profile_id: str
    profile_hash: str
    decision: DecisionKind
    reason: str
    suggest: str
    stage: int
    rule_id: str | None
    model: str | None
    model_raw_response: dict[str, Any] | None
    latency_stage1_ms: int | None
    latency_stage2_ms: int | None
    latency_total_ms: int
    error: str | None
    cached: bool
    metadata: dict[str, Any]

    @computed_field
    @property
    def decision_id(self) -> str:
        return self.id


@dataclass(frozen=True)
class Decision:
    id: str
    ts: datetime
    request: DecideRequest
    verdict: Verdict
    latency: Latency
    profile_id: str
    profile_hash: str
    action: NormalizedAction | None = None
    state: SessionState | None = None
    cache_key: str | None = None
    cached: bool = False

    def to_response(self) -> DecideResponse:
        return DecideResponse(
            decision=self.verdict.decision,
            reason=self.verdict.reason,
            suggest=self.verdict.suggest,
            stage=self.verdict.stage,
            rule_id=self.verdict.rule_id,
            model=self.verdict.model,
            latency_ms=self.latency.to_schema(),
            cached=self.cached,
            decision_id=self.id,
        )

    def to_view(self) -> DecisionView:
        return DecisionView(
            id=self.id,
            session_id=self.request.session_id,
            ts=self.ts,
            harness=self.request.harness,
            tool=self.request.tool.value,
            raw=self.request.raw,
            normalized=self.action.to_dict() if self.action is not None else {},
            user_request=self.request.user_request,
            profile_id=self.profile_id,
            profile_hash=self.profile_hash,
            decision=self.verdict.decision,
            reason=self.verdict.reason,
            suggest=self.verdict.suggest,
            stage=self.verdict.stage,
            rule_id=self.verdict.rule_id,
            model=self.verdict.model,
            model_raw_response=self.verdict.raw_response,
            latency_stage1_ms=self.latency.stage1_ms,
            latency_stage2_ms=self.latency.stage2_ms,
            latency_total_ms=self.latency.total_ms,
            error=self.verdict.error,
            cached=self.cached,
            metadata=self.request.metadata,
        )
```

Run: `cd service && uv run pytest tests/engine/test_decision.py -v`
Expected: 7 passed.

- [ ] **Step 4: Failing-тест для `DecisionWriter`**

`service/tests/store/__init__.py`: пустой файл.

`service/tests/store/test_writer.py`:

```python
import logging
from datetime import datetime, timezone

from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Latency
from agentgate.store.writer import CompositeDecisionWriter, JsonlDecisionWriter, PostgresDecisionWriter
from tests.factories import FailingDecisionWriter, RecordingDecisionWriter, decide_request


def decision(verdict: Verdict | None = None, **overrides) -> Decision:
    data = dict(
        id="01J0", ts=datetime.now(timezone.utc), request=decide_request("ls -la"),
        verdict=verdict or Verdict.allow("allowlist.readonly"),
        latency=Latency(total_ms=1), profile_id="default", profile_hash="h" * 64,
    )
    data.update(overrides)
    return Decision(**data)


class FakeSessionRepo:
    def __init__(self) -> None:
        self.upserts: list[str] = []
        self.cache_puts: list[tuple[str, str, str]] = []

    async def upsert(self, state) -> None:
        self.upserts.append(state.session_id)

    async def cache_put(self, session_id, action_hash, decision_id, expires_at) -> None:
        self.cache_puts.append((session_id, action_hash, decision_id))


class FakeDecisionRepo:
    def __init__(self) -> None:
        self.inserted: list[Decision] = []

    async def insert(self, decision) -> None:
        self.inserted.append(decision)


class CollectingLogger:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, record: dict) -> None:
        self.lines.append(record)


def state(session_id: str = "s1"):
    from agentgate.session.state import SessionState

    return SessionState(session_id=session_id, harness="t", profile_id="default", workspace="/w")


async def test_jsonl_writer_writes_the_view_shape():
    logger = CollectingLogger()
    await JsonlDecisionWriter(logger).write(decision())
    assert logger.lines[0]["decision_id"] == "01J0"
    assert logger.lines[0]["id"] == "01J0"


async def test_postgres_writer_upserts_the_session_before_the_decision():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    order: list[str] = []
    sessions.upsert = lambda s: order.append("session") or _done()  # noqa: E731
    await PostgresDecisionWriter(decisions, sessions, 86400).write(decision(state=state()))
    assert order == ["session"] and len(decisions.inserted) == 1


async def test_postgres_writer_caches_an_allow():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(state=state(), cache_key="k" * 64)
    )
    assert sessions.cache_puts == [("s1", "k" * 64, "01J0")]


async def test_postgres_writer_never_caches_a_deny():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(verdict=Verdict.deny("profile.path", "outside"), state=state(), cache_key="k" * 64)
    )
    assert sessions.cache_puts == []


async def test_postgres_writer_never_recaches_a_cache_hit():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(
        decision(state=state(), cache_key="k" * 64, cached=True)
    )
    assert sessions.cache_puts == []


async def test_postgres_writer_skips_the_session_row_for_a_sessionless_call():
    sessions, decisions = FakeSessionRepo(), FakeDecisionRepo()
    await PostgresDecisionWriter(decisions, sessions, 86400).write(decision(state=None))
    assert sessions.upserts == [] and len(decisions.inserted) == 1


async def test_composite_runs_every_writer():
    first, second = RecordingDecisionWriter(), RecordingDecisionWriter()
    await CompositeDecisionWriter([first, second]).write(decision())
    assert len(first.decisions) == 1 and len(second.decisions) == 1


async def test_composite_isolates_a_failing_writer():
    failing, healthy = FailingDecisionWriter(), RecordingDecisionWriter()
    await CompositeDecisionWriter([failing, healthy]).write(decision())
    assert failing.calls == 1 and len(healthy.decisions) == 1


async def test_composite_logs_the_failure_it_swallowed(caplog):
    with caplog.at_level(logging.ERROR):
        await CompositeDecisionWriter([FailingDecisionWriter()]).write(decision())
    assert "01J0" in caplog.text
```

Убрать из теста `test_postgres_writer_upserts_the_session_before_the_decision` хак с лямбдой — записать порядок честным фейком:

```python
class OrderRecordingRepos:
    """Both repos sharing one order log, so 'session row before decision row'
    (the FK requirement) is asserted as an observable fact, not as a call count.
    """

    def __init__(self) -> None:
        self.order: list[str] = []

    async def upsert(self, state) -> None:
        self.order.append("session")

    async def cache_put(self, session_id, action_hash, decision_id, expires_at) -> None:
        self.order.append("cache")

    async def insert(self, decision) -> None:
        self.order.append("decision")


async def test_postgres_writer_writes_session_then_decision_then_cache():
    repos = OrderRecordingRepos()
    await PostgresDecisionWriter(repos, repos, 86400).write(decision(state=state(), cache_key="k" * 64))
    assert repos.order == ["session", "decision", "cache"]
```

Run: `cd service && uv run pytest tests/store/test_writer.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.store.writer'`.

- [ ] **Step 5: Реализовать `DecisionWriter`**

`service/agentgate/store/writer.py`:

```python
"""Where a decision goes after the answer has already been sent.

One protocol, three implementations. `write` never raises: a decision the
caller already has must not be undone by a storage failure, and one sink
failing must not stop the others.

The FK ordering (session row -> decision row -> allow-cache row) lives
here and nowhere else -- `decisions.session_id` references `sessions.id`
and `allow_cache.decision_id` references `decisions.id`.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol

from agentgate.api.schemas import DecisionKind
from agentgate.engine.decision import Decision

log = logging.getLogger(__name__)


class DecisionWriter(Protocol):
    async def write(self, decision: Decision) -> None: ...


class JsonlDecisionWriter:
    def __init__(self, logger) -> None:
        self._logger = logger

    async def write(self, decision: Decision) -> None:
        self._logger.write(decision.to_view().model_dump(mode="json"))


class PostgresDecisionWriter:
    def __init__(self, decisions, sessions, cache_ttl_seconds: int) -> None:
        self._decisions = decisions
        self._sessions = sessions
        self._cache_ttl_seconds = cache_ttl_seconds

    async def write(self, decision: Decision) -> None:
        if decision.state is not None:
            await self._sessions.upsert(decision.state)
        await self._decisions.insert(decision)
        if self._should_cache(decision):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._cache_ttl_seconds)
            await self._sessions.cache_put(
                decision.state.session_id, decision.cache_key, decision.id, expires_at
            )

    def _should_cache(self, decision: Decision) -> bool:
        return (
            decision.state is not None
            and decision.cache_key is not None
            and decision.verdict.decision is DecisionKind.allow
            and not decision.cached
        )


class CompositeDecisionWriter:
    def __init__(self, writers: Sequence[DecisionWriter]) -> None:
        self._writers = tuple(writers)

    async def write(self, decision: Decision) -> None:
        for writer in self._writers:
            try:
                await writer.write(decision)
            except Exception:  # noqa: BLE001 - one sink's failure must not stop the others
                log.exception(
                    "%s failed to write decision %s", type(writer).__name__, decision.id
                )
```

`DecisionRepo.insert` теперь принимает `Decision`, а не `DecisionRecord` — маппинг в строку переезжает в задачу 6. На время этой задачи добавить в `agentgate/store/repo.py` мост:

```python
    async def insert(self, decision) -> None:
        async with self._sf() as s:
            s.add(_row_from_view(decision.to_view()))
            await s.commit()
```

и модульную функцию `_row_from_view(view: DecisionView) -> DecisionRow`, которая делает единственное переименование `metadata → metadata_`:

```python
def _row_from_view(view) -> DecisionRow:
    data = view.model_dump(exclude={"decision_id"})
    data["metadata_"] = data.pop("metadata")
    return DecisionRow(**data)
```

`DecisionRecord` пока остаётся для `DecisionRepo.list` — уходит в задаче 6.

Run: `cd service && uv run pytest tests/store/test_writer.py -v`
Expected: 9 passed.

- [ ] **Step 6: TTL кэша — одно поле настроек**

`service/agentgate/config.py` — добавить в `Settings` поле после `default_profile`:

```python
    allow_cache_ttl_seconds: int = 86400
```

Оно заменяет `_CACHE_TTL_SECONDS` в `api/app.py` и дефолт `cache_ttl_seconds=86400` в `Gate.__init__` — знание о TTL перестаёт быть записанным дважды (гайд 1.3).

`service/tests/test_config.py` — добавить один тест:

```python
def test_allow_cache_ttl_defaults_to_a_day(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    assert Settings().allow_cache_ttl_seconds == 86400
```

- [ ] **Step 7: Failing-тест для нового `Gate`**

`service/tests/engine/test_gate.py` — перенос `tests/test_pipeline.py` с адаптацией под `Decision`. Все существующие проверки сохраняются; меняется только распаковка результата (`decision = await gate(...).decide(...)` вместо `resp, rec, state = ...`) и источник фикстур (`tests.factories`). Начало файла:

```python
import httpx

from agentgate.api.schemas import DecisionKind
from agentgate.engine.gate import Gate
from agentgate.session.memory import InMemorySessionStateStore
from tests.factories import WORKSPACE, FakeLLM, decide_request, profile


def gate(llm: FakeLLM, **profile_overrides) -> Gate:
    return Gate(
        profiles={"default": profile(**profile_overrides)},
        default_profile="default",
        state_store=InMemorySessionStateStore(),
        http=httpx.AsyncClient(transport=httpx.MockTransport(llm)),
        allow_cache_ttl_seconds=86400,
    )


async def test_stage1_allow_skips_the_classifier():
    llm = FakeLLM()
    decision = await gate(llm).decide(decide_request("ls -la"))
    assert decision.verdict.decision is DecisionKind.allow
    assert decision.verdict.rule_id == "allowlist.readonly"
    assert llm.calls == 0


async def test_stage1_allow_reports_no_model():
    decision = await gate(FakeLLM()).decide(decide_request("ls -la"))
    assert decision.to_response().model is None


async def test_stage1_allow_measures_stage1_but_not_stage2():
    decision = await gate(FakeLLM()).decide(decide_request("ls -la"))
    assert decision.latency.stage2_ms is None and decision.latency.total_ms >= 0


async def test_decision_records_what_was_normalized():
    decision = await gate(FakeLLM()).decide(decide_request("ls -la"))
    assert decision.action is not None and decision.action.to_dict()["tool"] == "shell"
    assert decision.profile_hash


async def test_session_counters_advance():
    decision = await gate(FakeLLM()).decide(decide_request("ls -la"))
    assert decision.state is not None and decision.state.decisions_total == 1
```

Остальные тесты `tests/test_pipeline.py` перенести один в один, разделяя те, что проверяют по нескольку фактов (гайд 6.5), и заменяя обращения:
- `resp.decision` → `decision.verdict.decision`
- `resp.stage` → `decision.verdict.stage`
- `resp.rule_id` → `decision.verdict.rule_id`
- `resp.cached` → `decision.cached`
- `rec.decision == "allow"` → `decision.verdict.decision is DecisionKind.allow`
- `rec.normalized["tool"]` → `decision.action.to_dict()["tool"]`
- проверки `persisted` (список из старого фейка `persist`) — удалить: запись больше не входит в `Gate`. Их предмет покрыт `tests/store/test_writer.py` и `tests/test_api.py`.

Run: `cd service && uv run pytest tests/engine/test_gate.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'agentgate.engine.gate'`.

- [ ] **Step 8: Реализовать `Gate`**

`service/agentgate/engine/gate.py` — новый модуль вместо `pipeline.py`. Оркестрация и только она; ни записи, ни знания о HTTP.

```python
"""The decision pipeline.

Fixed order: resolve the profile and the model -> normalize -> allow-cache
lookup -> stage 1 rules -> stage 2 classifier -> escalation -> session
state. Every step either produces a Verdict or hands the call to the next
one, and `decide` returns one immutable Decision.

Fail-closed is the spine: an unknown profile or model resolves to `ask`
before anything else runs, and stage 2 turns every classifier failure into
`ask` itself (see agentgate.stage2.run), so nothing here produces `allow`
on an error path.

Persistence is not this module's concern -- see agentgate.store.writer.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from ulid import ULID

from agentgate.api.schemas import DecideRequest, DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Timings
from agentgate.normalize import normalize
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.loader import with_workspace
from agentgate.profiles.schema import ModelConfig, Profile
from agentgate.session.cache_key import allow_cache_key
from agentgate.session.escalation import should_escalate
from agentgate.session.state import SessionState, SessionStateStore
from agentgate.stage1.chain import run_stage1
from agentgate.stage2.client import LLMClient
from agentgate.stage2.run import run_stage2

log = logging.getLogger(__name__)

STAGE1_PASSED = "passed: no hard-deny match, not in allowlist"
STAGE1_SKIPPED = "skipped: command unparseable"


@dataclass(frozen=True)
class _Context:
    """Everything resolved from the request before any rule runs."""

    profile: Profile
    profile_hash: str
    profile_id: str
    model_name: str
    model_config: ModelConfig
    state: SessionState | None


class Gate:
    def __init__(
        self,
        profiles: dict[str, Profile],
        default_profile: str,
        state_store: SessionStateStore,
        http: httpx.AsyncClient,
        allow_cache_ttl_seconds: int = 86400,
    ) -> None:
        self._profiles = profiles
        self._default_profile = default_profile
        self._states = state_store
        self._http = http
        self._allow_cache_ttl_seconds = allow_cache_ttl_seconds

    async def decide(self, request: DecideRequest) -> Decision:
        timings = Timings()
        decision_id = str(ULID())
        profile_id = request.profile_id or self._default_profile

        resolved = await self._resolve(request, profile_id)
        if isinstance(resolved, Verdict):
            return self._finish(decision_id, request, resolved, timings, profile_id, "")

        action = normalize(request)
        cache_key = allow_cache_key(
            resolved.profile_hash, action.action_hash(), request.user_request
        )
        if await self._cache_hit(resolved, cache_key):
            return self._finish(
                decision_id, request, Verdict.allow("cache", stage=0), timings,
                profile_id, resolved.profile_hash, action, resolved.state, cache_key, cached=True,
            )

        verdict = await self._evaluate(request, action, resolved, timings)
        verdict = self._escalate(resolved.state, resolved.profile, verdict)
        await self._settle_session(resolved.state, verdict, cache_key, decision_id)
        return self._finish(
            decision_id, request, verdict, timings, profile_id, resolved.profile_hash,
            action, resolved.state, cache_key,
        )

    async def _resolve(self, request: DecideRequest, profile_id: str) -> "_Context | Verdict":
        base = self._profiles.get(profile_id)
        if base is None:
            return Verdict.ask("api.unknown-profile", f"unknown profile '{profile_id}'", stage=0)
        try:
            model_name, model_config = base.models.model_config_for(request.model)
        except KeyError:
            return Verdict.ask("api.unknown-model", f"unknown model '{request.model}'", stage=0)

        profile = with_workspace(base, request.args.cwd)
        state = None
        if request.session_id:
            state = await self._states.get_or_create(
                request.session_id, request.harness, profile_id,
                profile.workspace or request.args.cwd,
            )
        return _Context(
            profile=profile, profile_hash=profile.profile_hash(), profile_id=profile_id,
            model_name=model_name, model_config=model_config, state=state,
        )

    async def _cache_hit(self, context: _Context, cache_key: str) -> bool:
        if context.state is None:
            return False
        return await self._states.cache_get(context.state.session_id, cache_key) is not None

    async def _evaluate(
        self, request: DecideRequest, action: NormalizedAction, context: _Context, timings: Timings
    ) -> Verdict:
        with timings.stage(1):
            verdict = None if action.flags.unparseable else run_stage1(action, context.profile)
        if verdict is not None:
            return verdict
        note = STAGE1_SKIPPED if action.flags.unparseable else STAGE1_PASSED
        with timings.stage(2):
            client = LLMClient(context.model_name, context.model_config, self._http)
            return await run_stage2(
                action, request.user_request, context.profile, context.model_name, client, note
            )

    def _escalate(self, state: SessionState | None, profile: Profile, verdict: Verdict) -> Verdict:
        if state is None or verdict.hard or verdict.decision is DecisionKind.ask:
            return verdict
        if not should_escalate(state, profile.escalation):
            return verdict
        hits = state.deny_consecutive
        state.reset_after_escalation()
        return verdict.escalated(hits)

    async def _settle_session(
        self, state: SessionState | None, verdict: Verdict, cache_key: str, decision_id: str
    ) -> None:
        if state is None:
            return
        state.record(verdict.decision)
        await self._states.save(state)
        if verdict.decision is DecisionKind.allow:
            await self._states.cache_put(
                state.session_id, cache_key, decision_id, self._allow_cache_ttl_seconds
            )

    def _finish(
        self, decision_id: str, request: DecideRequest, verdict: Verdict, timings: Timings,
        profile_id: str, profile_hash: str, action: NormalizedAction | None = None,
        state: SessionState | None = None, cache_key: str | None = None, cached: bool = False,
    ) -> Decision:
        return Decision(
            id=decision_id, ts=datetime.now(timezone.utc), request=request, verdict=verdict,
            latency=timings.finish(), profile_id=profile_id, profile_hash=profile_hash,
            action=action, state=state, cache_key=cache_key, cached=cached,
        )
```

Заметить (L3): ранний отказ по неизвестному профилю больше не нормализует запрос ради того, чтобы было что записать — `action` остаётся `None`, и `DecisionView.normalized` для такой строки будет `{}`.

Удалить `service/agentgate/pipeline.py` и `service/tests/test_pipeline.py`.

Run: `cd service && uv run pytest tests/engine -v`
Expected: все зелёные.

- [ ] **Step 9: Переключить `app.py` и `__main__.py` на writer**

`service/agentgate/api/app.py`:
- Удалить `_CACHE_TTL_SECONDS`, замыкание `persist` целиком (строки 80–99) и функцию `_ask` (строки 60–64).
- Сигнатура: `create_app(settings, gate, writer, decision_repo, profiles, db_probe=None, key_repo=None)` — `session_repo` и `jsonl` больше не нужны, их знает writer.
- Ранний отказ (невалидный JSON, невалидное тело, исключение из `decide`) собирается через `Verdict` + `Decision`:

```python
def _refuse(rule_id: str, reason: str) -> DecideResponse:
    return Verdict.ask(rule_id, reason, stage=0).to_response_for(str(ULID()))
```

Метод `to_response_for` на `Verdict` не вводить (гайд 1.2) — вместо него собрать `DecideResponse` прямо:

```python
def _refuse(rule_id: str, reason: str) -> DecideResponse:
    verdict = Verdict.ask(rule_id, reason, stage=0)
    return DecideResponse(
        decision=verdict.decision, reason=verdict.reason, stage=verdict.stage,
        rule_id=verdict.rule_id, model=None,
        latency_ms=LatencyMs(stage1=None, stage2=None, total=0), decision_id=str(ULID()),
    )
```

- Роут `decide`:

```python
    @app.post("/v1/decide", response_model=DecideResponse, dependencies=[auth])
    async def decide(request: Request, background: BackgroundTasks) -> DecideResponse:
        try:
            payload = await request.json()
        except ValueError:
            return _refuse("api.invalid-request", "request body is not valid JSON")
        try:
            parsed = DecideRequest.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            return _refuse("api.invalid-request", f"invalid request: {location}: {first.get('msg')}")
        try:
            decision = await gate.decide(parsed)
        except Exception as exc:  # noqa: BLE001 - fail-closed: no exception may escape as a 500
            log.exception("Gate.decide failed")
            return _refuse("api.internal-error", f"internal error: {type(exc).__name__}")
        background.add_task(writer.write, decision)
        return decision.to_response()
```

- Роут `/v1/decisions` пока оставить как есть (`decision_repo.list` + `to_dict`), он переезжает в задаче 6.

`service/agentgate/__main__.py` — в `build_app` собрать writer:

```python
    writer = CompositeDecisionWriter([
        JsonlDecisionWriter(JsonlLogger(settings.log_path)),
        PostgresDecisionWriter(decision_repo, session_repo, settings.allow_cache_ttl_seconds),
    ])
    gate = Gate(profiles, settings.default_profile, store, httpx.AsyncClient(),
                allow_cache_ttl_seconds=settings.allow_cache_ttl_seconds)
    app = create_app(settings, gate, writer, decision_repo, profiles,
                     db_probe=make_db_probe(engine), key_repo=key_repo)
```

Порядок writer'ов важен и сохраняет нынешнее поведение: JSONL пишется первым, отказ Postgres его не отменяет.

В `make_db_probe` заменить молчаливый `except Exception: return False` на логирующий (G1, гайд 7.2):

```python
        except Exception:  # noqa: BLE001 - a broken probe reports "not ok", never a 500
            log.warning("database probe failed", exc_info=True)
            return False
```

То же самое в `agentgate/api/app.py` в обработчике `/healthz`.

- [ ] **Step 10: Обновить `tests/test_api.py`**

- `build()` в тесте собирает `create_app(settings, gate, writer, decision_repo, profiles, ...)`, где `writer` — `RecordingDecisionWriter` из `tests.factories` либо реальный `CompositeDecisionWriter` поверх фейковых репозиториев, в зависимости от того, что проверяет тест.
- Импорт `from tests.test_pipeline import FakeLLM, profile` заменить на `from tests.factories import FakeLLM, profile` (F16).
- Проверки вида `drepo.rows[0].metadata == {"run_id": "r"}` остаются: фейковый репозиторий теперь получает `Decision`, поэтому читать `drepo.rows[0].request.metadata` или `drepo.rows[0].to_view().metadata`. Выбрать `to_view()` — это форма, в которой строка действительно ложится в базу.
- `drepo.rows[0].decision == "deny"` → `drepo.rows[0].to_view().decision is DecisionKind.deny`.

- [ ] **Step 11: Полный прогон, контракт, коммит**

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: всё зелёное. Тесты `tests/test_stage1_*.py`, `tests/test_normalize_*.py`, `tests/test_stage2_*.py`, `tests/test_profiles.py` не менялись и обязаны пройти без правок.

Run:
```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff --exit-code ../contracts
```
Expected: пустой diff.

```bash
git add service/agentgate/engine service/agentgate/store/writer.py service/agentgate/store/repo.py service/agentgate/api/app.py service/agentgate/__main__.py service/agentgate/config.py service/tests/factories.py service/tests/engine service/tests/store service/tests/test_api.py service/tests/test_config.py
git rm service/agentgate/pipeline.py service/tests/test_pipeline.py
git commit -m "refactor(service): Decision plus DecisionWriter, Gate only orchestrates

Gate.decide returns one immutable Decision and no longer persists
anything; the two persistence paths collapse into DecisionWriter with
Postgres/JSONL/Composite implementations. The allow-cache TTL and the
cache key each live in one place. tests/factories.py replaces the
cross-imports between test modules.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

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

### Task 5: `Policy` — workspace привязывается к сессии

Закрывает: **F4** — единственная находка ревью с последствиями для безопасности. Реализует §6 спеки, которую код сейчас не выполняет.

Рулинг 2 действует: workspace фиксируется первым запросом сессии. Это видимое изменение поведения, поэтому у задачи собственный регрессионный тест — таблица из F4.

**Files:**
- Create: `service/agentgate/domain/policy.py`, `service/tests/domain/test_policy.py`, `service/tests/domain/test_workspace_binding.py`
- Modify: `service/agentgate/profiles/schema.py` (убрать `workspace`, `_expand`, `resolved_*`, `public_dict`), `service/agentgate/profiles/loader.py` (убрать `with_workspace`; `profile_hash` считается при загрузке), `service/agentgate/engine/gate.py`, все модули `service/agentgate/rules/` (сигнатура `evaluate(action, policy)`), `service/agentgate/stage2/prompt.py`, `service/agentgate/api/app.py`
- Modify: `service/tests/test_profiles.py`, `service/tests/rules/*`, `service/tests/factories.py`
- **Contract:** `contracts/openapi.yaml` — из схемы `Profile` исчезает поле `workspace`

**Interfaces:**
- Produces: `agentgate.domain.policy.Policy` — frozen dataclass: `profile: Profile`, `workspace: str`, `allowed_paths: tuple[str, ...]`, `protected_paths: tuple[str, ...]`, `profile_hash: str`; свойства-делегаты `id`, `network`, `protected_branches`, `safe_prefixes`, `escalation`, `prose`; классметод `Policy.bind(profile: Profile, workspace: str) -> Policy`.
- Produces: `agentgate.profiles.loader.LoadedProfile` — `Profile` плюс посчитанный при загрузке `profile_hash`. Проще: `load_profiles` возвращает `dict[str, Profile]` как раньше, а хэш кэшируется на самом `Profile` через `functools.cached_property` — pydantic-модели это поддерживают при `model_config = ConfigDict(ignored_types=(cached_property,))`.
- Changed: `Rule.evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None` — второй параметр меняет тип.

- [ ] **Step 1: Регрессионный тест на таблицу из F4 (падает на текущем коде)**

`service/tests/domain/test_workspace_binding.py`:

```python
"""The workspace a session's policy uses is fixed by the session's first
request, not re-derived from each request's cwd.

Without this, an agent that runs `cd /` and reports the new cwd widens
its own sandbox to the filesystem root: allowed_paths becomes ["/"], and
"outside the workspace" stops existing as a concept.
"""

from agentgate.api.schemas import DecisionKind
from tests.factories import decide_request, gate_for_binding_tests

FIRST_CWD = "/home/u/repo"


async def test_first_request_of_a_session_fixes_the_workspace():
    gate = gate_for_binding_tests()
    await gate.decide(decide_request("ls -la", session_id="s1", args={"cwd": FIRST_CWD}))
    decision = await gate.decide(
        decide_request("rm -rf /home/u/other-project", session_id="s1", args={"cwd": "/"})
    )
    assert decision.verdict.decision is DecisionKind.deny
    assert decision.verdict.rule_id == "hard-deny.destructive"


async def test_a_later_cwd_change_does_not_widen_allowed_paths():
    gate = gate_for_binding_tests()
    await gate.decide(decide_request("ls -la", session_id="s1", args={"cwd": FIRST_CWD}))
    decision = await gate.decide(
        decide_request("cp payload /etc/cron.d/job", session_id="s1", args={"cwd": "/"})
    )
    assert decision.verdict.decision is DecisionKind.deny


async def test_a_sessionless_call_still_uses_its_own_cwd():
    decision = await gate_for_binding_tests().decide(
        decide_request("rm -rf /home/u/other-project", session_id=None, args={"cwd": FIRST_CWD})
    )
    assert decision.verdict.decision is DecisionKind.deny


async def test_a_new_session_picks_up_its_own_first_cwd():
    gate = gate_for_binding_tests()
    await gate.decide(decide_request("ls -la", session_id="s1", args={"cwd": FIRST_CWD}))
    decision = await gate.decide(
        decide_request("ls -la", session_id="s2", args={"cwd": "/tmp/other"})
    )
    assert decision.state.workspace == "/tmp/other"
```

`gate_for_binding_tests()` добавить в `tests/factories.py`: `Gate` с профилем, у которого `allowed_paths: ["${WORKSPACE}", "/tmp/agentgate-scratch"]`, и `FakeLLM`, который всегда отвечает `A` — так любой не-deny случай виден как «упало в ступень 2», а не как случайный отказ.

Run: `cd service && uv run pytest tests/domain/test_workspace_binding.py -v`
Expected: FAIL — первые два теста падают на текущем коде ровно так, как описано в F4 (`ask`/allow вместо `deny`). Это тот самый баг; красный тест — его воспроизведение.

- [ ] **Step 2: Failing-тест для `Policy`**

`service/tests/domain/test_policy.py`:

```python
import dataclasses

import pytest

from agentgate.domain.policy import Policy
from tests.factories import profile


def test_bind_expands_the_workspace_placeholder():
    policy = Policy.bind(profile(allowed_paths=["${WORKSPACE}"]), "/home/u/repo")
    assert policy.allowed_paths == ("/home/u/repo",)


def test_bind_expands_a_tilde_in_protected_paths():
    policy = Policy.bind(profile(protected_paths=["~/.ssh/**"]), "/home/u/repo")
    assert policy.protected_paths[0].startswith("/") and "~" not in policy.protected_paths[0]


def test_resolved_paths_are_immutable():
    policy = Policy.bind(profile(), "/home/u/repo")
    assert isinstance(policy.allowed_paths, tuple)


def test_policy_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Policy.bind(profile(), "/home/u/repo").workspace = "/elsewhere"


def test_hash_does_not_depend_on_the_workspace():
    first = Policy.bind(profile(), "/home/u/repo")
    second = Policy.bind(profile(), "/tmp/other")
    assert first.profile_hash == second.profile_hash


def test_hash_changes_when_the_profile_changes():
    first = Policy.bind(profile(), "/w")
    second = Policy.bind(profile(protected_paths=[".env*", "*.pem"]), "/w")
    assert first.profile_hash != second.profile_hash


def test_network_is_reachable_without_reaching_into_the_profile():
    assert Policy.bind(profile(), "/w").network.allowed_domains == ["pypi.org"]
```

`service/agentgate/domain/policy.py`:

```python
"""A profile bound to one workspace: what the rules and the prompt see.

`Profile` is operator configuration and knows nothing about any request.
`Policy` is that configuration resolved against the workspace of one
session -- placeholders expanded, paths normalized, the hash taken once.
Resolving on every call was both repeated work and the reason a later
`cwd` could silently widen the sandbox.
"""

import os
from dataclasses import dataclass
from functools import cached_property

from agentgate.profiles.schema import Escalation, Network, Profile, Prose


@dataclass(frozen=True)
class Policy:
    profile: Profile
    workspace: str
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    profile_hash: str

    @classmethod
    def bind(cls, profile: Profile, workspace: str) -> "Policy":
        return cls(
            profile=profile,
            workspace=workspace,
            allowed_paths=tuple(
                os.path.normpath(_expand(path, workspace)) for path in profile.allowed_paths
            ),
            protected_paths=tuple(_expand(path, workspace) for path in profile.protected_paths),
            profile_hash=profile.profile_hash(),
        )

    @property
    def id(self) -> str:
        return self.profile.id

    @property
    def network(self) -> Network:
        return self.profile.network

    @property
    def protected_branches(self) -> list[str]:
        return self.profile.protected_branches

    @property
    def safe_prefixes(self) -> list[list[str]]:
        return self.profile.safe_prefixes

    @property
    def escalation(self) -> Escalation:
        return self.profile.escalation

    @property
    def prose(self) -> Prose:
        return self.profile.prose


def _expand(path: str, workspace: str) -> str:
    return os.path.expanduser(path.replace("${WORKSPACE}", workspace))
```

Run: `cd service && uv run pytest tests/domain/test_policy.py -v`
Expected: 7 passed.

- [ ] **Step 3: Убрать `workspace` из `Profile`**

`service/agentgate/profiles/schema.py`:
- Удалить поле `workspace`, методы `_expand`, `resolved_allowed_paths`, `resolved_protected_paths`, `public_dict`.
- `profile_hash()` больше не исключает `workspace` (его нет): `payload = self.model_dump_json()`.
- Кэшировать хэш, чтобы он считался один раз на профиль, а не на запрос:

```python
    model_config = ConfigDict(ignored_types=(cached_property,))

    @cached_property
    def _hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()

    def profile_hash(self) -> str:
        return self._hash
```

`service/agentgate/profiles/loader.py`: удалить `with_workspace`. `detect_workspace` остаётся — им пользуется `Gate` при создании сессии.

- [ ] **Step 4: Правила принимают `Policy`**

Во всех модулях `agentgate/rules/`:
- Сигнатура `evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None`, в том числе в `Rule` (Protocol) и `RuleChain.evaluate`.
- `profile.resolved_allowed_paths()` → `policy.allowed_paths`; `profile.resolved_protected_paths()` → `policy.protected_paths`; `profile.workspace` → `policy.workspace`; `profile.network` → `policy.network`; `profile.safe_prefixes` → `policy.safe_prefixes`; `profile.protected_branches` → `policy.protected_branches`.
- `is_within(p, allowed)` и `matches_any(p, protected, workspace)` принимают списки — передавать `list(policy.allowed_paths)` там, где сигнатура требует list, либо (лучше) расширить сигнатуры `normalize/paths.py` до `Sequence[str]`. Выбрать второе: изменение аннотации без изменения поведения.

`service/agentgate/stage2/prompt.py`: `build_system_prompt(profile)` → `build_system_prompt(policy)`; внутри `profile.workspace` → `policy.workspace`, `profile.network` → `policy.network`, `profile.resolved_protected_paths()` → `policy.protected_paths`, `profile.prose` → `policy.prose`. Формат строк промпта **не меняется** — его проверяют существующие тесты `tests/test_stage2_prompt.py`.

- [ ] **Step 5: `Gate` берёт workspace из сессии**

`service/agentgate/engine/gate.py`, `_resolve`:

```python
    async def _resolve(self, request: DecideRequest, profile_id: str) -> "_Context | Verdict":
        profile = self._profiles.get(profile_id)
        if profile is None:
            return Verdict.ask("api.unknown-profile", f"unknown profile '{profile_id}'", stage=0)
        try:
            model_name, model_config = profile.models.model_config_for(request.model)
        except KeyError:
            return Verdict.ask("api.unknown-model", f"unknown model '{request.model}'", stage=0)

        state = None
        if request.session_id:
            state = await self._states.get_or_create(
                request.session_id, request.harness, profile_id,
                detect_workspace(request.args.cwd),
            )
        workspace = state.workspace if state is not None else detect_workspace(request.args.cwd)
        return _Context(
            policy=Policy.bind(profile, workspace), profile_id=profile_id,
            model_name=model_name, model_config=model_config, state=state,
        )
```

`_Context` теряет поля `profile` и `profile_hash`, получает `policy`. `context.profile_hash` → `context.policy.profile_hash`.

`SessionStateStore.get_or_create` уже принимает `workspace` и возвращает существующее состояние без изменения этого поля — значит workspace первого запроса сохраняется автоматически. Проверить это отдельным тестом (он уже написан в шаге 1: `test_first_request_of_a_session_fixes_the_workspace`).

`Policy.bind` на каждый запрос всё ещё делает `os.path.expanduser` по путям профиля. Это дешевле, чем `detect_workspace` с обходом файловой системы, который теперь вызывается только при создании сессии. Если `tests/rules/test_latency.py` покажет регрессию — кэшировать `Policy` в `SessionState`; пока не усложнять (гайд 1.2).

- [ ] **Step 6: `GET /v1/profiles/{id}` отдаёт сам профиль**

`service/agentgate/api/app.py`:

```python
    @app.get("/v1/profiles/{profile_id}", response_model=Profile, dependencies=[auth])
    async def get_profile(profile_id: str) -> Profile:
        profile = profiles.get(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        return profile
```

`public_dict()` удалён в шаге 3 — модель сама себе представление (F15, гайд 1.3).

- [ ] **Step 7: Обновить тесты профилей и прогнать всё**

`service/tests/test_profiles.py` → `service/tests/profiles/test_loader.py` и `service/tests/profiles/test_schema.py` (гайд 6.3):
- Тесты `test_resolved_allowed_paths`, `test_resolved_allowed_paths_workspace_and_tilde`, `test_resolved_protected_paths_workspace_and_tilde` переезжают в `tests/domain/test_policy.py` и переписываются на `Policy.bind` — предмет проверки тот же, владелец другой.
- `test_profile_hash_ignores_workspace` (строки 65–67, использует `with_workspace`) заменяется на `test_hash_does_not_depend_on_the_workspace` из `tests/domain/test_policy.py` — уже написан.
- Тесты `interpolate_env` и `load_profiles` не меняются, кроме расположения.
- `tests/rules/*`: `with_workspace(Profile.model_validate({...}), WS)` → `Policy.bind(Profile.model_validate({...}), WS)`; вызовы правил передают `policy`. Табличные ожидания не трогаются.
- `tests/factories.py`: добавить `policy(**overrides) -> Policy` и `hard_deny_policy()`.
- `tests/equivalence/test_equivalence.py`: `hard_deny_profile()` → `hard_deny_policy()`.

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: всё зелёное, включая четыре теста из шага 1 (теперь проходят) и корпус эквивалентности (workspace в корпусе один и тот же, поэтому вердикты не меняются).

- [ ] **Step 8: Перегенерировать контракт — здесь diff ожидается**

Run:
```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff ../contracts
```
Expected: непустой diff в `contracts/openapi.yaml` — из схемы `Profile` пропало поле `workspace`. `contracts/decide_request.schema.json` и `decide_response.schema.json` не меняются (проверить глазами: если изменились — что-то поехало в `DecideRequest`/`DecideResponse`, остановиться).

Обоснование изменения для PR: `GET /v1/profiles/{id}` всегда возвращал `workspace: null`, потому что отдаётся базовый профиль, а не привязанный к сессии. Поле исчезает, а не меняет смысл.

- [ ] **Step 9: Обновить спеку и закоммитить**

В `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md` §6 — отметить, что реализовано: workspace определяется из `args.cwd` первого запроса сессии и хранится в `SessionState.workspace`; без `session_id` — из `cwd` текущего запроса.

В `CLAUDE.md` (корень), раздел «Известные ограничения» — удалить пункт про расхождение с §6, если он там есть; добавить в «Зафиксировано в v1» строку про привязку workspace к сессии.

```bash
git add service/agentgate/domain service/agentgate/profiles service/agentgate/rules service/agentgate/engine service/agentgate/stage2/prompt.py service/agentgate/api/app.py service/agentgate/normalize/paths.py service/tests service/../contracts docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md CLAUDE.md
git commit -m "fix(service): bind the policy workspace to the session, not to each cwd

Spec 6 fixes the workspace from the first request of a session; the code
re-derived it from every request's cwd, so an agent reporting cwd=/ after
a cd widened allowed_paths to the filesystem root and 'outside the
workspace' stopped meaning anything. Profile (operator config) and Policy
(profile bound to one workspace, paths resolved once, hash taken once)
are now separate types.

Contract: Profile.workspace disappears from GET /v1/profiles/{id}, where
it was always null.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `Classifier`, `PersistentSessionStateStore`, `bootstrap.py`, типизированные границы

Закрывает: F3 (`SessionStateStore` разорван на две половины), F7 (`Gate` строит `LLMClient` сам), F12 (нетипизированные границы API), F13 (`DecisionRecord` — ручной маппинг в три стороны), F15 (два composition root), G3 (тесты с несколькими утверждениями), гайд 6.1 (три `monkeypatch.setattr`).

**Files:**
- Create: `service/agentgate/classify/__init__.py`, `service/agentgate/classify/base.py`, `service/agentgate/classify/llm.py`, `service/agentgate/session/persistent.py`, `service/agentgate/store/mapper.py`, `service/agentgate/bootstrap.py`, `service/tests/classify/__init__.py`, `service/tests/classify/test_llm.py`, `service/tests/session/__init__.py`, `service/tests/session/test_persistent.py`, `service/tests/test_bootstrap.py`
- Move: `service/agentgate/stage2/` → `service/agentgate/classify/` (`client.py`, `prompt.py`, `schema.py`; `run.py` растворяется в `llm.py`); `service/agentgate/session/state.py` → `service/agentgate/domain/session.py`
- Modify: `service/agentgate/engine/gate.py`, `service/agentgate/api/app.py`, `service/agentgate/api/deps.py`, `service/agentgate/api/schemas.py`, `service/agentgate/store/repo.py`, `service/agentgate/cli.py`, `service/agentgate/__main__.py`
- Move tests: `test_stage2_client.py` → `tests/classify/test_client.py`, `test_stage2_prompt.py` → `tests/classify/test_prompt.py`, `test_stage2_run.py` → `tests/classify/test_llm.py`, `test_session.py` → `tests/session/test_memory.py` + `tests/domain/test_session.py`, `test_store.py` → `tests/store/test_repo.py`, `test_keys.py` → `tests/store/test_keys.py`, `test_deps_keys.py` → `tests/api/test_deps.py`, `test_api.py` → `tests/api/test_app.py`, `test_log.py` → `tests/log/test_jsonl.py`, `test_main.py` → `tests/test_bootstrap.py`

**Interfaces:**
- Produces: `agentgate.classify.base.Classifier` (Protocol): `name: str`, `async def classify(self, action, user_request, policy, stage1_note) -> Verdict`. Никогда не бросает — любая ошибка становится `Verdict.ask(..., stage=2, error=...)`.
- Produces: `agentgate.classify.llm.LLMClassifier(name, model_config, http)` — реализация поверх нынешних `LLMClient` + `build_system_prompt` + `build_user_message`.
- Produces: `agentgate.classify.llm.build_classifiers(profile, http) -> dict[str, Classifier]`.
- Produces: `agentgate.session.persistent.PersistentSessionStateStore(inner, sessions)` — реализация `SessionStateStore` с write-through и `async def restore(self) -> None`.
- Produces: `agentgate.store.mapper.row_from_view`, `view_from_row` — единственное место, знающее про `metadata_`.
- Produces: `agentgate.bootstrap.build_service(settings, *, http=None, writer=None, state_store=None) -> Service` — `Service` — frozen dataclass с полями `app`, `gate`, `writer`, `engine`, `settings`.
- Produces: `agentgate.api.schemas.DecisionsPage`, `agentgate.api.schemas.Health`.

- [ ] **Step 1: `Classifier` — протокол и фейк вместо `MockTransport`**

`service/agentgate/classify/base.py`:

```python
"""Stage 2: what the classifier is, from the engine's point of view.

`classify` never raises. Every failure -- a timeout, a malformed reply, a
bug in the client -- comes back as an `ask` verdict carrying `error`, so
`allow` on a broken classifier is not expressible.
"""

from typing import Protocol

from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction


class Classifier(Protocol):
    name: str

    async def classify(
        self, action: NormalizedAction, user_request: str, policy: Policy, stage1_note: str
    ) -> Verdict: ...
```

В `tests/factories.py` добавить фейк, реализующий этот протокол (гайд 6.1 — фейк вместо подмены транспорта):

```python
class FakeClassifier:
    """A Classifier that answers what it was told to, and counts calls."""

    def __init__(self, verdict: Verdict | None = None, name: str = "m") -> None:
        self.name = name
        self.calls = 0
        self._verdict = verdict or Verdict(
            decision=DecisionKind.allow, stage=2, model=name, raw_response={"choices": []}
        )

    async def classify(self, action, user_request, policy, stage1_note) -> Verdict:
        self.calls += 1
        return self._verdict
```

`FakeLLM` (транспорт) остаётся только в `tests/classify/test_client.py`, где предметом проверки и является HTTP-клиент.

- [ ] **Step 2: `LLMClassifier` — `run_stage2` становится методом**

`service/agentgate/classify/llm.py` — объединяет нынешние `stage2/run.py` и конструирование `LLMClient`:

```python
class LLMClassifier:
    def __init__(self, name: str, model_config: ModelConfig, http: httpx.AsyncClient) -> None:
        self.name = name
        self._client = LLMClient(name, model_config, http)

    async def classify(self, action, user_request, policy, stage1_note) -> Verdict:
        system = build_system_prompt(policy)
        user = build_user_message(action, user_request, stage1_note)
        try:
            output, raw = await self._client.classify(system, user)
        except Stage2Error as exc:
            return self._unavailable(exc.kind, f"classifier unavailable: {exc.kind}")
        except Exception as exc:  # noqa: BLE001 - fail closed on anything
            log.warning("classifier raised an unexpected error", exc_info=True)
            return self._unavailable(
                "unexpected", f"classifier unavailable: unexpected ({type(exc).__name__})"
            )
        return self._verdict_from(output, raw)


def build_classifiers(profile: Profile, http: httpx.AsyncClient) -> dict[str, Classifier]:
    """One classifier per model the profile declares, built once at startup."""
    return {
        name: LLMClassifier(name, config, http)
        for name, config in profile.models.configs.items()
    }
```

`_verdict_from` и `_unavailable` — приватные методы, тела переносятся из `run_stage2` без изменений. Модули `stage2/client.py`, `stage2/prompt.py`, `stage2/schema.py` переезжают в `classify/` как есть (`git mv`), с правкой импортов.

`service/tests/classify/test_llm.py` — перенос `tests/test_stage2_run.py`; вызовы `run_stage2(action, task, P, "m", client, note)` → `LLMClassifier("m", config, http).classify(action, task, policy, note)`. Все ассерты на `res.decision/.model/.error/.raw_response/.reason/.suggest` остаются буквально: `Verdict` даёт те же имена.

- [ ] **Step 3: `Gate` получает реестр классификаторов**

`service/agentgate/engine/gate.py`:
- `__init__(self, profiles, classifiers, rules, state_store, allow_cache_ttl_seconds)`. `http` уходит — его держит классификатор (F7, гайд 2.1).
- `classifiers: Mapping[str, Mapping[str, Classifier]]` — по профилю, потом по имени модели, потому что конфиг модели живёт в профиле.
- Проверка «unknown model» перестаёт быть `try/except KeyError` вокруг `model_config_for` и становится отсутствием ключа:

```python
        classifier = self._classifiers[profile_id].get(request.model or profile.models.default)
        if classifier is None:
            return Verdict.ask("api.unknown-model", f"unknown model '{request.model}'", stage=0)
```

- `_evaluate` вызывает `await context.classifier.classify(action, request.user_request, context.policy, STAGE1_PASSED)`.

`tests/engine/test_gate.py` переходит с `FakeLLM` + `MockTransport` на `FakeClassifier` — исчезает единственная причина, по которой тест пайплайна знал про httpx.

- [ ] **Step 4: `PersistentSessionStateStore`**

`service/tests/session/test_persistent.py`:

```python
async def test_get_or_create_returns_the_restored_state():
    sessions = FakeSessionRepo(states=[state("s1", deny_total=5)])
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.restore()
    assert (await store.get_or_create("s1", "t", "default", "/w")).deny_total == 5


async def test_save_writes_through_to_the_repository():
    sessions = FakeSessionRepo()
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.save(state("s1"))
    assert sessions.upserts == ["s1"]


async def test_restore_reloads_the_valid_allow_cache():
    sessions = FakeSessionRepo(cache=[("s1", "k", "d1", _in_an_hour())])
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.restore()
    assert await store.cache_get("s1", "k") == "d1"


async def test_restore_ignores_an_expired_cache_row():
    sessions = FakeSessionRepo(cache=[("s1", "k", "d1", _an_hour_ago())])
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.restore()
    assert await store.cache_get("s1", "k") is None


async def test_cache_put_writes_through():
    sessions = FakeSessionRepo()
    store = PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.cache_put("s1", "k", "d1", 60)
    assert len(sessions.cache_puts) == 1
```

`service/agentgate/session/persistent.py`:

```python
"""In-memory session state with write-through to Postgres.

The spec keeps counters and the allow cache in memory behind
SessionStateStore, writes them through after every decision, and restores
them at startup. That was three pieces of glue in two modules; it is one
implementation of the protocol here, which is also what makes a Redis
store a single new class.
"""

from datetime import datetime, timezone

from agentgate.domain.session import SessionState, SessionStateStore


class PersistentSessionStateStore:
    def __init__(self, inner: SessionStateStore, sessions) -> None:
        self._inner = inner
        self._sessions = sessions

    async def restore(self) -> None:
        self._inner.preload(await self._sessions.load_all())
        now = datetime.now(timezone.utc)
        for session_id, action_hash, decision_id, expires_at in await self._sessions.cache_load_valid():
            ttl_seconds = int((expires_at - now).total_seconds())
            if ttl_seconds > 0:
                await self._inner.cache_put(session_id, action_hash, decision_id, ttl_seconds)

    async def get_or_create(self, session_id, harness, profile_id, workspace) -> SessionState:
        return await self._inner.get_or_create(session_id, harness, profile_id, workspace)

    async def save(self, state: SessionState) -> None:
        await self._inner.save(state)
        await self._sessions.upsert(state)

    async def cache_get(self, session_id: str, key: str) -> str | None:
        return await self._inner.cache_get(session_id, key)

    async def cache_put(self, session_id: str, key: str, decision_id: str, ttl_seconds: int) -> None:
        await self._inner.cache_put(session_id, key, decision_id, ttl_seconds)
```

Заметить: `save` и `cache_put` теперь пишут в Postgres сами, поэтому `PostgresDecisionWriter` перестаёт делать `sessions.upsert` и `sessions.cache_put` — иначе запись случится дважды. Перенести обязанность: writer пишет **только** строку решения, состояние сессии пишет store. FK-порядок сохраняется, потому что `Gate._settle_session` вызывает `save` до того, как решение уходит в writer. Обновить `tests/store/test_writer.py`: тесты `test_postgres_writer_upserts_...`, `test_postgres_writer_caches_an_allow` и соседние переезжают в `tests/session/test_persistent.py`, а у writer остаётся один тест — «вставляет строку решения».

**Проверить порядок явным тестом** в `tests/api/test_app.py`: сессия должна быть записана до строки решения, иначе `IntegrityError` по FK. Тест с реальной БД (`requires_db`), который делает один `POST /v1/decide` в новой сессии и убеждается, что строка решения появилась.

- [ ] **Step 5: `DecisionRecord` уходит, остаётся один маппер**

`service/agentgate/store/mapper.py`:

```python
"""The only place that knows a decision row calls its metadata column
`metadata_` -- SQLAlchemy reserves `metadata` on the declarative base.
"""

from agentgate.engine.decision import DecisionView
from agentgate.store.models import DecisionRow


def row_from_view(view: DecisionView) -> DecisionRow:
    data = view.model_dump(exclude={"decision_id"})
    data["metadata_"] = data.pop("metadata")
    return DecisionRow(**data)


def view_from_row(row: DecisionRow) -> DecisionView:
    return DecisionView.model_validate(
        {c.name: getattr(row, c.name) for c in DecisionRow.__table__.columns}
        | {"metadata": row.metadata_}
    )
```

`service/agentgate/store/repo.py`: удалить `DecisionRecord` целиком (`to_row`, `from_row`, `to_dict`). `DecisionRepo.insert(decision: Decision)` использует `row_from_view(decision.to_view())`; `DecisionRepo.list(...) -> list[DecisionView]` использует `view_from_row`. Докстринги про FK-порядок остаются, ссылки на «Task 10»/«Task 11» вычищаются (гайд 5.3).

`tests/store/test_repo.py` (перенос `test_store.py`): `rec(**over)` строит `Decision` через `tests.factories`; `test_decision_record_to_dict` переезжает в `tests/engine/test_decision.py` (уже написан как `test_view_exposes_both_id_and_decision_id` и соседние) и здесь удаляется.

- [ ] **Step 6: Типизированные границы API**

`service/agentgate/api/schemas.py` — добавить:

```python
class DecisionsPage(BaseModel):
    items: list[DecisionView]
    next_before: str | None = None


class Health(BaseModel):
    status: str
    db: bool
    llm: bool | None = None
```

`service/agentgate/api/app.py`:

```python
    @app.get("/v1/decisions", response_model=DecisionsPage, dependencies=[auth])
    async def decisions(
        session_id: str | None = None,
        model: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        before: str | None = None,
    ) -> DecisionsPage:
        rows = await decision_repo.list(session_id=session_id, model=model, limit=limit, before=before)
        next_before = rows[-1].id if len(rows) == limit else None
        return DecisionsPage(items=rows, next_before=next_before)
```

`dict(r.to_dict(), decision_id=r.id)` исчезает из обоих мест (F13): `DecisionView` уже несёт оба ключа.

`create_app` — типизировать параметры (F12):

```python
def create_app(
    settings: Settings,
    gate: Gate,
    writer: DecisionWriter,
    decision_repo: DecisionRepo,
    profiles: Mapping[str, Profile],
    db_probe: Callable[[], Awaitable[bool]] | None = None,
    key_repo: ApiKeyRepo | None = None,
) -> FastAPI:
```

Шесть параметров вместо восьми и все с типами. `decision_repo=None`/`session_repo=None` ветки удаляются — приложение без хранилища не собирается ни в одном сценарии, а мёртвая ветка `if decision_repo is None: return {"items": []}` маскировала бы ошибку сборки.

`service/agentgate/api/deps.py`: `key_repo: ApiKeyRepo | None`, `now_fn: Callable[[], float]`, `background: BackgroundTasks` без `# type: ignore` — FastAPI всегда инжектит настоящий экземпляр, поэтому `= None` заменить на честный `BackgroundTasks` без дефолта. Если это ломает прямой вызов `require_token` в `tests/api/test_deps.py`, тест передаёт `BackgroundTasks()` явно.

- [ ] **Step 7: `bootstrap.py` — единственный composition root**

`service/agentgate/bootstrap.py`:

```python
"""Where the service is assembled.

Everything above this module depends on protocols; this is the one place
that knows which implementations are used in production. Tests and the
CLI call it with substitutes rather than assembling their own variants.
"""

from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from agentgate.api.app import create_app
from agentgate.classify.llm import build_classifiers
from agentgate.config import Settings
from agentgate.engine.gate import Gate
from agentgate.log.jsonl import JsonlLogger
from agentgate.profiles.loader import load_profiles
from agentgate.rules.chain import STAGE1
from agentgate.session.memory import InMemorySessionStateStore
from agentgate.session.persistent import PersistentSessionStateStore
from agentgate.store.db import make_engine, make_session_factory
from agentgate.store.keys import ApiKeyRepo
from agentgate.store.repo import DecisionRepo, SessionRepo
from agentgate.store.writer import CompositeDecisionWriter, JsonlDecisionWriter, PostgresDecisionWriter


@dataclass(frozen=True)
class Service:
    app: FastAPI
    gate: Gate
    engine: AsyncEngine
    settings: Settings


async def build_service(
    settings: Settings,
    *,
    http: httpx.AsyncClient | None = None,
    state_store=None,
    writer=None,
) -> Service:
    """Assemble the service. Every collaborator can be substituted, so a
    test never has to reproduce this wiring to change one piece of it.

    Raises SystemExit if the configured default profile does not exist,
    and whatever `validate_token_for_bind` raises for an unsafe
    token/bind combination -- both are startup failures.
    """
    settings.validate_token_for_bind()
    profiles = load_profiles(settings.profiles_dir)
    if settings.default_profile not in profiles:
        raise SystemExit(
            f"default profile '{settings.default_profile}' not found in {settings.profiles_dir}"
        )

    engine = make_engine(settings.db_url)
    session_factory = make_session_factory(engine)
    decisions, sessions = DecisionRepo(session_factory), SessionRepo(session_factory)

    store = state_store or PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    if hasattr(store, "restore"):
        await store.restore()

    http = http or httpx.AsyncClient()
    classifiers = {name: build_classifiers(profile, http) for name, profile in profiles.items()}
    gate = Gate(profiles, classifiers, STAGE1, store, settings.allow_cache_ttl_seconds)

    writer = writer or CompositeDecisionWriter([
        JsonlDecisionWriter(JsonlLogger(settings.log_path)),
        PostgresDecisionWriter(decisions),
    ])
    app = create_app(
        settings, gate, writer, decisions, profiles,
        db_probe=_make_db_probe(engine), key_repo=ApiKeyRepo(session_factory),
    )
    return Service(app=app, gate=gate, engine=engine, settings=settings)
```

`service/agentgate/__main__.py` сокращается до разбора аргументов и `uvicorn.run`:

```python
def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "keys":
        from agentgate.cli import run_keys_cli

        sys.exit(run_keys_cli(sys.argv[2:]))

    logging.basicConfig(level=logging.INFO)
    service = asyncio.run(build_service(get_settings()))
    uvicorn.run(service.app, host=service.settings.bind_host, port=service.settings.bind_port)
```

`service/agentgate/cli.py`: `_build_repo` удаляется, CLI берёт движок из `build_service`… но CLI не должен поднимать весь сервис ради выпуска ключа. Вместо этого вынести в `bootstrap.py` вторую, меньшую функцию:

```python
def build_key_repo(settings: Settings) -> tuple[ApiKeyRepo, AsyncEngine]:
    """The store the keys CLI needs, without assembling the HTTP service."""
    engine = make_engine(settings.db_url)
    return ApiKeyRepo(make_session_factory(engine)), engine
```

Знание «как построить движок и фабрику сессий» остаётся в одном модуле (F15, гайд 1.3), а CLI не тянет за собой профили и HTTP-клиент.

- [ ] **Step 8: Убрать три `monkeypatch.setattr` (гайд 6.1)**

- `tests/test_main.py:77` подменял `InMemorySessionStateStore` в модуле, чтобы проверить восстановление состояния. Теперь `build_service(settings, state_store=CapturingStore(...))` — подстановка через параметр. Файл переезжает в `tests/test_bootstrap.py`.
- `tests/test_cli_keys.py:176-179` подменял `build_app`, `uvicorn.run` и `sys.argv`. `sys.argv` — граница процесса, её подмена допустима (гайд 7.1), но `run_keys_cli(["create", "--label", "smoke"])` вызывается напрямую с аргументами, и подменять `sys.argv` больше не нужно. Проверку «ветка keys не поднимает сервер» переписать на утверждение о том, что `run_keys_cli` не обращается к профилям и HTTP: достаточно вызвать его с фейковым репозиторием ключей и убедиться в результате.
- `tests/test_session.py:61-74` подменял `time.monotonic`. Ввести часы как зависимость: `InMemorySessionStateStore(now=time.monotonic)`, тест передаёт `FakeClock` из `tests/factories.py`:

```python
class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds
```

Оба TTL-теста (`..._roundtrip_and_cache_ttl`, `..._cache_ttl_exact_boundary_expires`) переписать на `FakeClock`, разделив первый на два (гайд 6.5): «состояние переживает round-trip» и «запись истекает по TTL» — разные факты.

Проверка:
```bash
cd service && grep -rn "monkeypatch.setattr" tests/
```
Expected: пусто. `monkeypatch.setenv`/`delenv` остаются — это граница окружения.

- [ ] **Step 9: Прогон, контракт, коммит**

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: всё зелёное, включая e2e (`tests/e2e/test_e2e.py` — обновить сборку приложения на `build_service`).

Run:
```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py && git diff ../contracts
```
Expected: `decide_request`/`decide_response` без изменений. `openapi.yaml` может измениться, если ручная схема `/v1/decisions` расходится с `DecisionsPage` — **это и есть проверка**: расхождение означает, что модель написана неверно (контракт — источник истины), а не что yaml устарел. Привести модель к yaml, не наоборот.

```bash
git add service/agentgate service/tests service/../contracts
git commit -m "refactor(service): four protocols and one composition root

Classifier replaces Gate's inline LLMClient construction and the
model-lookup KeyError; PersistentSessionStateStore absorbs the
write-through and restore glue that lived in app.py and __main__.py;
DecisionRecord's three hand-written mappers collapse into store/mapper.py
over DecisionView; bootstrap.build_service is the only place that names
production implementations. API boundaries are typed and the three
monkeypatch.setattr call sites become injected fakes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `CommandSpec` — одна таблица знаний о командах

Закрывает: F9 (три ответа на «какие пути трогает команда»), F10 (одиннадцать множеств в пяти файлах).

Самый рискованный шаг: он трогает ядро безопасности целиком. Идёт последним из технических и опирается на корпус эквивалентности из задачи 4. **Если сроки поджимают — этот шаг откладывается**, остальные семь самодостаточны.

**Files:**
- Create: `service/agentgate/shell/commands.py`, `service/tests/shell/test_commands.py`
- Modify: `service/agentgate/normalize/shell.py` (`PATH_COMMANDS`, `_collect_paths`), `service/agentgate/rules/hard_deny/shared.py`, `service/agentgate/rules/allowlist.py`, `service/agentgate/rules/profile_paths.py`, `service/agentgate/rules/argv_paths.py`
- Delete: `service/agentgate/rules/argv_paths.py` (растворяется в таблице)

**Interfaces:**
- Produces: `agentgate.shell.commands.CommandSpec` — frozen dataclass: `name: str`, `roles: frozenset[Role]`, `value_flags: frozenset[str]`, `upload_flags: frozenset[str]`, `write_target: WriteTarget`, `path_arguments: PathArguments`.
- Produces: `Role` (Enum): `READONLY`, `MUTATING`, `NETWORK`, `DOWNLOADER`, `INTERPRETER`, `SHELL`, `FIREWALL`, `WRAPPER`, `WRITE`.
- Produces: `COMMANDS: Mapping[str, CommandSpec]` и `spec_for(executable: str) -> CommandSpec` (для неизвестной команды — нейтральная спецификация, а не `KeyError`).
- Produces: `commands_with_role(role: Role) -> frozenset[str]` — через неё выражаются нынешние одиннадцать множеств.

- [ ] **Step 1: Свести существующие множества в таблицу — без изменения поведения**

Порядок: сначала таблица описывает ровно то, что уже есть, и старые множества **выводятся** из неё, а не удаляются. Так корпус эквивалентности обязан оставаться зелёным на каждом шаге.

`service/agentgate/shell/commands.py` — таблица со строкой на команду:

```python
COMMANDS: dict[str, CommandSpec] = {
    "curl": CommandSpec(
        name="curl",
        roles=frozenset({Role.NETWORK, Role.DOWNLOADER}),
        value_flags=frozenset({"-o", "--output", "-T", "--upload-file", ...}),
        upload_flags=frozenset({"-d", "--data", "--data-binary", "-T", "--upload-file", ...}),
        write_target=WriteTarget.NONE,
        path_arguments=PathArguments.FLAG_VALUES,
    ),
    "rm": CommandSpec(
        name="rm", roles=frozenset({Role.MUTATING}), value_flags=frozenset(),
        upload_flags=frozenset(), write_target=WriteTarget.EVERY_POSITIONAL,
        path_arguments=PathArguments.EVERY_POSITIONAL,
    ),
    "cp": CommandSpec(..., write_target=WriteTarget.LAST_POSITIONAL, ...),
    "tee": CommandSpec(..., write_target=WriteTarget.EVERY_POSITIONAL, ...),
    "sed": CommandSpec(..., write_target=WriteTarget.POSITIONALS_AFTER_FIRST, ...),
    ...
}
```

и производные:

```python
def commands_with_role(role: Role) -> frozenset[str]:
    return frozenset(name for name, spec in COMMANDS.items() if role in spec.roles)
```

Затем в старых местах заменить литералы на запросы:
- `hard_deny/shared.py`: `NETWORK_COMMANDS = commands_with_role(Role.NETWORK)`, `DOWNLOADERS = commands_with_role(Role.DOWNLOADER)`, `SHELLS`, `INTERPRETERS`, `WRITE_COMMANDS`, `FIREWALL` — тем же способом.
- `rules/allowlist.py`: `READONLY = commands_with_role(Role.READONLY)`.
- `rules/profile_paths.py`: `MUTATING = commands_with_role(Role.MUTATING)`.
- `normalize/shell.py`: `PATH_COMMANDS = commands_with_role(...)` по соответствующей роли.

**Тест на равенство старому составу** — написать до замены, пока оба существуют:

```python
@pytest.mark.parametrize("role,legacy", [
    (Role.NETWORK, LEGACY_NETWORK_COMMANDS),
    (Role.READONLY, LEGACY_READONLY),
    (Role.MUTATING, LEGACY_MUTATING),
    ...
], ids=["network", "readonly", "mutating", ...])
def test_table_reproduces_the_legacy_set(role, legacy):
    assert commands_with_role(role) == frozenset(legacy)
```

`LEGACY_*` — копии нынешних множеств, вписанные в тест литералами. Тест удаляется вместе с последним старым множеством; до тех пор он доказывает, что таблица ничего не потеряла.

Run: `cd service && uv run pytest tests/shell/test_commands.py tests/equivalence -q`

- [ ] **Step 2: Один ответ на «какие пути трогает команда»**

Свести три функции в одну, опирающуюся на таблицу:

```python
def command_paths(command: SimpleCommand, cwd: str, role: PathRole) -> tuple[str, ...]:
    """Paths this command references in the given role.

    READ, WRITE and ANY are different questions, and the three functions
    that used to answer them disagreed -- which was already a bug once
    (a readonly command outside PATH_COMMANDS reading a bare-name
    protected file was invisible to the allowlist guard).
    """
```

Заменять по одному вызывающему, прогоняя корпус после каждого:
1. `rules/argv_paths.command_argv_paths` → `command_paths(..., PathRole.ANY)`; модуль удаляется.
2. `hard_deny/shared.command_paths` (нынешний `_cmd_paths`) → та же функция с `PathRole.ANY`, плюс редиректы и stdin, которые теперь описаны в таблице как часть роли.
3. `normalize/shell._collect_paths` → таблица; find-специфика (`-delete`, `-exec`, narrowing predicates) переезжает в `CommandSpec` для `find`.

Run после каждого: `cd service && uv run pytest tests/equivalence tests/rules tests/normalize -q`
Expected: зелено. Красный корпус — откатить конкретную замену.

- [ ] **Step 3: Тест «добавить команду — одна строка»**

```python
def test_a_new_command_is_one_row():
    spec = CommandSpec(
        name="shred", roles=frozenset({Role.MUTATING}), value_flags=frozenset(),
        upload_flags=frozenset(), write_target=WriteTarget.EVERY_POSITIONAL,
        path_arguments=PathArguments.EVERY_POSITIONAL,
    )
    assert spec.name in {**COMMANDS, spec.name: spec}
    assert Role.MUTATING in spec_for("shred").roles


def test_an_unknown_command_gets_a_neutral_spec():
    spec = spec_for("some-tool-we-have-never-seen")
    assert spec.roles == frozenset()
    assert spec.write_target is WriteTarget.NONE
```

Второй тест важнее первого: неизвестная команда не должна ни падать, ни получать привилегий.

- [ ] **Step 4: Прогон, удаление корпуса, коммит**

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: всё зелёное.

Корпус эквивалентности выполнил свою работу и удаляется — он фиксирует реализацию, а не поведение, и оставлять его значит заморозить внутренности (гайд 6.2):

```bash
cd service && git rm -r tests/equivalence
```

```bash
git add service/agentgate/shell/commands.py service/agentgate/normalize service/agentgate/rules service/tests/shell
git rm service/agentgate/rules/argv_paths.py
git rm -r service/tests/equivalence
git commit -m "refactor(service): one CommandSpec table instead of eleven command sets

PATH_COMMANDS, MUTATING, WRITE_COMMANDS, READONLY, GIT_READONLY,
NETWORK_COMMANDS, DOWNLOADERS, INTERPRETERS, SHELLS, FIREWALL and the
wrapper set become queries against one table, and the three disagreeing
answers to 'which paths does this command touch' become one. Adding a
command is a row.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: OpenAPI из приложения

Закрывает: F14 в части `scripts/export_openapi.py` (886 строк, из них ≈600 — проза в константах).

**Процессное условие (рулинг 3):** задача меняет `contracts/openapi.yaml`, поэтому её мердж требует PR с упоминанием всех трёх направлений (`contracts/README.md`). Технически она ни от чего не зависит и может уехать отдельно.

**Files:**
- Modify: `service/scripts/export_openapi.py` (≈886 → ≈60 строк), `service/agentgate/api/schemas.py` (`description=` и `examples=` на полях), `service/agentgate/api/app.py` (`summary`, `description`, `responses` на маршрутах)
- Test: `service/tests/test_contracts.py`

- [ ] **Step 1: Тест, требующий, чтобы документ порождался приложением**

`service/tests/test_contracts.py` — добавить:

```python
def test_openapi_yaml_matches_what_the_app_generates(tmp_path):
    from agentgate.api.app import create_app

    generated = _generate_openapi()
    committed = yaml.safe_load(Path("../contracts/openapi.yaml").read_text(encoding="utf-8"))
    assert generated == committed


def test_no_endpoint_is_marked_provisional():
    committed = yaml.safe_load(Path("../contracts/openapi.yaml").read_text(encoding="utf-8"))
    assert "provisional" not in yaml.safe_dump(committed).lower()
```

Второй тест закрывает замечание из `contracts/README.md`: генератор до сих пор помечает реализованные эндпоинты как `provisional`.

- [ ] **Step 2: Описания переезжают на модели**

Перенести прозу из строковых констант `export_openapi.py` в `Field(description=...)` на полях `DecideRequest`, `DecideResponse`, `LatencyMs`, `DecisionView`, `DecisionsPage`, `Health`, `Profile` и в `summary`/`description` декораторов маршрутов. Знание о том, что означает поле, переезжает к самому полю (гайд 1.3) — теперь одна правка вместо двух.

Примеры запросов (`REQUEST_EXAMPLES`) переезжают в `model_config = {"json_schema_extra": {"examples": [...]}}` на `DecideRequest`.

- [ ] **Step 3: Скрипт сокращается**

`service/scripts/export_openapi.py` целиком:

```python
"""Export the OpenAPI document the service actually serves.

Descriptions and examples live on the models and the routes; this script
only renders them, so the document cannot drift from the code.
"""

import asyncio
from pathlib import Path

import yaml

from agentgate.bootstrap import build_service
from agentgate.config import Settings

OUTPUT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"


def main() -> None:
    settings = Settings(db_url="postgresql+asyncpg://export:export@localhost/export")
    service = asyncio.run(build_service(settings, state_store=_NullStore(), writer=_NullWriter()))
    document = service.app.openapi()
    OUTPUT.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=True), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
```

`build_service` при экспорте не должен требовать живой Postgres — `make_engine` соединение не открывает, а `state_store`/`writer` подставляются заглушками. Если `build_service` всё же обращается к БД (через `restore()`), передать `state_store=InMemorySessionStateStore()`, у которого нет `restore`.

- [ ] **Step 4: Сверить документ и закоммитить**

Run:
```bash
cd service && uv run python scripts/export_openapi.py && git diff ../contracts/openapi.yaml
```
Expected: осмысленный diff. Разобрать его целиком: каждое расхождение — это либо описание, потерянное при переносе (вернуть), либо ручная неточность старого генератора (исправление, записать в отчёт). Пути, коды ответов и схемы обязаны совпасть с тем, что было.

Run: `cd service && uv run pytest tests/test_contracts.py -v`
Expected: все зелёные.

```bash
git add service/scripts/export_openapi.py service/agentgate/api contracts/openapi.yaml service/tests/test_contracts.py
git commit -m "refactor(contracts): generate openapi.yaml from the app

Field and route descriptions move onto the models and the decorators, so
the document renders from the code that serves it instead of from 600
lines of prose constants. Endpoints that are implemented no longer
describe themselves as provisional.

Touches contracts/ -- see contracts/README.md: needs sign-off from the
harness, service and benchmark tracks.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Документация и отчёт

**Files:**
- Modify: `CLAUDE.md`, `service/CLAUDE.md`, `service/README.md`
- Create: `docs/reports/task-v1.5-solid-refactor.md`

- [ ] **Step 1: Обновить `CLAUDE.md`**

Раздел «Что построено» — заменить описание каскада на архитектуру v1.5: одна `RuleChain`, четыре протокола, `bootstrap.py`. Раздел «Папки и кто в них пишет» — добавить `domain/`, `rules/`, `classify/`, `shell/`, `engine/`.

Раздел «Зафиксировано в v1» — добавить:
- Workspace фиксируется первым запросом сессии (§6 спеки).
- Неразобранное действие закрывается ступенью 1 правилом `unparseable`.
- Один тип `Verdict` от правила до строки в Postgres.

Раздел «Известные ограничения» — вычеркнуть то, что закрыто; оставить: атрибуция `key_id`, per-process кэш ключей, v2–v4, конфликт fail-open/fail-closed с контрактом адаптера.

Раздел «Правила работы» — добавить: «Новое правило ступени 1 — новый класс в `agentgate/rules/` и строка в `STAGE1`; `Gate` не меняется».

- [ ] **Step 2: Обновить `service/CLAUDE.md` и `service/README.md`**

`service/CLAUDE.md` — карта модулей v1.5, где что лежит, куда добавлять правило/классификатор/writer/store.

`service/README.md` — раздел «Как добавить»: правило ступени 1, модель, хранилище сессий. Три примера по 5–10 строк каждый: они и есть демонстрация масштабируемости для судей.

- [ ] **Step 3: Отчёт**

`docs/reports/task-v1.5-solid-refactor.md` на русском: что построено по задачам, доказательства TDD (какой тест падал до реализации), какие находки F1–F16 закрыты и чем, два санкционированных изменения поведения с их регрессионными тестами, расхождения корпуса эквивалентности и как разобраны, что отложено.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md service/CLAUDE.md service/README.md docs/reports/task-v1.5-solid-refactor.md
git commit -m "docs: v1.5 architecture in CLAUDE.md and the refactor report

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-Review

Проверка плана против спеки (`docs/reports/code-quality-review-and-refactor-plan.md`), выполнена после написания.

**1. Покрытие находок.**

| Находка | Задача | Находка | Задача |
|---|---|---|---|
| F1 | 1, 2 | F9 (секреты) | 4 |
| F2 | 2 | F9 (пути) | 7 |
| F3 | 6 | F10 | 7 |
| F4 | 5 | F11 | 4 |
| F5 | 3 | F12 | 6 |
| F6 | 3 | F13 | 6 |
| F7 | 6 | F14 (hard_deny) | 3 |
| F8 | 4 | F14 (shell) | 4 |
| F15 | 6 | F14 (openapi) | 8 |
| F16 | 2 | L1 | 1 |
| L3 | 2 | G1 | 1, 2, 4 |
| G2 | 3, 4 | G3 | 6 |

**L2 (идемпотентность) не реализуется** — ревью само относит её к контракту адаптера, а не к рефакторингу. `Verdict` + `DecisionWriter` делают её добавление локальным, что и было целью.

**2. Плейсхолдеры.** Проверено: нет «TBD», «реализовать позже», «аналогично задаче N». Механические переносы (задачи 3, 7) описаны как «перенести функцию X в модуль Y, изменив Z» с перечислением всех имён — это исполнимая инструкция, а не заглушка. Полные тела шести правил hard-deny в план не выписаны сознательно: они переносятся дословно, и копия в плане была бы вторым источником истины для 900 строк кода, который обязан остаться байт-в-байт (гайд 1.3). Вместо этого дана проверка полноты переноса (`grep` по процессным комментариям) и корпус эквивалентности.

**3. Согласованность типов.** `Verdict` (задача 1) → используется задачами 2, 3, 6. `Decision`/`DecisionView` (задача 2) → задачи 6, 8. `Rule.evaluate(action, profile)` в задаче 3 становится `evaluate(action, policy)` в задаче 5 — переход назван явно в обеих. `Gate.__init__` меняется трижды (задачи 2, 3, 6) — каждая сигнатура выписана целиком. `PostgresDecisionWriter` теряет работу с сессиями в задаче 6 — сказано явно, вместе с переносом его тестов.

**4. Риски, зафиксированные планом.**

- Задача 3 может задеть бюджет latency (цепочка объектов вместо функций). Проверяется существующим тестом; порог не ослабляется.
- Задача 4, объединение двух списков секретов, может изменить вердикты. Корпус ловит; каждое расхождение разбирается поимённо, молчаливое обновление baseline запрещено.
- Задача 5 меняет поведение намеренно; регрессионный тест написан до реализации и падает на текущем коде — это воспроизведение бага, а не подгонка.
- Задача 7 — самая рискованная и последняя; откладывается целиком, если сроки поджимают.

---

## Execution Handoff

План сохранён в `docs/superpowers/service/plans/2026-09-04-agentgate-v1.5-solid-refactor.md`.

Порядок задач — это и порядок ценности: после задач 1–3 ступень 1 читается как список правил и её можно показывать; задача 5 обязательна до любого внешнего запуска (единственная находка с последствиями для безопасности); задачи 7 и 8 отделимы.
