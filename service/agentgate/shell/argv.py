"""One structural read of an argv.

Four hand-rolled `while index < len(argv)` loops used to answer four
variations of the same question -- which tokens are flags, which flag
took the next token as its value, what is left over as a positional.
They are one parse now, and the callers ask it questions.

Which flags take a separate value is per-command knowledge the caller
supplies; this module only knows the shapes (`-o value`, `-o=value`,
`--output=value`, `--`). Whether `--` ends the options at all is
per-command knowledge too -- not every command implements the
convention -- so the caller can turn it off.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Option:
    name: str
    value: str | None
    inline: bool


@dataclass(frozen=True)
class ParsedArgv:
    executable: str
    positionals: tuple[str, ...]
    options: tuple[Option, ...]

    @classmethod
    def of(
        cls,
        argv: Sequence[str],
        value_flags: frozenset[str] = frozenset(),
        *,
        double_dash_ends_options: bool = False,
    ) -> "ParsedArgv":
        if not argv:
            return cls("", (), ())
        options: list[Option] = []
        positionals: list[str] = []
        index = 1
        while index < len(argv):
            token = argv[index]
            index += 1
            if token == "--" and double_dash_ends_options:
                positionals.extend(argv[index:])
                break
            if not token.startswith("-") or token == "-":
                positionals.append(token)
                continue
            name, separator, inline_value = token.partition("=")
            if separator:
                options.append(Option(name, inline_value, inline=True))
                continue
            if name in value_flags and index < len(argv):
                options.append(Option(name, argv[index], inline=False))
                index += 1
                continue
            options.append(Option(name, None, inline=False))
        return cls(argv[0], tuple(positionals), tuple(options))

    def option(self, name: str) -> Option | None:
        for candidate in self.options:
            if candidate.name == name:
                return candidate
        return None

    def has(self, name: str) -> bool:
        return self.option(name) is not None

    def values_of(self, *names: str) -> tuple[str, ...]:
        return tuple(
            option.value for option in self.options
            if option.name in names and option.value is not None
        )
