"""The decision pipeline.

Fixed order: resolve the profile and the model -> normalize -> allow-cache
lookup -> stage 1 rules -> stage 2 classifier -> escalation -> session
state. Every step either produces a Verdict or hands the call to the next
one, and `decide` returns one immutable Decision.

Fail-closed is the spine: an unknown profile or model resolves to `ask`
before anything else runs, and stage 2 turns every classifier failure into
`ask` itself (see agentgate.stage2.run), so nothing here produces `allow`
on an error path.

Persistence is not this module's concern -- see agentgate.store.writer.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from ulid import ULID

from agentgate.api.schemas import DecideRequest, DecisionKind
from agentgate.domain.verdict import Verdict
from agentgate.engine.decision import Decision
from agentgate.engine.timings import Timings
from agentgate.normalize import normalize
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.loader import with_workspace
from agentgate.profiles.schema import ModelConfig, Profile
from agentgate.session.cache_key import allow_cache_key
from agentgate.session.escalation import should_escalate
from agentgate.session.state import SessionState, SessionStateStore
from agentgate.stage1.chain import run_stage1
from agentgate.stage2.client import LLMClient
from agentgate.stage2.run import run_stage2

log = logging.getLogger(__name__)

STAGE1_PASSED = "passed: no hard-deny match, not in allowlist"
STAGE1_SKIPPED = "skipped: command unparseable"


@dataclass(frozen=True)
class _Context:
    """Everything resolved from the request before any rule runs."""

    profile: Profile
    profile_hash: str
    profile_id: str
    model_name: str
    model_config: ModelConfig
    state: SessionState | None


class Gate:
    def __init__(
        self,
        profiles: dict[str, Profile],
        default_profile: str,
        state_store: SessionStateStore,
        http: httpx.AsyncClient,
        allow_cache_ttl_seconds: int = 86400,
    ) -> None:
        self._profiles = profiles
        self._default_profile = default_profile
        self._states = state_store
        self._http = http
        self._allow_cache_ttl_seconds = allow_cache_ttl_seconds

    async def decide(self, request: DecideRequest) -> Decision:
        timings = Timings()
        decision_id = str(ULID())
        profile_id = request.profile_id or self._default_profile

        resolved = await self._resolve(request, profile_id)
        if isinstance(resolved, Verdict):
            return self._finish(decision_id, request, resolved, timings, profile_id, "")

        action = normalize(request)
        cache_key = allow_cache_key(
            resolved.profile_hash, action.action_hash(), request.user_request
        )
        if await self._cache_hit(resolved, cache_key):
            return self._finish(
                decision_id, request, Verdict.allow("cache", stage=0), timings,
                profile_id, resolved.profile_hash, action, resolved.state, cache_key, cached=True,
            )

        verdict = await self._evaluate(request, action, resolved, timings)
        verdict = self._escalate(resolved.state, resolved.profile, verdict)
        await self._settle_session(resolved.state, verdict, cache_key, decision_id)
        return self._finish(
            decision_id, request, verdict, timings, profile_id, resolved.profile_hash,
            action, resolved.state, cache_key,
        )

    async def _resolve(self, request: DecideRequest, profile_id: str) -> "_Context | Verdict":
        base = self._profiles.get(profile_id)
        if base is None:
            return Verdict.ask("api.unknown-profile", f"unknown profile '{profile_id}'", stage=0)
        try:
            model_name, model_config = base.models.model_config_for(request.model)
        except KeyError:
            return Verdict.ask("api.unknown-model", f"unknown model '{request.model}'", stage=0)

        profile = with_workspace(base, request.args.cwd)
        state = None
        if request.session_id:
            state = await self._states.get_or_create(
                request.session_id, request.harness, profile_id,
                profile.workspace or request.args.cwd,
            )
        return _Context(
            profile=profile, profile_hash=profile.profile_hash(), profile_id=profile_id,
            model_name=model_name, model_config=model_config, state=state,
        )

    async def _cache_hit(self, context: _Context, cache_key: str) -> bool:
        if context.state is None:
            return False
        return await self._states.cache_get(context.state.session_id, cache_key) is not None

    async def _evaluate(
        self, request: DecideRequest, action: NormalizedAction, context: _Context, timings: Timings
    ) -> Verdict:
        with timings.stage(1):
            verdict = None if action.flags.unparseable else run_stage1(action, context.profile)
        if verdict is not None:
            return verdict
        note = STAGE1_SKIPPED if action.flags.unparseable else STAGE1_PASSED
        with timings.stage(2):
            client = LLMClient(context.model_name, context.model_config, self._http)
            return await run_stage2(
                action, request.user_request, context.profile, context.model_name, client, note
            )

    def _escalate(self, state: SessionState | None, profile: Profile, verdict: Verdict) -> Verdict:
        if state is None or verdict.hard or verdict.decision is DecisionKind.ask:
            return verdict
        if not should_escalate(state, profile.escalation):
            return verdict
        hits = state.deny_consecutive
        state.reset_after_escalation()
        return verdict.escalated(hits)

    async def _settle_session(
        self, state: SessionState | None, verdict: Verdict, cache_key: str, decision_id: str
    ) -> None:
        if state is None:
            return
        state.record(verdict.decision)
        await self._states.save(state)
        if verdict.decision is DecisionKind.allow:
            await self._states.cache_put(
                state.session_id, cache_key, decision_id, self._allow_cache_ttl_seconds
            )

    def _finish(
        self, decision_id: str, request: DecideRequest, verdict: Verdict, timings: Timings,
        profile_id: str, profile_hash: str, action: NormalizedAction | None = None,
        state: SessionState | None = None, cache_key: str | None = None, cached: bool = False,
    ) -> Decision:
        return Decision(
            id=decision_id, ts=datetime.now(timezone.utc), request=request, verdict=verdict,
            latency=timings.finish(), profile_id=profile_id, profile_hash=profile_hash,
            action=action, state=state, cache_key=cache_key, cached=cached,
        )
