"""In-memory implementation of SessionStateStore, for tests and local dev.

TTL for the allow-decision cache is measured on a monotonic clock rather than
wall-clock time, so it is immune to clock adjustments (NTP, DST, manual
changes). The clock is a constructor argument so a test can move it by hand
instead of patching the module.
"""

import time
from collections.abc import Callable

from agentgate.domain.session import SessionState


class InMemorySessionStateStore:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._states: dict[str, SessionState] = {}
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}

    async def get_or_create(
        self, session_id: str, harness: str, profile_id: str, workspace: Callable[[], str]
    ) -> SessionState:
        state = self._states.get(session_id)
        # An empty `harness` is the sentinel `SessionRepo.ensure` writes for a
        # session an inspect call created without ever deciding anything --
        # `DecideRequest.harness` requires at least one character, so a real
        # decide never produces one. A restart preloads that row like any
        # other (see `PersistentSessionStateStore.restore`), and without this
        # check the first decide for that session would inherit whatever
        # `cwd` the inspect call happened to carry as its workspace pin,
        # instead of establishing its own.
        if state is None or not state.harness:
            state = SessionState(
                session_id=session_id, harness=harness, profile_id=profile_id,
                workspace=workspace(),
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
        if self._now() >= expires:
            del self._cache[(session_id, key)]
            return None
        return decision_id

    async def cache_put(self, session_id: str, key: str, decision_id: str, ttl_seconds: int) -> None:
        self._cache[(session_id, key)] = (decision_id, self._now() + ttl_seconds)

    def preload(self, states: list[SessionState]) -> None:
        for state in states:
            self._states[state.session_id] = state
