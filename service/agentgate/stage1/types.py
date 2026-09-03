from collections.abc import Callable
from dataclasses import dataclass

from agentgate.api.schemas import DecisionKind
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile


@dataclass(frozen=True)
class Stage1Decision:
    decision: DecisionKind
    rule_id: str
    reason: str
    suggest: str = ""
    hard: bool = False


Check = Callable[[NormalizedAction, Profile], Stage1Decision | None]
