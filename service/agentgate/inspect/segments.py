"""What the inspect classifier sees of a tool result: numbered windows
around the findings, never the whole output.

`Segment` is one contiguous run of lines in `output.split("\\n")`
coordinates, `Segments` the ordered, non-overlapping set the prompt renders
and the span validator checks against: a span the model returns is only
applied when every line of it was inside a segment it saw. `build` (added
by the segmentation task) is the only producer; the dataclasses are here
so the prompt and the validator can depend on the shape without it.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from agentgate.inspect.detectors import Finding
from agentgate.profiles.schema import ModelBudget


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Segments:
    items: tuple[Segment, ...] = ()
    omitted_segments: int = 0
    omitted_lines: int = 0

    def covers(self, start: int, end: int) -> bool:
        """True when every line in ``[start, end]`` lies in some segment."""
        return all(any(s.start <= n <= s.end for s in self.items) for n in range(start, end + 1))

    @property
    def chars(self) -> int:
        return sum(len(line) + 1 for s in self.items for line in s.lines)


def build(lines: Sequence[str], findings: Sequence[Finding], budget: ModelBudget) -> Segments:
    """Windows of `budget.window_lines` around each finding, merged where
    they touch, cut to `segment_max_lines`, then taken in line order until
    `max_segments` or `max_chars` runs out. With no findings the head of
    the output is the one window: an instruction is most often at the
    start of a file or a page, so the head outranks the tail.
    """
    if not lines:
        return Segments()
    windows = _merge(_windows(lines, findings, budget.window_lines))
    chunks = [chunk for window in windows for chunk in _cut(window, budget.segment_max_lines)]
    return _fit(lines, chunks, budget)


def _windows(lines: Sequence[str], findings: Sequence[Finding], radius: int) -> list[tuple[int, int]]:
    last = len(lines) - 1
    if not findings:
        return [(0, last)]
    return sorted((max(0, f.line - radius), min(last, f.last + radius)) for f in findings)


def _merge(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _cut(window: tuple[int, int], max_lines: int) -> list[tuple[int, int]]:
    start, end = window
    return [(s, min(end, s + max_lines - 1)) for s in range(start, end + 1, max_lines)]


def _fit(lines: Sequence[str], chunks: list[tuple[int, int]], budget: ModelBudget) -> Segments:
    items: list[Segment] = []
    chars = 0
    omitted_segments = omitted_lines = 0
    for start, end in chunks:
        if len(items) >= budget.max_segments:
            omitted_segments += 1
            omitted_lines += end - start + 1
            continue
        kept: list[str] = []
        for number in range(start, end + 1):
            cost = len(lines[number]) + 1
            if chars + cost > budget.max_chars:
                break
            kept.append(lines[number])
            chars += cost
        if not kept:
            omitted_segments += 1
            omitted_lines += end - start + 1
            continue
        items.append(Segment(start=start, end=start + len(kept) - 1, lines=tuple(kept)))
        omitted_lines += (end - start + 1) - len(kept)
    return Segments(items=tuple(items), omitted_segments=omitted_segments, omitted_lines=omitted_lines)
