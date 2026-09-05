"""The inspect pipeline: cache -> detectors -> mask -> (classifier, task 9).

`drop` is the fail-closed answer here: there is no human to ask about a
result that already exists, and passing it unjudged is the one thing this
route must never do. A cache that fails means no cache; a detector that
raises means `drop` with `api.internal-error`.
"""

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone

from ulid import ULID

from agentgate.api.schemas import InspectRequest, InspectVerdict
from agentgate.domain.inspect_cache import InspectCache, inspect_cache_key
from agentgate.domain.policy import Policy
from agentgate.engine.inspection import Inspection
from agentgate.engine.timings import Timings
from agentgate.inspect.detectors import Detector, scan
from agentgate.inspect.mask import apply
from agentgate.profiles.loader import detect_workspace
from agentgate.profiles.schema import Profile

log = logging.getLogger(__name__)


class Inspector:
    def __init__(
        self,
        profiles: Mapping[str, Profile],
        default_profile: str,
        detectors: tuple[Detector, ...],
        cache: InspectCache,
        ttl_seconds: int = 86400,
        classifiers: Mapping[str, Mapping[str, object]] | None = None,
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
                outcome = apply(request.output, scan(request.output, self._detectors))
        except Exception:  # noqa: BLE001 - a detector bug must read as drop, never as pass
            log.exception("inspect stage 1 raised")
            return self._refuse(
                inspection_id, request, timings, profile_id, policy.profile_hash,
                "api.internal-error", "internal error", error="unexpected",
            )

        inspection = Inspection(
            id=inspection_id, ts=datetime.now(timezone.utc), request=request, verdict=outcome.verdict,
            latency=timings.finish(), profile_id=profile_id, profile_hash=policy.profile_hash,
            replacement=outcome.replacement, reason=outcome.reason, stage=1, rule_id=outcome.rule_id,
        )
        await self._remember(key, inspection)
        return inspection

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
