"""The dialogue that preceded a proposed action, as stage 2 gets to see it.

Pure data, no I/O. Two facts about it matter to the rest of the service:

- `digest()` is taken over the five fields of each turn (role, author,
  content, tool, call_id) exactly as the harness sent them, before any
  truncation, and enters the allow-cache key. Two identical actions with
  different histories therefore never share a cached `allow`. Growing
  `Turn` with a new field does not change the digest unless that field is
  added here too, on purpose.
- `fit()` is what reaches the prompt: capped per role, oldest turns dropped
  first, the newest turn never dropped. It runs only when stage 2 runs.

Stage 1 never receives this type. That is enforced by the rule signature,
not by convention: `Rule.evaluate(action, policy)` has no parameter for it.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from agentgate.api.schemas import Author, Turn, TurnRole
from agentgate.profiles.schema import History

OMITTED_MARKER = "…[{n} chars omitted]…"


@dataclass(frozen=True)
class Dialogue:
    turns: tuple[Turn, ...] = ()
    omitted: int = 0

    @classmethod
    def of(cls, turns: Sequence[Turn]) -> "Dialogue":
        return cls(tuple(turns))

    @property
    def is_empty(self) -> bool:
        return not self.turns

    def digest(self) -> str:
        payload = json.dumps(
            [[turn.role.value, turn.author.value, turn.content, turn.tool, turn.call_id] for turn in self.turns],
            ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()

    def last_human_request(self) -> str | None:
        for turn in reversed(self.turns):
            if turn.role is TurnRole.human and turn.author is Author.human:
                return turn.content
        return None

    def fit(self, budget: History) -> "Dialogue":
        """The part of this dialogue that fits the prompt budget.

        Each turn is first capped by its role's limit; then the oldest turns
        are dropped whole until the total content fits `budget_chars`. The
        newest turn is never dropped: if it alone exceeds the budget, it
        stays and the budget is simply exhausted.
        """
        kept = [_cap(turn, budget.cap_for(turn.role.value)) for turn in self.turns]
        # Each turn is rendered once and its length kept alongside it: measuring
        # the whole remainder per dropped turn is quadratic, and a maximum
        # history drops most of its turns.
        sizes = [_content_chars(turn) for turn in kept]
        total = sum(sizes)
        omitted = 0
        while len(kept) > 1 and total > budget.budget_chars:
            kept.pop(0)
            total -= sizes.pop(0)
            omitted += 1
        return Dialogue(tuple(kept), self.omitted + omitted)


def _content_chars(turn: Turn) -> int:
    # What the prompt emits, not the raw text: json.dumps expands control
    # characters six-fold, and tool output is where they cluster.
    return len(json.dumps(turn.content, ensure_ascii=False))


def _cap(turn: Turn, limit: int) -> Turn:
    if len(turn.content) <= limit:
        return turn
    if turn.role is TurnRole.human:
        return turn.model_copy(update={"content": turn.content[-limit:]})
    return turn.model_copy(update={"content": _head_and_tail(turn.content, limit)})


def _head_and_tail(content: str, limit: int) -> str:
    # The marker alone can exceed `limit` when `limit < len(marker)`: `keep`
    # floors at 0, and the bare marker is returned even though it is longer
    # than the cap (and possibly longer than `content` itself). Deliberate;
    # see test_fit_cap_smaller_than_the_marker_keeps_only_the_marker.
    marker_room = len(OMITTED_MARKER.format(n=len(content)))
    keep = max(limit - marker_room, 0)
    head, tail = keep // 2, keep - keep // 2
    omitted = len(content) - keep
    marker = OMITTED_MARKER.format(n=omitted)
    return content[:head] + marker + content[len(content) - tail:]
