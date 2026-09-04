"""Shared builders for tests. Test modules import from here, never from
each other -- renaming a test module must not break three others.
"""

from collections.abc import Callable
from datetime import datetime, timezone

from agentgate.api.schemas import DecideRequest, DecisionKind, Turn
from agentgate.classify.base import Classifier, ReviewCase
from agentgate.domain.dialogue import Dialogue
from agentgate.domain.policy import Policy
from agentgate.domain.session import SessionState, SessionStateStore
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision, DecisionRecord
from agentgate.engine.gate import Gate
from agentgate.engine.timings import Latency
from agentgate.normalize import normalize
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.rules.chain import STAGE1
from agentgate.session.memory import InMemorySessionStateStore

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


def minimal_profile_data(**overrides) -> dict:
    """The smallest profile that validates, as the plain dict the loader
    tests write to a YAML file.
    """
    data = {
        "id": "t",
        "allowed_paths": ["${WORKSPACE}"],
        "protected_paths": [".env*"],
        "network": {"mode": "allowlist", "allowed_domains": ["pypi.org"]},
        "models": {"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "qwen"}}},
    }
    data.update(overrides)
    return data


def policy(**overrides) -> Policy:
    """``profile()`` bound to the test workspace -- what a rule actually sees."""
    return Policy.bind(profile(**overrides), WORKSPACE)


def stage1_policy(**overrides) -> Policy:
    """The policy the stage 1 chain runs against in tests: a profile bound to
    the test workspace, since rules read resolved paths and an unbound
    profile would measure nothing real.
    """
    data = {
        "id": "t",
        "allowed_paths": ["${WORKSPACE}", "/tmp/agentgate-scratch"],
        "protected_paths": [".env*", ".git/hooks/**"],
        "network": {"mode": "allowlist", "allowed_domains": ["pypi.org", "github.com"]},
        "safe_prefixes": [["npm", "test"], ["pytest"]],
        "models": {"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "q"}}},
    }
    data.update(overrides)
    return Policy.bind(Profile.model_validate(data), WORKSPACE)


def hard_deny_policy(**overrides) -> Policy:
    """The policy the hard-deny table tests run against: its protected
    paths and branches are what those tables assert on.
    """
    data = {
        "id": "t",
        "allowed_paths": ["${WORKSPACE}", "/tmp/agentgate-scratch"],
        "protected_paths": [".env*", ".git/hooks/**", ".claude/**", "AGENTS.md", "~/.ssh/**", "~/.aws/**"],
        "protected_branches": ["main", "release/*"],
        "network": {"mode": "allowlist", "allowed_domains": ["pypi.org"]},
        "models": {"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "q"}}},
    }
    data.update(overrides)
    return Policy.bind(Profile.model_validate(data), WORKSPACE)


class FakeClassifier:
    """A Classifier that answers what it was told to, and keeps what it was asked."""

    def __init__(self, verdict: Verdict | None = None, name: str = "m") -> None:
        self.name = name
        self.calls = 0
        self.cases: list[ReviewCase] = []
        self._verdict = verdict or Verdict(
            decision=DecisionKind.allow, stage=2, model=name, raw_response={"choices": []}
        )

    async def classify(self, case: ReviewCase) -> Verdict:
        self.calls += 1
        self.cases.append(case)
        return self._verdict


def stage2_verdict(letter: str, reason: str = "", suggest: str = "", name: str = "m") -> Verdict:
    """What an LLMClassifier returns for one of its three answers."""
    kinds = {"A": DecisionKind.allow, "D": DecisionKind.deny, "U": DecisionKind.ask}
    return Verdict(
        decision=kinds[letter], stage=2, reason=reason, suggest=suggest,
        model=name, raw_response={"choices": []},
    )


def unavailable_verdict(error: str = "http", name: str = "m") -> Verdict:
    """What an LLMClassifier returns when it could not reach a verdict at all."""
    return Verdict(
        decision=DecisionKind.ask, stage=2, reason=f"classifier unavailable: {error}",
        model=name, error=error,
    )


def classifiers(*fakes: Classifier) -> dict[str, dict[str, Classifier]]:
    """The registry Gate takes, for the single "default" profile built here."""
    return {"default": {fake.name: fake for fake in fakes}}


def gate(
    classifier: Classifier | None = None,
    state_store: SessionStateStore | None = None,
    **profile_overrides,
) -> Gate:
    """A Gate over one profile whose only model is the given classifier."""
    classifier = classifier if classifier is not None else FakeClassifier()
    return Gate(
        profiles={"default": profile(**profile_overrides)},
        default_profile="default",
        classifiers=classifiers(classifier),
        rules=STAGE1,
        state_store=state_store if state_store is not None else InMemorySessionStateStore(),
        allow_cache_ttl_seconds=86400,
    )


def gate_for_binding_tests(state_store: SessionStateStore | None = None) -> Gate:
    """A Gate whose classifier always allows, so a case that reaches stage 2
    is visible as "stage 1 said nothing" rather than as an accidental refusal.
    """
    return gate(state_store=state_store, allowed_paths=["${WORKSPACE}", "/tmp/agentgate-scratch"])


def shell_action(raw: str, cwd: str = WORKSPACE) -> NormalizedAction:
    """What a shell command normalizes to -- the only input a rule sees."""
    return normalize(
        DecideRequest(harness="t", tool="shell", raw=raw, args={"cwd": cwd}, user_request="x")
    )


def unparseable_action(cwd: str = WORKSPACE) -> NormalizedAction:
    """An action whose command bashlex could not parse: a single
    unterminated quote leaves commands, paths and domains empty.
    """
    return shell_action('echo "unterminated', cwd)


def decide_request(raw: str, session_id: str | None = "s1", **overrides) -> DecideRequest:
    data = dict(
        session_id=session_id, harness="t", tool="shell", raw=raw,
        args={"cwd": WORKSPACE}, user_request="task",
    )
    data.update(overrides)
    return DecideRequest.model_validate(data)


def turn(role: str = "human", author: str = "human", content: str = "fix the build", **overrides) -> Turn:
    data = dict(role=role, author=author, content=content)
    data.update(overrides)
    return Turn.model_validate(data)


def dialogue(*turns: Turn) -> Dialogue:
    return Dialogue.of(turns)


def session_state(session_id: str = "s1", **overrides) -> SessionState:
    data = dict(session_id=session_id, harness="t", profile_id="default", workspace="/w")
    data.update(overrides)
    return SessionState(**data)


def decision(**overrides) -> Decision:
    data = dict(
        id="01J0", ts=datetime.now(timezone.utc), request=decide_request("ls -la"),
        verdict=Verdict.allow("allowlist.readonly"),
        latency=Latency(total_ms=1), profile_id="default", profile_hash="h" * 64,
    )
    data.update(overrides)
    return Decision(**data)


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeSessionRecords:
    """The persisted half of a session, in memory.

    Serves both roles the real ``SessionRepo`` plays: what
    ``PersistentSessionStateStore`` restores from and writes through to, and
    what ``PostgresDecisionWriter`` puts allow-cache rows into.
    """

    def __init__(
        self,
        states: list[SessionState] | None = None,
        cache: list[tuple[str, str, str, datetime]] | None = None,
        upsert_error: Exception | None = None,
    ) -> None:
        self._states = list(states or [])
        self._cache = list(cache or [])
        self._upsert_error = upsert_error
        self.upserts: list[str] = []
        self.cache_puts: list[tuple[str, str, str]] = []

    async def load_all(self) -> list[SessionState]:
        return list(self._states)

    async def cache_load_valid(self) -> list[tuple[str, str, str, datetime]]:
        return list(self._cache)

    async def upsert(self, state: SessionState) -> None:
        if self._upsert_error is not None:
            raise self._upsert_error
        self.upserts.append(state.session_id)

    async def cache_put(self, session_id, action_hash, decision_id, expires_at) -> None:
        self.cache_puts.append((session_id, action_hash, decision_id))


class CountingWorkspaceStore:
    """A session store that counts how often the workspace detector actually ran.

    It wraps the producer the gate hands to ``get_or_create`` rather than the
    detector itself, so the count is of detections the store asked for -- which
    is the whole contract: a workspace is produced when a session is created,
    not on every request of an existing one.
    """

    def __init__(self, inner: SessionStateStore | None = None) -> None:
        self._inner = inner if inner is not None else InMemorySessionStateStore()
        self.detections = 0

    async def get_or_create(
        self, session_id: str, harness: str, profile_id: str, workspace: Callable[[], str]
    ) -> SessionState:
        def counted() -> str:
            self.detections += 1
            return workspace()

        return await self._inner.get_or_create(session_id, harness, profile_id, counted)

    async def save(self, state: SessionState) -> None:
        await self._inner.save(state)

    async def cache_get(self, session_id: str, key: str) -> str | None:
        return await self._inner.cache_get(session_id, key)

    async def cache_put(
        self, session_id: str, key: str, decision_id: str, ttl_seconds: int
    ) -> None:
        await self._inner.cache_put(session_id, key, decision_id, ttl_seconds)


class FakeReplayRecords:
    """The keyed decisions a replay store restores from, in memory."""

    def __init__(self, records: list[DecisionRecord] | None = None, error: Exception | None = None) -> None:
        self._records = list(records or [])
        self._error = error
        self.cutoffs: list[datetime] = []

    async def load_replayable(self, newer_than: datetime) -> list[DecisionRecord]:
        if self._error is not None:
            raise self._error
        self.cutoffs.append(newer_than)
        return [r for r in self._records if r.ts > newer_than]


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
