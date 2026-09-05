"""What the inspect classifier sees of a tool result: numbered windows
around the findings, never the whole output.

`Segment` is one contiguous run of lines in `output.split("\\n")`
coordinates, `Segments` the ordered, non-overlapping set the prompt renders
and the span validator checks against: a span the model returns is only
applied when every line of it was inside a segment it saw. `build` (added
by the segmentation task) is the only producer; the dataclasses are here
so the prompt and the validator can depend on the shape without it.
"""

from dataclasses import dataclass


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
