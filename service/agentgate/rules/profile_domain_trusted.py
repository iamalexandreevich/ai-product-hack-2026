"""The one positive verdict a listed domain can carry.

`network.allowed_domains` stays what it has always been: a gate that
forbids. A domain in the list means "going there is not forbidden", not
"this command is safe" -- `curl -X DELETE https://github.com/...` is a
listed domain and a destructive act.

So the positive case is a rule of its own, off unless the operator turns
it on (`network.trusted_allows`), and it answers `allow` only when the
single reason the action did not pass the server allowlist is that it
touches the network. That is stated as a list of conditions rather than a
sprinkle of network inside `allowlist.py`, because a list can be read, can
be covered by a table of tests, and keeps the network policy in one file.

Every condition must hold; failing any of them, the rule says nothing and
the action goes on to stage 2. This rule never denies -- denying an
unlisted domain is `ProfileDomainRule`'s job, upstream in the chain.

Conditions 5-7 and 9 are the ones that make the list worth writing:
`curl … | sh` is closed by hard-deny long before this rule runs, but the
rule refuses it on its own so its correctness does not depend on the order
of the chain; `curl -o file` and `curl … > file` are closed by 7 and 5.
"""

from agentgate.api.schemas import Tool
from agentgate.domain.domains import domain_allowed
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction, SimpleCommand
from agentgate.normalize.paths import is_within, matches_any
from agentgate.profiles.schema import NetworkMode
from agentgate.rules.allowlist import _is_readonly, _matches_prefix
from agentgate.shell.argv import ParsedArgv
from agentgate.shell.commands import Role, spec_for
from agentgate.shell.paths import PathRole, command_paths, writes_a_file

# Modes in which a listed domain is a considered choice of the operator's.
# `off` forbids the network as a class; `open` has no gate at all, so the
# list influences nothing there and reading a promise into it would be
# inventing an intention the operator never expressed.
_TRUSTING_MODES = (NetworkMode.allowlist, NetworkMode.ask)

# Roles that make a command more than a read: it changes something, runs
# something, or carries someone else's privileges.
_FORBIDDEN_ROLES = frozenset({
    Role.MUTATING, Role.INTERPRETER, Role.SHELL,
    Role.ESCALATOR, Role.FIREWALL, Role.WRAPPER, Role.STDIN_FORWARDER,
})


class ProfileDomainTrustedRule:
    id = "profile.domain-trusted"
    hard = False

    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
        if not self._network_trusts(action, policy):
            return None
        if not self._shape_is_readable(action):
            return None
        if not all(self._command_is_a_read(c, policy) for c in action.commands):
            return None
        if not self._paths_are_safe(action, policy):
            return None
        return Verdict.allow(self.id)

    def _network_trusts(self, action: NormalizedAction, policy: Policy) -> bool:
        """Conditions 1 and 4."""
        network = policy.network
        if not network.trusted_allows or network.mode not in _TRUSTING_MODES:
            return False
        if not action.domains:
            return False
        return all(domain_allowed(d, network.allowed_domains) for d in action.domains)

    def _shape_is_readable(self, action: NormalizedAction) -> bool:
        """Conditions 2 and 3."""
        if action.tool is not Tool.shell or not action.commands:
            return False
        flags = action.flags
        return not (flags.unparseable or flags.has_eval or flags.has_subst)

    def _command_is_a_read(self, command: SimpleCommand, policy: Policy) -> bool:
        """Conditions 5-10, for one command."""
        if writes_a_file(command):
            return False
        spec = spec_for(command.argv[0])
        option_names = {o.name for o in ParsedArgv.of(command.argv, spec.value_flags).options}
        if spec.upload_flags & option_names or spec.output_flags & option_names:
            return False
        if _FORBIDDEN_ROLES & spec.roles:
            return False
        return (
            _is_readonly(command)
            or Role.NETWORK in spec.roles
            or _matches_prefix(command, policy.safe_prefixes)
        )

    def _paths_are_safe(self, action: NormalizedAction, policy: Policy) -> bool:
        """Condition 11."""
        paths = [
            p
            for c in action.commands
            for p in command_paths(c.argv, action.cwd, PathRole.ANY)
        ]
        return all(
            is_within(p, policy.allowed_paths)
            and not matches_any(p, policy.protected_paths, policy.workspace)
            for p in paths
        )
