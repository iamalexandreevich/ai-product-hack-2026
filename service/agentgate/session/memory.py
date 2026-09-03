"""In-memory implementation of SessionStateStore, for tests and local dev.

TTL for the allow-decision cache uses time.monotonic() rather than wall-clock
time, so it is immune to clock adjustments (NTP, DST, manual changes).
"""

import time

from agentgate.session.state import SessionState


class InMemorySessionStateStore:
    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}

    async def get_or_create(
        self, session_id: str, harness: str, profile_id: str, workspace: str
    ) -> SessionState:
        state = self._states.get(session_id)
        if state is None:
            state = SessionState(
                session_id=session_id, harness=harness, profile_id=profile_id, workspace=workspace
            )
            self._states[session_id] = state
        return state

    async def save(self, state: SessionState) -> None:
        self._states[state.session_id] = state

    async def cache_get(self, session_id: str, key: str) -> str | None:
        item = self._cache.get((session_id, key))
        if item is None:
            return None
        decision_id, expires = item
        if time.monotonic() >= expires:
            del self._cache[(session_id, key)]
            return None
        return decision_id

    async def cache_put(self, session_id: str, key: str, decision_id: str, ttl_seconds: int) -> None:
        self._cache[(session_id, key)] = (decision_id, time.monotonic() + ttl_seconds)

    def preload(self, states: list[SessionState]) -> None:
        for s in states:
            self._states[s.session_id] = s
