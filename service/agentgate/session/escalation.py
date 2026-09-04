"""Escalation rule: decide whether a session's history should force `ask`.

Must be evaluated BEFORE the current decision is recorded into SessionState.
This function only reads state; it never overrides a hard `deny` itself —
that composition is the caller's responsibility -- see Gate.decide().
"""

from agentgate.domain.session import SessionState
from agentgate.profiles.schema import Escalation


def should_escalate(state: SessionState, cfg: Escalation) -> bool:
    if state.deny_consecutive >= cfg.deny_consecutive:
        return True
    window = list(state.recent)[-cfg.deny_window.of_last :]
    return window.count("deny") >= cfg.deny_window.count
