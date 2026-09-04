"""The Classifier the service runs in production: one LLM behind one prompt.

Every Stage2Error, and every other exception the client did not
anticipate, resolves to DecisionKind.ask -- never allow, never deny.
Only the happy path (a valid A/D/U from the model) can produce anything
else.

Nothing here guards against an action bashlex could not parse: stage 1's
UnparseableRule settles those before the cascade reaches this module, so
the classifier is never asked about an action it could not have seen.
"""

import logging

import httpx

from agentgate.api.schemas import DecisionKind
from agentgate.classify.base import Classifier
from agentgate.classify.client import LLMClient, Stage2Error
from agentgate.classify.prompt import build_system_prompt, build_user_message
from agentgate.classify.schema import ClassifierOutput
from agentgate.domain.policy import Policy
from agentgate.domain.verdict import Verdict
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import ModelConfig, Profile

log = logging.getLogger(__name__)

_DECISIONS = {"A": DecisionKind.allow, "D": DecisionKind.deny, "U": DecisionKind.ask}


class LLMClassifier:
    def __init__(self, name: str, model_config: ModelConfig, http: httpx.AsyncClient) -> None:
        self.name = name
        self._client = LLMClient(name, model_config, http)

    async def classify(
        self, action: NormalizedAction, user_request: str, policy: Policy, stage1_note: str
    ) -> Verdict:
        system = build_system_prompt(policy)
        user = build_user_message(action, user_request, stage1_note)
        try:
            output, raw = await self._client.classify(system, user)
        except Stage2Error as exc:
            return self._unavailable(exc.kind, f"classifier unavailable: {exc.kind}")
        except Exception as exc:  # noqa: BLE001 - fail closed on anything, not just Stage2Error
            log.warning("classifier raised an unexpected error", exc_info=True)
            return self._unavailable(
                "unexpected", f"classifier unavailable: unexpected ({type(exc).__name__})"
            )
        return self._verdict_from(output, raw)

    def _verdict_from(self, output: ClassifierOutput, raw: dict) -> Verdict:
        decision = _DECISIONS[output.decision]
        allowed = decision is DecisionKind.allow
        return Verdict(
            decision=decision,
            stage=2,
            reason="" if allowed else output.reason,
            suggest="" if allowed else output.suggest,
            model=self.name,
            raw_response=raw,
        )

    def _unavailable(self, error: str, reason: str) -> Verdict:
        return Verdict(
            decision=DecisionKind.ask, stage=2, reason=reason, model=self.name, error=error
        )


def build_classifiers(profile: Profile, http: httpx.AsyncClient) -> dict[str, Classifier]:
    """One classifier per model the profile declares, built once at startup."""
    return {
        name: LLMClassifier(name, config, http)
        for name, config in profile.models.configs.items()
    }
