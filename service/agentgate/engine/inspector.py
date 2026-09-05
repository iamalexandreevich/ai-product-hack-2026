"""The inspect pipeline: cache -> detectors -> mask -> classifier by flag.

`drop` is the fail-closed answer here: there is no human to ask about a
result that already exists, and passing it unjudged is the one thing this
route must never do. A cache that fails means no cache; a detector that
raises means `drop` with `api.internal-error`. Stage 2 is the one place
this invariant relaxes on purpose: an error there falls back to stage 1's
verdict, not to `drop` -- stage 1 already produced a safe answer, and the
classifier only ever re-judges it.
"""

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone

from ulid import ULID

from agentgate.api.schemas import InspectRequest, InspectVerdict
from agentgate.domain.dialogue import Dialogue
from agentgate.domain.inspect_cache import InspectCache, inspect_cache_key
from agentgate.domain.policy import Policy
from agentgate.engine.inspection import Inspection
from agentgate.engine.timings import Timings
from agentgate.inspect.classify import InspectCase, InspectClassifier, InspectVerdictOutcome
from agentgate.inspect.detectors import Detector, Finding, scan
from agentgate.inspect.mask import Stage1Outcome, apply
from agentgate.profiles.loader import detect_workspace
from agentgate.profiles.schema import Profile

log = logging.getLogger(__name__)

_INVISIBLE_RULE = "inspect.invisible"


class Inspector:
    def __init__(
        self,
        profiles: Mapping[str, Profile],
        default_profile: str,
        detectors: tuple[Detector, ...],
        cache: InspectCache,
        ttl_seconds: int = 86400,
        classifiers: Mapping[str, Mapping[str, InspectClassifier]] | None = None,
    ) -> None:
        self._profiles = profiles
        self._default_profile = default_profile
        self._detectors = detectors
        self._cache = cache
        self._ttl_seconds = ttl_seconds
        self._classifiers = classifiers

    async def inspect(self, request: InspectRequest) -> Inspection:
        timings = Timings()
        inspection_id = str(ULID())
        profile_id = request.profile_id or self._default_profile
        profile = self._profiles.get(profile_id)
        if profile is None:
            return self._refuse(
                inspection_id, request, timings, profile_id, "", "api.unknown-profile",
                f"unknown profile '{profile_id}'",
            )
        policy = Policy.bind(profile, detect_workspace(request.args.cwd))
        key = inspect_cache_key(policy.profile_hash, request.provenance.kind, _digest(request.output))

        hit = await self._cached(key)
        if hit is not None:
            return replace(
                hit, id=inspection_id, ts=datetime.now(timezone.utc), request=request,
                cached=True, latency=timings.finish(),
            )

        try:
            with timings.stage(1):
                findings = scan(request.output, self._detectors)
                outcome = apply(request.output, findings)
        except Exception:  # noqa: BLE001 - a detector bug must read as drop, never as pass
            log.exception("inspect stage 1 raised")
            return self._refuse(
                inspection_id, request, timings, profile_id, policy.profile_hash,
                "api.internal-error", "internal error", error="unexpected",
            )

        verdict, replacement, reason, stage, model, cls_error = outcome.verdict, outcome.replacement, outcome.reason, 1, None, None
        if self._should_classify(policy, outcome, findings):
            with timings.stage(2):
                result = await self._classify(request, profile_id, policy, findings, outcome)
            if result.error is not None:
                cls_error = result.error
                model = result.model
            else:
                verdict, replacement, reason, stage, model = (
                    result.verdict, result.replacement, result.reason, 2, result.model,
                )

        inspection = Inspection(
            id=inspection_id, ts=datetime.now(timezone.utc), request=request, verdict=verdict,
            latency=timings.finish(), profile_id=profile_id, profile_hash=policy.profile_hash,
            replacement=replacement, reason=reason, stage=stage, rule_id=outcome.rule_id, model=model,
            error=cls_error, findings=tuple(f.rule_id for f in findings),
        )
        await self._remember(key, inspection)
        return inspection

    def _should_classify(self, policy: Policy, outcome: Stage1Outcome, findings: list[Finding]) -> bool:
        if policy.inspect.classifier != "on-flag":
            return False
        if outcome.verdict is InspectVerdict.pass_:
            return False
        # A finding this route never lets the classifier soften: if every
        # flagged line is `inspect.invisible`, stage 2 is not even asked.
        return any(f.rule_id != _INVISIBLE_RULE for f in findings)

    async def _classify(
        self, request: InspectRequest, profile_id: str, policy: Policy,
        findings: list[Finding], outcome: Stage1Outcome,
    ) -> InspectVerdictOutcome:
        classifier = self._classifier_for(profile_id, policy)
        if classifier is None:
            return InspectVerdictOutcome(
                verdict=outcome.verdict, replacement=outcome.replacement, reason=outcome.reason,
                model=None, error="unknown-model",
            )
        case = InspectCase.build(request, Dialogue.of(request.history), policy, findings, outcome)
        try:
            return await classifier.classify(case)
        except Exception as exc:  # noqa: BLE001 - a classifier bug falls back to stage 1, never to `pass`
            log.exception("inspect stage 2 raised")
            return InspectVerdictOutcome(
                verdict=outcome.verdict, replacement=outcome.replacement, reason=outcome.reason,
                model=classifier.name, error=f"unexpected ({type(exc).__name__})",
            )

    def _classifier_for(self, profile_id: str, policy: Policy) -> InspectClassifier | None:
        if self._classifiers is None:
            return None
        per_profile = self._classifiers.get(profile_id)
        if per_profile is None:
            return None
        return per_profile.get(policy.profile.models.default)

    async def _cached(self, key: str) -> Inspection | None:
        try:
            return await self._cache.get(key)
        except Exception:  # noqa: BLE001 - a cache failure means no cache, not a failure
            log.exception("inspect cache get raised")
            return None

    async def _remember(self, key: str, inspection: Inspection) -> None:
        try:
            await self._cache.put(key, inspection, self._ttl_seconds)
        except Exception:  # noqa: BLE001 - a cache failure means no cache, not a failure
            log.exception("inspect cache put raised")

    def _refuse(
        self, inspection_id: str, request: InspectRequest, timings: Timings, profile_id: str,
        profile_hash: str, rule_id: str, reason: str, error: str | None = None,
    ) -> Inspection:
        return Inspection(
            id=inspection_id, ts=datetime.now(timezone.utc), request=request, verdict=InspectVerdict.drop,
            latency=timings.finish(), profile_id=profile_id, profile_hash=profile_hash,
            stage=0, rule_id=rule_id, reason=reason, error=error,
        )


def _digest(output: str) -> str:
    return hashlib.sha256(output.encode("utf-8", "surrogatepass")).hexdigest()
