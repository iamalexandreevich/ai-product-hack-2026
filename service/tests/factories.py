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
