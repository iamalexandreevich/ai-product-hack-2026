"""One table of what the service knows about a command.

Eleven sets spread over five files used to answer variants of "what does
this command do", so a single command was described in three or four
places by different properties and the answers drifted apart. Every such
question is a query against this table now, and adding a command is one
row.

A command with no row gets a neutral spec: no role, no write target, no
declared path arguments. An unknown command must never raise on the hot
path and must never inherit a privilege by omission.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto


class Role(Enum):
    """What a command is, for the rules that ask about a class of commands."""

    READONLY = auto()
    MUTATING = auto()
    NETWORK = auto()
    DOWNLOADER = auto()
    INTERPRETER = auto()
    SHELL = auto()
    FIREWALL = auto()
    ESCALATOR = auto()
    # Runs its remaining argv as a command, passing stdin through
    # unchanged. xargs runs its remaining argv too but CONSUMES stdin as
    # an argument source, so it does not carry this role.
    WRAPPER = auto()
    # Sends whatever arrives on stdin to the far end with no flag at all.
    STDIN_FORWARDER = auto()


class WriteTarget(Enum):
    """Which of a command's positional arguments it writes to."""

    NONE = auto()
    EVERY_POSITIONAL = auto()
    LAST_POSITIONAL = auto()
    # The first positional is a script rather than a file, and the write
    # happens only when the command was asked to edit in place.
    POSITIONALS_AFTER_FIRST = auto()


class PathArguments(Enum):
    """Which of a command's positional arguments are known to be paths."""

    # Nothing declared: the looks_like_path heuristic judges each token.
    UNDECLARED = auto()
    EVERY_POSITIONAL = auto()
    # Positionals are the roots of a search, so a glob pattern among them
    # is a pattern, not a path.
    SEARCH_ROOTS = auto()


@dataclass(frozen=True)
class CommandSpec:
    """Everything the service knows about one command."""

    name: str
    roles: frozenset[Role] = field(default_factory=frozenset)
    # Options whose value is the NEXT argv token rather than part of this
    # one, so a reader does not mistake the value for a positional. Only
    # options whose argument is MANDATORY belong here: an
    # optional-argument option (xargs -i/-l/-e, env -i) must not consume
    # the token after it, which may be the wrapped command itself.
    # Attached forms ("-n10", "--signal=KILL") carry their value inside
    # the token and need no entry.
    value_flags: frozenset[str] = field(default_factory=frozenset)
    # Options whose value is transmitted outward: the argument becomes
    # request body or upload content.
    upload_flags: frozenset[str] = field(default_factory=frozenset)
    write_target: WriteTarget = WriteTarget.NONE
    path_arguments: PathArguments = PathArguments.UNDECLARED
    # Subcommands of this command that only read.
    readonly_subcommands: frozenset[str] = field(default_factory=frozenset)


def _row(
    name: str,
    *roles: Role,
    value_flags: Sequence[str] = (),
    upload_flags: Sequence[str] = (),
    write_target: WriteTarget = WriteTarget.NONE,
    path_arguments: PathArguments = PathArguments.UNDECLARED,
    readonly_subcommands: Sequence[str] = (),
) -> CommandSpec:
    return CommandSpec(
        name=name,
        roles=frozenset(roles),
        value_flags=frozenset(value_flags),
        upload_flags=frozenset(upload_flags),
        write_target=write_target,
        path_arguments=path_arguments,
        readonly_subcommands=frozenset(readonly_subcommands),
    )


_EVERY = PathArguments.EVERY_POSITIONAL

COMMANDS: Mapping[str, CommandSpec] = {
    spec.name: spec
    for spec in (
        _row("awk", path_arguments=_EVERY),
        _row("bash", Role.SHELL, Role.INTERPRETER),
        _row("cat", Role.READONLY, path_arguments=_EVERY),
        _row("cd", path_arguments=_EVERY),
        _row("chmod", Role.MUTATING, path_arguments=_EVERY),
        _row("chown", Role.MUTATING, path_arguments=_EVERY),
        _row("command", Role.WRAPPER),
        _row("cp", Role.MUTATING, path_arguments=_EVERY, write_target=WriteTarget.LAST_POSITIONAL),
        _row("curl", Role.NETWORK, Role.DOWNLOADER, upload_flags=(
            "-T", "--upload-file", "-d", "--data", "--data-ascii", "--data-binary",
            "--data-raw", "--data-urlencode", "-F", "--form",
        )),
        _row("cut", Role.READONLY),
        _row("dash", Role.SHELL, Role.INTERPRETER),
        _row("dd", Role.MUTATING, path_arguments=_EVERY),
        _row("diff", Role.READONLY),
        _row("doas", Role.WRAPPER, Role.ESCALATOR, value_flags=("-u", "-C", "-a")),
        _row("du", Role.READONLY, path_arguments=_EVERY),
        _row("env", Role.WRAPPER, value_flags=(
            "-u", "--unset", "-C", "--chdir", "-S", "--split-string",
        )),
        _row("file", Role.READONLY),
        _row("find", path_arguments=PathArguments.SEARCH_ROOTS, value_flags=(
            "-name", "-iname", "-path", "-type", "-exec",
        )),
        _row("firewall-cmd", Role.FIREWALL),
        _row("ftp", Role.NETWORK),
        _row("git", value_flags=(
            "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
        ), readonly_subcommands=(
            "status", "diff", "log", "show", "branch", "rev-parse", "remote", "blame",
        )),
        _row("grep", Role.READONLY, path_arguments=_EVERY),
        _row("head", Role.READONLY, path_arguments=_EVERY),
        _row("install", Role.MUTATING, write_target=WriteTarget.LAST_POSITIONAL),
        _row("ip6tables", Role.FIREWALL),
        _row("iptables", Role.FIREWALL),
        _row("ksh", Role.SHELL, Role.INTERPRETER),
        _row("less", Role.READONLY, path_arguments=_EVERY),
        _row("ln", Role.MUTATING, path_arguments=_EVERY, write_target=WriteTarget.LAST_POSITIONAL),
        _row("ls", Role.READONLY, path_arguments=_EVERY),
        _row("mkdir", Role.MUTATING, path_arguments=_EVERY),
        _row("more", Role.READONLY, path_arguments=_EVERY),
        _row("mv", Role.MUTATING, path_arguments=_EVERY, write_target=WriteTarget.LAST_POSITIONAL),
        _row("nc", Role.NETWORK, Role.STDIN_FORWARDER),
        _row("ncat", Role.NETWORK, Role.STDIN_FORWARDER),
        _row("netcat", Role.NETWORK, Role.STDIN_FORWARDER),
        _row("nft", Role.FIREWALL),
        _row("nice", Role.WRAPPER, value_flags=("-n", "--adjustment")),
        _row("node", Role.INTERPRETER),
        _row("nohup", Role.WRAPPER),
        _row("perl", Role.INTERPRETER),
        _row("pfctl", Role.FIREWALL),
        _row("pwd", Role.READONLY),
        _row("python", Role.INTERPRETER),
        _row("python3", Role.INTERPRETER),
        _row("rg", Role.READONLY, path_arguments=_EVERY),
        _row("rm", Role.MUTATING, path_arguments=_EVERY),
        _row("rmdir", Role.MUTATING, path_arguments=_EVERY),
        _row("rsync", Role.NETWORK, value_flags=(
            "-i", "-e", "-F", "-o", "-l", "-P", "--rsh", "--exclude",
        )),
        _row("ruby", Role.INTERPRETER),
        _row("scp", Role.NETWORK, value_flags=(
            "-i", "-e", "-F", "-o", "-l", "-P", "--rsh", "--exclude",
        )),
        _row("sed", path_arguments=_EVERY, write_target=WriteTarget.POSITIONALS_AFTER_FIRST),
        _row("setsid", Role.WRAPPER),
        _row("sftp", Role.NETWORK),
        _row("sh", Role.SHELL, Role.INTERPRETER),
        _row("shred", Role.MUTATING, path_arguments=_EVERY),
        _row("socat", Role.NETWORK, Role.STDIN_FORWARDER),
        _row("sort", Role.READONLY),
        _row("ssh", Role.NETWORK, Role.STDIN_FORWARDER),
        _row("stat", Role.READONLY, path_arguments=_EVERY),
        _row("stdbuf", Role.WRAPPER, value_flags=(
            "-i", "--input", "-o", "--output", "-e", "--error",
        )),
        _row("su", Role.ESCALATOR),
        _row("sudo", Role.WRAPPER, Role.ESCALATOR, value_flags=(
            "-u", "--user", "-g", "--group", "-p", "--prompt", "-C", "--close-from",
            "-h", "--host", "-r", "--role", "-t", "--type", "-U", "--other-user",
        )),
        _row("tail", Role.READONLY, path_arguments=_EVERY),
        _row("tar", path_arguments=_EVERY),
        _row("tee", Role.MUTATING, path_arguments=_EVERY,
             write_target=WriteTarget.EVERY_POSITIONAL),
        _row("telnet", Role.NETWORK, Role.STDIN_FORWARDER),
        _row("timeout", Role.WRAPPER, value_flags=("-s", "--signal", "-k", "--kill-after")),
        _row("touch", Role.MUTATING, path_arguments=_EVERY),
        _row("tr", Role.READONLY),
        _row("tree", Role.READONLY),
        _row("truncate", Role.MUTATING, path_arguments=_EVERY),
        _row("ufw", Role.FIREWALL),
        _row("uniq", Role.READONLY),
        _row("unzip", path_arguments=_EVERY),
        _row("wc", Role.READONLY, path_arguments=_EVERY),
        _row("wget", Role.NETWORK, Role.DOWNLOADER, upload_flags=("--post-file", "--post-data")),
        _row("which", Role.READONLY),
        _row("xargs", value_flags=(
            "-n", "--max-args", "-P", "--max-procs", "-I", "-d", "--delimiter",
            "-a", "--arg-file", "-E", "-s", "--max-chars", "-L", "--max-lines",
        )),
        _row("zip", path_arguments=_EVERY),
        _row("zsh", Role.SHELL, Role.INTERPRETER),
    )
}

_NEUTRAL = CommandSpec(name="")


def spec_for(executable: str) -> CommandSpec:
    """What is known about ``executable``, or a neutral spec if nothing is."""
    return COMMANDS.get(executable, _NEUTRAL)


def commands_with_role(role: Role) -> frozenset[str]:
    return frozenset(name for name, spec in COMMANDS.items() if role in spec.roles)


def commands_with_write_target(target: WriteTarget) -> frozenset[str]:
    return frozenset(name for name, spec in COMMANDS.items() if spec.write_target is target)


def every_upload_flag() -> frozenset[str]:
    """Every option any command sends its value outward through."""
    return frozenset().union(*(spec.upload_flags for spec in COMMANDS.values()))
