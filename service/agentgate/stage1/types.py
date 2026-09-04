from collections.abc import Callable

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile

Check = Callable[[NormalizedAction, Profile], Verdict | None]
