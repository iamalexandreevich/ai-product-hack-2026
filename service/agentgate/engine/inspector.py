"""The inspect pipeline: cache -> secrets -> detectors -> mask -> segments
-> classifier by mode -> reconcile.

`drop` is the fail-closed answer here: there is no human to ask about a
result that already exists, and passing it unjudged is the one thing this
route must never do. A cache that fails means no cache; a detector that
raises means `drop` with `api.internal-error`. Stage 2 is the one place
this invariant relaxes on purpose: an error there falls back to stage 1's
verdict, not to `drop` -- stage 1 already produced a safe answer, and the
classifier only ever re-judges it.

Secrets are scanned first, before anything leaves this process: the
segments the prompt renders are built from the *redacted* lines, and the
`raw` of the stored record is that same redacted text, line count kept.
A value recognized as a secret is therefore never in the prompt, the
database, the JSONL or the answer -- only the model's `unredact` can
release a line, and only one flagged by entropy alone.
"""

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ulid import ULID

from agentgate.api.schemas import Cost, InspectRequest, InspectVerdict, Span
from agentgate.domain.dialogue import Dialogue
from agentgate.domain.inspect_cache import InspectCache
from agentgate.domain.policy import Policy
from agentgate.engine.inspection import Inspection
from agentgate.engine.timings import Timings
from agentgate.inspect.classify import InspectCase, InspectClassifier, InspectOutcome
from agentgate.inspect.detectors import Action, Detector, Finding, scan
from agentgate.inspect.mask import Stage1Outcome, apply, redacted_lines
from agentgate.inspect.reconcile import reconcile
from agentgate.inspect.secrets import entropy_candidates_allowed, scan_secrets
from agentgate.inspect.segments import Segments
from agentgate.inspect.segments import build as build_segments
from agentgate.profiles.loader import detect_workspace
from agentgate.profiles.schema import Profile
from agentgate.session.cache_key import inspect_cache_key

log = logging.getLogger(__name__)

_INVISIBLE_RULE = "inspect.invisible"
_SECRET_RULE = "inspect.secret"


@dataclass(frozen=True)
class _Context:
    """Everything resolved from the request before stage 1 runs."""

    policy: Policy
    cache_key: str
    dialogue: Dialogue
    entropy_candidates: bool


@dataclass(frozen=True)
class _Stage2Result:
    """The verdict this inspection currently stands on, and how it got
    there -- seeded from stage 1's `Stage1Outcome` and replaced exactly
    once, by `_run_stage2`, if stage 2 runs and answers.
    """

    verdict: InspectVerdict
    replacement: str | None
    reason: str
    stage: int
    rule_id: str | None
    model: str | None
    error: str | None
    cost: Cost | None = None
    spans: tuple[Span, ...] = ()
    redacted: int = 0
    spans_rejected: int = 0

    @classmethod
    def from_stage1(cls, outcome: Stage1Outcome) -> "_Stage2Result":
        return cls(
            verdict=outcome.verdict, replacement=outcome.replacement, reason=outcome.reason,
            stage=1, rule_id=outcome.rule_id, model=None, error=None, spans=outcome.spans, redacted=outcome.redacted,
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
        policy, cache_key, dialogue = resolved.policy, resolved.cache_key, resolved.dialogue

        hit = await self._cached(cache_key)
        if hit is not None:
            return hit.as_cached(inspection_id, request, timings.finish(), workspace)

        try:
            with timings.stage(1):
                lines = request.output.split("\n")
                findings = self._scan(request, policy, resolved.entropy_candidates)
                outcome = apply(request.output, findings)
                redacted = redacted_lines(lines, findings)
        except Exception:  # noqa: BLE001 - a detector bug must read as drop, never as pass
            log.exception("inspect stage 1 raised")
            return self._refuse(
                inspection_id, request, timings, profile_id, policy.profile_hash, workspace,
                "api.internal-error", "internal error", error="unexpected",
            )

        result = _Stage2Result.from_stage1(outcome)
        if self._should_classify(policy, findings):
            with timings.stage(2):
                segments = build_segments(redacted, findings, policy.inspect.model_budget)
                result = await self._run_stage2(
                    request, profile_id, policy, dialogue, findings, outcome, segments, result,
                )

        inspection = Inspection(
            id=inspection_id, ts=datetime.now(timezone.utc), request=request, verdict=result.verdict,
            latency=timings.finish(), profile_id=profile_id, profile_hash=policy.profile_hash,
            replacement=result.replacement, reason=result.reason, stage=result.stage, rule_id=result.rule_id,
            model=result.model, error=result.error, findings=tuple(f.rule_id for f in findings), workspace=workspace,
            cost=result.cost,
            spans=result.spans, redacted=result.redacted, spans_rejected=result.spans_rejected,
            redacted_output="\n".join(redacted) if any(f.action is Action.redact for f in findings) else None,
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
            dialogue = Dialogue.of(request.history)
            intent = request.user_request or dialogue.last_human_request() or ""
            candidates = entropy_candidates_allowed(request.provenance, workspace)
            cache_key = inspect_cache_key(
                policy.profile_hash, request.provenance.kind, _digest(request.output), _digest(intent),
                dialogue.digest(), candidates,
            )
            return _Context(policy=policy, cache_key=cache_key, dialogue=dialogue, entropy_candidates=candidates)
        except Exception:  # noqa: BLE001 - a bug resolving the policy must read as drop, never as pass
            log.exception("inspect prelude raised")
            return self._refuse(
                inspection_id, request, timings, profile_id, "", workspace, "api.internal-error", "internal error",
                error="unexpected",
            )

    def _scan(self, request: InspectRequest, policy: Policy, entropy_candidates: bool) -> list[Finding]:
        """Stage 1's findings, secrets first: a value hidden here is hidden
        everywhere downstream, whatever a detector or the model says about
        the same line (`mask.resolve` ranks `redact` above the rest)."""
        findings: list[Finding] = []
        if policy.inspect.secrets == "on":
            findings.extend(scan_secrets(request.output, entropy_candidates=entropy_candidates))
        findings.extend(scan(request.output, self._detectors))
        return findings

    async def _run_stage2(
        self, request: InspectRequest, profile_id: str, policy: Policy, dialogue: Dialogue,
        findings: list[Finding], outcome: Stage1Outcome, segments: Segments, result: _Stage2Result,
    ) -> _Stage2Result:
        classified = await self._classify(request, profile_id, policy, dialogue, findings, outcome, segments)
        if classified.error is not None:
            return replace(result, error=classified.error, model=classified.model)
        merged = reconcile(request.output, findings, outcome, classified, segments, policy.inspect.model_budget)
        if merged.error is not None:
            # A `mask` with nothing to apply is a stage-2 error: stage 1's
            # verdict stands, the rejection count is still worth recording.
            return replace(result, error=merged.error, model=classified.model, spans_rejected=merged.spans_rejected)
        return replace(
            result, verdict=merged.verdict, replacement=merged.replacement, reason=merged.reason,
            rule_id=merged.rule_id, stage=2, model=classified.model, cost=classified.cost,
            spans=merged.spans, redacted=merged.redacted, spans_rejected=merged.spans_rejected,
        )

    def _should_classify(self, policy: Policy, findings: list[Finding]) -> bool:
        mode = policy.inspect.classifier
        if mode == "off":
            return False
        if mode == "always":
            # Unconditional on purpose: a zero-width character or a token
            # in the output must not switch the semantic check off.
            return True
        # on-flag: something the model can actually re-judge -- not an
        # invisible-character cleanup, not a secret recognized by form.
        return any(_asks_the_model(f) for f in findings)

    async def _classify(
        self, request: InspectRequest, profile_id: str, policy: Policy, dialogue: Dialogue,
        findings: list[Finding], outcome: Stage1Outcome, segments: Segments,
    ) -> InspectOutcome:
        classifier = self._classifier_for(profile_id, policy)
        if classifier is None:
            return InspectOutcome(verdict=outcome.verdict, reason=outcome.reason, model=None, error="unknown-model")
        case = InspectCase.build(request, dialogue, policy, findings, outcome, segments)
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


def _asks_the_model(finding: Finding) -> bool:
    """Whether this finding leaves the model anything to re-judge.

    Invisible characters are cleaned, never softened, and a secret matched
    by form is not the model's call at all -- only a candidate flagged by
    entropy alone is, since only that one can be released.
    """
    if finding.rule_id == _INVISIBLE_RULE:
        return False
    if finding.rule_id == _SECRET_RULE:
        return finding.candidate_key is not None
    return True


def _digest(output: str) -> str:
    return hashlib.sha256(output.encode("utf-8", "surrogatepass")).hexdigest()
