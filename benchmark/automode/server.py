"""The AgentGate server as an automode implementation.

This is the only production adapter, and it is a move, not a rewrite: the session
strategy and the request metadata used to sit in ``runner/executor.py``, where they made
the runner depend on how our server behaves. They belong here — a session id exists
because of *the service's* allow cache and escalation counters, which is server
semantics, not benchmark semantics.

The HTTP contract stays entirely in ``client/security_service.py``. This module owns no
transport of its own, post-processes nothing, and does not own the client's lifecycle
either: ``cli.py`` opens ``async with SecurityServiceClient(...)`` and hands the
reference here.
"""

from __future__ import annotations

from typing import Any

from automode.base import AutomodeExecutionResult
from client.security_service import SecurityServiceClient
from schemas.case import BenchmarkCase

BENCHMARK_NAME = "agentgate-benchmark-v1"


class ServerAutomodeAdapter:
    """Runs one benchmark case as one ``POST /v1/decide`` call against our service."""

    name = "server"

    def __init__(
        self,
        client: SecurityServiceClient,
        *,
        session_mode: str = "per_case",
        benchmark_name: str = BENCHMARK_NAME,
        send_history: bool = True,
    ) -> None:
        self.client = client
        self.session_mode = session_mode
        self.benchmark_name = benchmark_name
        self.send_history = send_history

    async def execute(self, case: BenchmarkCase, *, run_id: str) -> AutomodeExecutionResult:
        """Send the case to the service and return its answer, unmodified."""
        history = case.history if self.send_history else []
        response = await self.client.evaluate(
            case.human_req,
            case.assistant_tool_call,
            session_id=_session_id(case, run_id, self.session_mode),
            metadata=self._metadata(case, run_id),
            history=history,
        )
        return AutomodeExecutionResult(response=response, history_turns_sent=len(history))

    def _metadata(self, case: BenchmarkCase, run_id: str) -> dict[str, Any]:
        return {"benchmark": self.benchmark_name, "run_id": run_id, "case_id": case.id}


def _session_id(case: BenchmarkCase, run_id: str, session_mode: str) -> str | None:
    """Session strategy.

    ``per_case`` (default) gives every case a fresh session, so the service's allow cache
    and its escalation counters (three consecutive denies force an ``ask``, spec 5.4)
    cannot leak between cases and distort the measurement. ``shared`` deliberately keeps
    one session for the whole run to exercise that escalation logic; ``none`` omits
    ``session_id`` entirely, which the contract allows.
    """
    match session_mode:
        case "per_case":
            return f"bench-{run_id}-{case.id}"
        case "shared":
            return f"bench-{run_id}"
        case "none":
            return None
        case _:
            raise ValueError(f"unknown session_mode {session_mode!r}")
