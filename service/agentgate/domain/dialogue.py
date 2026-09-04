"""The dialogue that preceded a proposed action, as stage 2 gets to see it.

Pure data, no I/O. Two facts about it matter to the rest of the service:

- `digest()` is taken over the turns exactly as the harness sent them,
  before any truncation, and enters the allow-cache key. Two identical
  actions with different histories therefore never share a cached `allow`.
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
            [turn.model_dump(mode="json") for turn in self.turns],
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def last_human_request(self) -> str | None:
        for turn in reversed(self.turns):
            if turn.role is TurnRole.human and turn.author is Author.human:
                return turn.content
        return None

    def fit(self, budget: History) -> "Dialogue":
        raise NotImplementedError
