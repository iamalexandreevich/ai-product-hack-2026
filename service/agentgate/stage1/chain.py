"""Stage 1 cascade: the deterministic checks run before the stage-2 LLM.

Fixed order — hard-deny first, so it always wins over any later
allow/ask/deny that a lower-priority check would otherwise produce (see
service/CLAUDE.md: hard-deny is never overridden). The first non-None
result short-circuits the chain.
"""

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.stage1.allowlist import check_allowlist
from agentgate.stage1.hard_deny import check_hard_deny
from agentgate.stage1.packages import check_packages
from agentgate.stage1.profile_check import check_profile
from agentgate.stage1.types import Check

CHECKS: list[Check] = [check_hard_deny, check_profile, check_allowlist, check_packages]


def run_stage1(action: NormalizedAction, profile: Profile) -> Verdict | None:
    for check in CHECKS:
        decision = check(action, profile)
        if decision is not None:
            return decision
    return None
