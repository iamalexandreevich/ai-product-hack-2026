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

