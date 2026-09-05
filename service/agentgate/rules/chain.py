"""Stage 1 in one place: the order the rules run in.

Hard-deny first, so it always wins over any later allow or ask.
UnparseableRule opens the chain -- nothing below it can evaluate an action
bashlex could not parse. The user's own rules sit at three points:
`client.deny` right after hard-deny, `client.ask` after the profile's own
denials, and `client.allow` right before the server allowlist -- so a user
can forbid more but cannot permit what hard-deny or the profile forbids.

`ProfileMcpRule` runs twice, mirroring `ClientRulesRule`.
`ProfileMcpRule("refuse")` stands with the profile's other denials, above
the user's `ask` floor, so an operator's `deny` on an MCP tool cannot be
softened by it. `ProfileMcpRule("allow")` sits right after
`ClientRulesRule("allow")` and before the server allowlist, so an
operator's `allow` on an MCP tool is settled as `ask` at stage 1 when the
user asked to confirm it -- an allow never outruns the user's floor,
whether it is the operator's or the user's own.

`McpReadonlyRule` sits right after the allowlist: it is a naming
convention, so it yields to everything the operator and the user wrote by
hand. `ProfileDomainTrustedRule` sits right after `McpReadonlyRule`: it is
the allowlist extended to network reads, so everything the allowlist
already permitted never reaches it, and everything stricter -- hard-deny,
the user's denial, the profile's denials, the user's floor -- has already
run.
"""

from agentgate.rules.allowlist import AllowlistRule
from agentgate.rules.base import RuleChain
from agentgate.rules.client_rules import ClientRulesRule
from agentgate.rules.hard_deny import HARD_DENY_RULES
from agentgate.rules.hard_deny.wrapper_unresolved import WrapperUnresolvedRule
from agentgate.rules.mcp_readonly import McpReadonlyRule
from agentgate.rules.packages import PackagesRule
from agentgate.rules.profile_domain_trusted import ProfileDomainTrustedRule
from agentgate.rules.profile_domains import ProfileDomainRule
from agentgate.rules.profile_mcp import ProfileMcpRule
from agentgate.rules.profile_paths import ProfilePathRule
from agentgate.rules.unparseable import UnparseableRule

STAGE1 = RuleChain([
    UnparseableRule(),
    *HARD_DENY_RULES,
    WrapperUnresolvedRule(),
    ClientRulesRule("deny"),
    ProfilePathRule(),
    ProfileDomainRule(),
    ProfileMcpRule("refuse"),
    ClientRulesRule("ask"),
    ClientRulesRule("allow"),
    ProfileMcpRule("allow"),
    AllowlistRule(),
    McpReadonlyRule(),
    ProfileDomainTrustedRule(),
    PackagesRule(),
])
