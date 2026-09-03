"""Wires prompt building + LLMClient.classify into a fail-closed Stage2Result.

Every Stage2Error, and every other exception the client did not
anticipate, resolves to DecisionKind.ask — never allow, never deny.
Only the happy path (a valid A/D/U from the model) can produce anything
else.

An action bashlex could not structurally parse (`flags.unparseable`) is
refused before any prompt is built and before the LLM is ever called:
`commands`/`paths`/`domains` are empty by construction for such an
action (see normalize/shell.py), so nothing about it was actually
verified, and _MAP has no cross-check that would stop a classifier
from answering "A" about an action it never saw. Short-circuiting here,
rather than trusting the model to notice `unparseable=true` in the
flags line, is what makes that unreachable.
"""

from dataclasses import dataclass

from agentgate.api.schemas import DecisionKind
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.stage2.client import LLMClient, Stage2Error
from agentgate.stage2.prompt import build_system_prompt, build_user_message

_MAP = {"A": DecisionKind.allow, "D": DecisionKind.deny, "U": DecisionKind.ask}


@dataclass
class Stage2Result:
    decision: DecisionKind
    reason: str
    suggest: str
    model: str
    raw_response: dict | None
    error: str | None


async def run_stage2(
    action: NormalizedAction,
    user_request: str,
    profile: Profile,
    model_name: str,
    client: LLMClient,
    stage1_note: str,
) -> Stage2Result:
    if action.flags.unparseable:
        return Stage2Result(
            DecisionKind.ask,
            "action could not be structurally parsed and was never verified",
            "",
            model_name,
            None,
            None,
        )

    system = build_system_prompt(profile)
    user = build_user_message(action, user_request, stage1_note)
    try:
        out, raw = await client.classify(system, user)
    except Stage2Error as exc:
        return Stage2Result(DecisionKind.ask, f"classifier unavailable: {exc.kind}", "", model_name, None, exc.kind)
    except Exception as exc:  # noqa: BLE001 - fail closed on anything, not just Stage2Error
        return Stage2Result(
            DecisionKind.ask,
            f"classifier unavailable: unexpected ({type(exc).__name__})",
            "",
            model_name,
            None,
            "unexpected",
        )

    decision = _MAP[out.decision]
    reason = "" if decision is DecisionKind.allow else out.reason
    suggest = "" if decision is DecisionKind.allow else out.suggest
    return Stage2Result(decision, reason, suggest, model_name, raw, None)
