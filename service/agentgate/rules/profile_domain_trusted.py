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

Conditions 5 and 9 are worth calling out on their own: `curl … | sh` is
closed by hard-deny long before this rule runs, but the rule refuses it
on its own so its correctness does not depend on the order of the chain;
`curl … > file` is closed by 5.

Condition 6 -- "every option in the command's argv is read-only" -- is a
CLOSED allowlist (`CommandSpec.read_only_flags`), not a check for the
ABSENCE of an upload or output flag. A negative check has to name every
way a command can write, upload, or change its target, and a review pass
found eight it had missed in a single look: `-K`/`--config` (a config
file can itself set the URL, an upload file, an output path, or a
header), `-D`/`--dump-header`, `-c`/`--cookie-jar`, `--trace*`,
`--etag-save`, `--stderr`, `--remote-name-all`, and plain `wget <url>`
(writes a file by default, no flag required). A positive list only has
to be right about what curl and wget CANNOT do with the flags on it, and
an unlisted flag -- including the next one a future curl release adds --
falls outside the allowlist by default instead of needing its own new
negative check.

`-X`/`--request` sits inside the allowlist but its value is still
inspected: the flag itself writes nothing, but `curl -X DELETE
https://github.com/o/r` is a listed domain and a destructive act (the
exact example spec v3.1 §5.1 uses for why a domain is not a permission),
so only `GET`/`HEAD` pass. wget has no flag-free read that stays off
disk, so it qualifies only when told to write to stdout: `-O -`,
`--output-document=-`, or the bundled `-qO-`.
"""

from collections.abc import Sequence

from agentgate.api.schemas import Tool
from agentgate.domain.domains import domain_allowed
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction, SimpleCommand
from agentgate.profiles.schema import NetworkMode
from agentgate.rules.readonly import is_readonly, matches_prefix, paths_are_safe
from agentgate.shell.commands import CommandSpec, Role, spec_for
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

# The methods a trusted-domain read may use. Any other value of
# -X/--request makes the invocation something other than a read.
_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})


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
        if _FORBIDDEN_ROLES & spec.roles:
            return False
        if Role.NETWORK in spec.roles:
            return self._network_command_is_readonly(
                command, spec
            ) or matches_prefix(command, policy.safe_prefixes)
        return is_readonly(command) or matches_prefix(command, policy.safe_prefixes)

    def _network_command_is_readonly(self, command: SimpleCommand, spec: CommandSpec) -> bool:
        """Condition 6: every option this network command carries is on its
        closed read-only allowlist, and any inspected value (curl's
        method, wget's output target) is itself a read.
        """
        if not spec.read_only_flags:
            return False
        options = _parse_read_only_options(command.argv, spec)
        if options is None:
            return False
        if any(name not in spec.read_only_flags for name, _ in options):
            return False
        exe = command.argv[0]
        if exe == "curl":
            # `-H @file` makes curl read the headers from a local file and
            # send them: an allowlisted flag becomes an exfiltration channel.
            if any(n in ("-H", "--header") and v is not None and v.startswith("@") for n, v in options):
                return False
            methods = [v.upper() for n, v in options if n in ("-X", "--request") and v is not None]
            return all(m in _READ_ONLY_METHODS for m in methods)
        if exe == "wget":
            outputs = [v for n, v in options if n in ("-O", "--output-document") and v is not None]
            return len(outputs) == 1 and outputs[0] == "-"
        return False

    def _paths_are_safe(self, action: NormalizedAction, policy: Policy) -> bool:
        """Condition 11."""
        paths = [
            p
            for c in action.commands
            for p in command_paths(c.argv, action.cwd, PathRole.ANY)
        ]
        return paths_are_safe(paths, policy)


def _boolean_short_letters(spec: CommandSpec) -> frozenset[str]:
    """Letters of this command's two-character, no-value flags -- the ones
    a short-option cluster like `-sSL` may bundle freely.
    """
    boolean_flags = spec.read_only_flags - spec.read_only_value_flags
    return frozenset(f[1] for f in boolean_flags if len(f) == 2 and f[0] == "-" and f[1] != "-")


def _value_short_letters(spec: CommandSpec) -> dict[str, str]:
    """This command's two-character value-taking flags, indexed by letter,
    so a cluster's trailing letter can claim the rest of the token (or
    the next one) as its value.
    """
    return {
        f[1]: f
        for f in spec.read_only_value_flags
        if len(f) == 2 and f[0] == "-" and f[1] != "-"
    }


def _parse_read_only_options(
    argv: Sequence[str], spec: CommandSpec
) -> list[tuple[str, str | None]] | None:
    """Read ``argv`` the way a read-only curl or wget invocation is shaped:
    a long flag with an attached "=value", a flag whose value is the next
    token, or a short-option cluster (`-sSL`, `-qO-`) where every letter
    but the last is boolean and a trailing value letter claims the rest
    of the token -- or the next one -- as its value.

    Returns None the instant a token cannot be read this way: an
    unrecognized flag, an unrecognized letter inside a cluster, or a
    value flag with nothing left to take a value from. The caller treats
    None exactly like "this command is not a read": failing to parse a
    shape is not proof of safety.
    """
    boolean_letters = _boolean_short_letters(spec)
    value_letters = _value_short_letters(spec)
    tokens = argv[1:]
    options: list[tuple[str, str | None]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token.startswith("-") or token == "-":
            continue
        if token.startswith("--"):
            name, separator, value = token.partition("=")
            if separator:
                options.append((name, value))
            elif name in spec.read_only_value_flags:
                if index >= len(tokens):
                    return None
                options.append((name, tokens[index]))
                index += 1
            else:
                options.append((name, None))
            continue
        if token in spec.read_only_value_flags:
            if index >= len(tokens):
                return None
            options.append((token, tokens[index]))
            index += 1
            continue
        if token in spec.read_only_flags:
            options.append((token, None))
            continue
        cluster = _parse_short_cluster(token[1:], boolean_letters, value_letters, tokens, index)
        if cluster is None:
            return None
        cluster_options, index = cluster
        options.extend(cluster_options)
    return options


def _parse_short_cluster(
    body: str,
    boolean_letters: frozenset[str],
    value_letters: dict[str, str],
    tokens: Sequence[str],
    index: int,
) -> tuple[list[tuple[str, str | None]], int] | None:
    """Split a bundled short-option token's body (`"sSL"`, `"qO-"`) into
    one option per boolean letter, ending in at most one value-taking
    letter that claims the rest of the token -- or, if nothing is left,
    the next argv token -- as its value. None if a letter is neither.
    """
    options: list[tuple[str, str | None]] = []
    position = 0
    while position < len(body):
        letter = body[position]
        if letter in boolean_letters:
            options.append((f"-{letter}", None))
            position += 1
            continue
        if letter in value_letters:
            rest = body[position + 1:]
            if rest:
                options.append((value_letters[letter], rest))
            else:
                if index >= len(tokens):
                    return None
                options.append((value_letters[letter], tokens[index]))
                index += 1
            return options, index
        return None
    return options, index
