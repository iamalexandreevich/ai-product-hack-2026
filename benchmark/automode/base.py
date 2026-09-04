"""The seam between the benchmark framework and one automode implementation.

The benchmark measures *an automode* — something that stands between a coding agent and
the operating system and decides what may run. Our own AgentGate server is one such
implementation; it is the only one that exists today. Everything the framework owns —
the dataset, the deterministic scorer, the aggregate metrics, storage and reporting —
is about the case and the outcome, not about how the outcome was obtained. This module
is where that split is made explicit, so a second implementation can be added without
touching the runner, the scorer or the metrics.

What this seam does **not** solve, stated plainly so nobody plans against it:
``BenchmarkResult`` still assumes one decision per case (``human_decision_count`` is
``int(result is ask)``, ``attack_success`` means "the automode permitted an action the
case forbids" rather than "harm actually occurred", ``task_success`` is ``allow``), and
``evaluator/metrics.py`` reads exactly those properties. An implementation that runs a
whole agent session to completion will still need work there. The envelope narrows that
future change to ``BenchmarkResult``, ``execute_case`` and the scoring path; it does not
remove it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from schemas.case import BenchmarkCase
from schemas.result import ServiceResponse


class AutomodeExecutionResult(BaseModel):
    """Normalised result of executing one benchmark case through one automode.

    A thin envelope around the outcome, and deliberately nothing more. It exists because
    the *return type of the protocol* is the most expensive thing to change later —
    every implementation and every test references it — while ``ServiceResponse`` is
    specifically the shape of one ``POST /v1/decide`` answer: it is ``extra="forbid"``,
    it is produced only by ``client/security_service.py::normalize_response``, and its
    single required field constrains the outcome to ``allow | deny | ask | error``. An
    implementation that drives a whole agent session over many turns has no single value
    of that kind. Returning the envelope keeps that future outside the signature.

    ``response`` is required *today* because the only production implementation always
    has exactly one decision. The day an implementation has no single AgentGate-style
    decision, this field becomes optional and whole-task fields join it — both are
    additive changes that touch neither the protocol signature nor
    ``ServerAutomodeAdapter``. Until such an implementation exists the fields are not
    guessed here: they are written against the real thing, by whoever writes it.
    """

    model_config = ConfigDict(extra="forbid")

    response: ServiceResponse


@runtime_checkable
class AutomodeAdapter(Protocol):
    """One automode implementation under test.

    ``name`` identifies the implementation in stored results and reports. A plain
    ``str``, not an enum, so adding an implementation needs no change to this module.

    ``execute`` runs **one benchmark case**, not one request: an implementation is free
    to spend a whole agent session with many turns and many tool calls inside a single
    call, and nothing in this contract may assume one HTTP request per case. It may
    raise — ``runner.executor.execute_case`` turns any exception into an ``error``
    result, so a single broken case never aborts a run.

    An adapter obtains an outcome and nothing else. It does not score, does not
    aggregate, and never touches storage or reporting: those are shared across
    implementations by construction, which is the entire point of the seam. There are
    deliberately no lifecycle methods here either — the owner of a resource (the HTTP
    client, say) is the composition root in ``cli.py``, and nothing today needs setup,
    teardown or a health check to go through this protocol.
    """

    name: str

    async def execute(self, case: BenchmarkCase, *, run_id: str) -> AutomodeExecutionResult: ...
