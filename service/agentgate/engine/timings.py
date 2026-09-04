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
