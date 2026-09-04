"""A command whose wrapper chain could not be resolved to a real command
-- too deep to follow, or consumed whole into an option value.

Last of the hard-deny set: none of the rules above could have evaluated
such a command, so their silence is not evidence of safety and this asks
instead. Not itself a hard verdict -- nothing was determined, so nothing
is final.
"""

from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.rules.hard_deny.shared import wrapper_chain_unresolved


class WrapperUnresolvedRule:
    id = "ambiguous.wrapper"
    hard = False

    def evaluate(self, action: NormalizedAction, profile: Profile) -> Verdict | None:
        for command in action.commands:
            why = wrapper_chain_unresolved(command.argv)
            if why is None:
                continue
            head = " ".join(command.argv[:4])
            if why == "depth":
                return Verdict.ask(
                    "ambiguous.wrapper-depth",
                    f"command wraps its target through more layers than can be safely resolved: {head} ...",
                    "Run the command directly, without stacking wrapper commands",
                )
            if why == "opaque":
                return Verdict.ask(
                    "ambiguous.wrapper-opaque",
                    f"wrapper consumed its entire command into an option value, leaving nothing to inspect: {head}",
                    "Write the command directly, without passing it as a string to the wrapper "
                    "(e.g. use `env FOO=bar <command>` rather than `env -S '<command>'`)",
                )
        return None
