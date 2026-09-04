"""Wires prompt building + LLMClient.classify into a fail-closed Verdict.

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

import logging

from agentgate.api.schemas import DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.stage2.client import LLMClient, Stage2Error
from agentgate.stage2.prompt import build_system_prompt, build_user_message

log = logging.getLogger(__name__)

_MAP = {"A": DecisionKind.allow, "D": DecisionKind.deny, "U": DecisionKind.ask}


async def run_stage2(
    action: NormalizedAction,
    user_request: str,
    profile: Profile,
    model_name: str,
    client: LLMClient,
    stage1_note: str,
) -> Verdict:
    if action.flags.unparseable:
        return Verdict(
            decision=DecisionKind.ask,
            stage=2,
            reason="action could not be structurally parsed and was never verified",
            model=model_name,
        )

    system = build_system_prompt(profile)
    user = build_user_message(action, user_request, stage1_note)
    try:
        out, raw = await client.classify(system, user)
    except Stage2Error as exc:
        return _unavailable(model_name, exc.kind, f"classifier unavailable: {exc.kind}")
    except Exception as exc:  # noqa: BLE001 - fail closed on anything, not just Stage2Error
        log.warning("classifier raised an unexpected error", exc_info=True)
        return _unavailable(
            model_name, "unexpected", f"classifier unavailable: unexpected ({type(exc).__name__})"
        )

    decision = _MAP[out.decision]
    allowed = decision is DecisionKind.allow
    return Verdict(
        decision=decision,
        stage=2,
        reason="" if allowed else out.reason,
        suggest="" if allowed else out.suggest,
        model=model_name,
        raw_response=raw,
    )


def _unavailable(model_name: str, error: str, reason: str) -> Verdict:
    return Verdict(
        decision=DecisionKind.ask, stage=2, reason=reason, model=model_name, error=error
    )
