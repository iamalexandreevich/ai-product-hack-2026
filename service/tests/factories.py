"""Shared builders for tests. Test modules import from here, never from
each other -- renaming a test module must not break three others.
"""

import json

import httpx

from agentgate.api.schemas import DecideRequest
from agentgate.domain.policy import Policy
from agentgate.engine.decision import Decision
from agentgate.engine.gate import Gate
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


def gate_for_binding_tests() -> Gate:
    """A Gate whose classifier always allows, so a case that reaches stage 2
    is visible as "stage 1 said nothing" rather than as an accidental refusal.
    """
    return Gate(
        profiles={"default": profile(allowed_paths=["${WORKSPACE}", "/tmp/agentgate-scratch"])},
        default_profile="default",
        rules=STAGE1,
        state_store=InMemorySessionStateStore(),
        http=httpx.AsyncClient(transport=httpx.MockTransport(FakeLLM("A"))),
    )


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


class FakeLLM:
    """An httpx MockTransport handler standing in for the OpenAI-compatible
    endpoint. Until a Classifier protocol exists, the transport is the only
    seam available to fake the model.
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
