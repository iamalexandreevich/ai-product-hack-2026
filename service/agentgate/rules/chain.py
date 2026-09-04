"""Stage 1 in one place: the order the rules run in.

Hard-deny first, so it always wins over any later allow or ask.
UnparseableRule opens the chain -- nothing below it can evaluate an action
bashlex could not parse.
"""

from agentgate.rules.allowlist import AllowlistRule
from agentgate.rules.base import RuleChain
from agentgate.rules.hard_deny import HARD_DENY_RULES
from agentgate.rules.hard_deny.wrapper_unresolved import WrapperUnresolvedRule
from agentgate.rules.packages import PackagesRule
from agentgate.rules.profile_domains import ProfileDomainRule
from agentgate.rules.profile_paths import ProfilePathRule
from agentgate.rules.unparseable import UnparseableRule

STAGE1 = RuleChain([
    UnparseableRule(),
    *HARD_DENY_RULES,
    WrapperUnresolvedRule(),
    ProfilePathRule(),
    ProfileDomainRule(),
    AllowlistRule(),
    PackagesRule(),
])
