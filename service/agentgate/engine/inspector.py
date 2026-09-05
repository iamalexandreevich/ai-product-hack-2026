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
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ulid import ULID

from agentgate.api.schemas import Cost, InspectRequest, InspectVerdict
from agentgate.domain.dialogue import Dialogue
from agentgate.domain.inspect_cache import InspectCache
from agentgate.domain.policy import Policy
from agentgate.engine.inspection import Inspection
from agentgate.engine.timings import Timings
from agentgate.inspect.classify import InspectCase, InspectClassifier, InspectOutcome
from agentgate.inspect.detectors import Action, Detector, Finding, scan
from agentgate.inspect.mask import Stage1Outcome, apply
from agentgate.profiles.loader import detect_workspace
from agentgate.profiles.schema import Profile
from agentgate.session.cache_key import inspect_cache_key

log = logging.getLogger(__name__)

_INVISIBLE_RULE = "inspect.invisible"


@dataclass(frozen=True)
class _Context:
    """Everything resolved from the request before stage 1 runs."""

    policy: Policy
    cache_key: str


@dataclass(frozen=True)
class _Stage2Result:
    """The verdict this inspection currently stands on, and how it got
    there -- seeded from stage 1's `Stage1Outcome` and replaced exactly
    once, by `_cap_stage2`, if stage 2 runs and answers.
    """

    verdict: InspectVerdict
    replacement: str | None
    reason: str
    stage: int
    rule_id: str | None
    model: str | None
    error: str | None
    cost: Cost | None = None

    @classmethod
    def from_stage1(cls, outcome: Stage1Outcome) -> "_Stage2Result":
        return cls(
            verdict=outcome.verdict, replacement=outcome.replacement, reason=outcome.reason,
            stage=1, rule_id=outcome.rule_id, model=None, error=None,
        )


class Inspector:
    def __init__(
        self,
        profiles: Mapping[str, Profile],
        default_profile: str,
        detectors: tuple[Detector, ...],
        cache: InspectCache[Inspection],
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
        # Resolved up front, independent of profile lookup: every `Inspection`
        # this method returns -- refused or not -- carries the workspace its
        # session row (if any) needs, and detect_workspace needs only `cwd`.
        workspace = detect_workspace(request.args.cwd)

        resolved = await self._resolve(inspection_id, request, timings, profile_id, workspace)
        if isinstance(resolved, Inspection):
            return resolved
        policy, cache_key = resolved.policy, resolved.cache_key

        hit = await self._cached(cache_key)
        if hit is not None:
            return hit.as_cached(inspection_id, request, timings.finish(), workspace)

        try:
            with timings.stage(1):
                findings = scan(request.output, self._detectors)
                outcome = apply(request.output, findings)
        except Exception:  # noqa: BLE001 - a detector bug must read as drop, never as pass
            log.exception("inspect stage 1 raised")
            return self._refuse(
                inspection_id, request, timings, profile_id, policy.profile_hash, workspace,
                "api.internal-error", "internal error", error="unexpected",
            )

        result = _Stage2Result.from_stage1(outcome)
        if self._should_classify(policy, outcome, findings):
            with timings.stage(2):
                result = await self._run_stage2(request, profile_id, policy, findings, outcome, result)

        inspection = Inspection(
            id=inspection_id, ts=datetime.now(timezone.utc), request=request, verdict=result.verdict,
            latency=timings.finish(), profile_id=profile_id, profile_hash=policy.profile_hash,
            replacement=result.replacement, reason=result.reason, stage=result.stage, rule_id=result.rule_id,
            model=result.model, error=result.error, findings=tuple(f.rule_id for f in findings), workspace=workspace,
            cost=result.cost,
        )
        if inspection.error is None:
            # An inspection whose stage 2 failed carries stage 1's verdict
            # under `error` -- caching it would later surface as a cache hit
            # with `error` dropped (see `Inspection.as_cached`) but `model` kept,
            # misreporting a verdict no model actually gave.
            await self._remember(cache_key, inspection)
        return inspection

    async def _resolve(
        self, inspection_id: str, request: InspectRequest, timings: Timings, profile_id: str, workspace: str,
    ) -> "_Context | Inspection":
        try:
            profile = self._profiles.get(profile_id)
            if profile is None:
                return self._refuse(
                    inspection_id, request, timings, profile_id, "", workspace, "api.unknown-profile",
                    f"unknown profile '{profile_id}'",
                )
            policy = Policy.bind(profile, workspace)
            cache_key = inspect_cache_key(policy.profile_hash, request.provenance.kind, _digest(request.output))
            return _Context(policy=policy, cache_key=cache_key)
        except Exception:  # noqa: BLE001 - a bug resolving the policy must read as drop, never as pass
            log.exception("inspect prelude raised")
            return self._refuse(
                inspection_id, request, timings, profile_id, "", workspace, "api.internal-error", "internal error",
                error="unexpected",
            )

    async def _run_stage2(
        self, request: InspectRequest, profile_id: str, policy: Policy,
        findings: list[Finding], outcome: Stage1Outcome, result: _Stage2Result,
    ) -> _Stage2Result:
        classified = await self._classify(request, profile_id, policy, findings, outcome)
        if classified.error is not None:
            return replace(result, error=classified.error, model=classified.model)
        verdict, replacement, reason, rule_id = self._cap_stage2(request, findings, outcome, classified)
        return replace(result, verdict=verdict, replacement=replacement, reason=reason, rule_id=rule_id,
                       stage=2, model=classified.model, cost=classified.cost)

    def _should_classify(self, policy: Policy, outcome: Stage1Outcome, findings: list[Finding]) -> bool:
        if policy.inspect.classifier != "on-flag":
            return False
        if outcome.verdict is InspectVerdict.pass_:
            return False
        # A finding this route never lets the classifier soften: if every
        # flagged line is `inspect.invisible`, stage 2 is not even asked.
        return any(f.rule_id != _INVISIBLE_RULE for f in findings)

    def _cap_stage2(
        self, request: InspectRequest, findings: list[Finding], outcome: Stage1Outcome, result: InspectOutcome,
    ) -> tuple[InspectVerdict, str | None, str, str | None]:
        """Apply the two caps spec 5.3 puts on a classifier answer, then derive `rule_id`.

        The drop threshold is a module constant, not model-negotiable: a
        `pass` cannot lift a stage-1 `drop`. Separately, `inspect.invisible`
        cleaning can never be undone: if the output carries a `clean`
        finding and the classifier says `pass`, the cleaned lines still
        reach the model while the rest of the output is restored -- reusing
        `mask.apply` on just the `clean` findings, since that is exactly
        the rewrite this route already trusts for that job. A verdict that
        stays `pass` after both caps carries no `rule_id`: stage 1's rule
        did not actually hold.
        """
        verdict, reason = result.verdict, result.reason
        # The classifier returns no text: a `mask` answer means stage 1's
        # own rewrite stands.
        replacement = outcome.replacement if verdict is InspectVerdict.mask else None
        rule_id = outcome.rule_id
        clean_findings = [f for f in findings if f.action is Action.clean]
        if outcome.verdict is InspectVerdict.drop and verdict is InspectVerdict.pass_:
            verdict, replacement = InspectVerdict.drop, None
            reason = f"stage 1 drop threshold stands despite model disagreement ({reason}): {outcome.reason}"
        elif clean_findings and verdict is InspectVerdict.pass_:
            cleaned = apply(request.output, clean_findings)
            verdict, replacement, reason, rule_id = InspectVerdict.mask, cleaned.replacement, cleaned.reason, _INVISIBLE_RULE
        if verdict is InspectVerdict.pass_:
            rule_id = None
        return verdict, replacement, reason, rule_id

    async def _classify(
        self, request: InspectRequest, profile_id: str, policy: Policy,
        findings: list[Finding], outcome: Stage1Outcome,
    ) -> InspectOutcome:
        classifier = self._classifier_for(profile_id, policy)
        if classifier is None:
            return InspectOutcome(verdict=outcome.verdict, reason=outcome.reason, model=None, error="unknown-model")
        case = InspectCase.build(request, Dialogue.of(request.history), policy, findings, outcome)
        try:
            return await classifier.classify(case)
        except Exception as exc:  # noqa: BLE001 - a classifier bug falls back to stage 1, never to `pass`
            log.exception("inspect stage 2 raised")
            return InspectOutcome(
                verdict=outcome.verdict, reason=outcome.reason,
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
        profile_hash: str, workspace: str, rule_id: str, reason: str, error: str | None = None,
    ) -> Inspection:
        return Inspection(
            id=inspection_id, ts=datetime.now(timezone.utc), request=request, verdict=InspectVerdict.drop,
            latency=timings.finish(), profile_id=profile_id, profile_hash=profile_hash,
            stage=0, rule_id=rule_id, reason=reason, error=error, workspace=workspace,
        )


def _digest(output: str) -> str:
    return hashlib.sha256(output.encode("utf-8", "surrogatepass")).hexdigest()
