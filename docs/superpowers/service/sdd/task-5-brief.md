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

**Инвариант, который нельзя потерять при переписывании `_resolve` (регресс, найденный ревью задачи 2).**
Ранний отказ `api.unknown-model` означает, что профиль **разрешён успешно** и не устроила только модель, —
значит в записанном решении обязан стоять настоящий `profile_hash` этого профиля, а не пустая строка.
До рефакторинга так и было (`pipeline.py` передавал `base_profile.profile_hash()`); образец кода в задаче 2
ошибочно передавал `""` для обеих веток раннего отказа, и это чинится отдельным коммитом.
Правильная форма в `decide()` — не терять хэш там, где профиль есть:

```python
        resolved = await self._resolve(request, profile_id)
        if isinstance(resolved, Verdict):
            profile = self._profiles.get(profile_id)
            profile_hash = profile.profile_hash() if profile is not None else ""
            return self._finish(decision_id, request, resolved, timings, profile_id, profile_hash)
```

Для `api.unknown-profile` хэш пуст по существу — профиля нет. Для `api.unknown-model` он настоящий.
Регрессионный тест обязателен: `test_unknown_model_still_records_the_profile_hash`.


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

