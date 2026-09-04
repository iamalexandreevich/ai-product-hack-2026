"""The rules that can never be overridden.

A hard deny is final: escalation does not replace it and no later stage
turns it into anything else. Because of that asymmetry each rule stays
deliberately narrow -- it must fire on the input its name promises and
never on ordinary, unrelated work. Where a rule recognizes the shape of
something dangerous but cannot determine its target, it asks (hard=False)
rather than deny what it has not earned or stay silent on what it does
not know.

Order is the order they run in: the first verdict wins.
WrapperUnresolvedRule closes the set -- it fires only when none of the six
above could have evaluated the command at all.
"""

from agentgate.rules.hard_deny.destructive import DestructiveRule
from agentgate.rules.hard_deny.exfil import ExfilRule
from agentgate.rules.hard_deny.git_force import GitForceRule
from agentgate.rules.hard_deny.pipe_exec import PipeExecRule
from agentgate.rules.hard_deny.privilege import PrivilegeRule
from agentgate.rules.hard_deny.protected_write import ProtectedWriteRule
from agentgate.rules.hard_deny.wrapper_unresolved import WrapperUnresolvedRule

HARD_DENY_RULES = [
    ExfilRule(),
    PipeExecRule(),
    DestructiveRule(),
    ProtectedWriteRule(),
    PrivilegeRule(),
    GitForceRule(),
    WrapperUnresolvedRule(),
]

__all__ = [
    "HARD_DENY_RULES", "DestructiveRule", "ExfilRule", "GitForceRule", "PipeExecRule",
    "PrivilegeRule", "ProtectedWriteRule", "WrapperUnresolvedRule",
]
