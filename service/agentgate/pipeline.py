"""The decision pipeline: assembles tasks 2-9 into Gate.decide().

Pipeline order (fixed): profile resolution (unknown profile_id -> ask, stage
0, "api.unknown-profile") -> unknown model -> ask ("api.unknown-model") ->
with_workspace -> normalize -> allow-cache lookup -> stage 1 -> (if None, or
the action is unparseable) stage 2 -> escalation (only when the decision is
not a hard stage-1 deny) -> session-state write -> allow-cache put ->
DecisionRecord.

Fail-closed is the spine: every error path here resolves to `ask` with the
decision still recorded; nothing produces `allow` on error, and no exception
from a downstream stage is allowed to escape `decide()` unhandled. Stage 2's
own exception handling (agentgate.stage2.run.run_stage2) already turns every
LLM failure into DecisionKind.ask, so this module does not need its own
try/except around that call.

Persistence happens strictly *after* the response is built (see `_do_persist`)
so a database write is never awaited on the path that produces the decision,
and a `persist` failure is swallowed (logged) rather than surfacing to the
caller. Ordering the write itself (session row before decision row, decision
row before allow-cache row) is the injected `persist` callable's
responsibility -- see agentgate.store.repo.DecisionRepo/SessionRepo
docstrings for the FK constraints that ordering exists to satisfy.

The `[STAGE1]` line handed to the stage-2 prompt is one of two fixed
strings, never Verdict.reason/suggest: those interpolate
action-derived text (a path, a domain, ...) and letting attacker-reachable
text onto that line would forge a fake prompt section (see
agentgate.stage2.prompt's docstring and the task 7 review that closed this).
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import httpx
from ulid import ULID

from agentgate.api.schemas import DecideRequest, DecideResponse, DecisionKind
from agentgate.engine.timings import Timings
from agentgate.normalize import normalize
from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.loader import with_workspace
from agentgate.profiles.schema import Profile
from agentgate.session.cache_key import allow_cache_key
from agentgate.session.escalation import should_escalate
from agentgate.session.state import SessionState, SessionStateStore
from agentgate.stage1.chain import run_stage1
from agentgate.stage2.client import LLMClient
from agentgate.stage2.run import run_stage2
from agentgate.store.repo import DecisionRecord

log = logging.getLogger(__name__)

Persist = Callable[[DecisionRecord, SessionState | None], Awaitable[None]]

# Fixed vocabulary for the [STAGE1] prompt line -- never Verdict.reason
# or .suggest, which interpolate action-derived text (see module docstring).
_NOTE_PASSED = "passed: no hard-deny match, not in allowlist"
_NOTE_SKIPPED = "skipped: command unparseable"


class Gate:
    def __init__(
        self,
        profiles: dict[str, Profile],
        default_profile: str,
        state_store: SessionStateStore,
        http: httpx.AsyncClient,
        persist: Persist | None = None,
        cache_ttl_seconds: int = 86400,
    ) -> None:
        self._profiles = profiles
        self._default_profile = default_profile
        self._states = state_store
        self._http = http
        self._persist = persist
        self._cache_ttl = cache_ttl_seconds

    async def decide(self, req: DecideRequest) -> tuple[DecideResponse, DecisionRecord, SessionState | None]:
        timings = Timings()
        decision_id = str(ULID())
        profile_id = req.profile_id or self._default_profile
        base_profile = self._profiles.get(profile_id)
        if base_profile is None:
            return await self._finish_early(
                req, decision_id, timings, profile_id, "", "api.unknown-profile", f"unknown profile '{profile_id}'"
            )
        try:
            model_name, model_cfg = base_profile.models.model_config_for(req.model)
        except KeyError:
            return await self._finish_early(
                req, decision_id, timings, profile_id, base_profile.profile_hash(),
                "api.unknown-model", f"unknown model '{req.model}'",
            )

        profile = with_workspace(base_profile, req.args.cwd)
        profile_hash = profile.profile_hash()
        action = normalize(req)

        state: SessionState | None = None
        if req.session_id:
            state = await self._states.get_or_create(req.session_id, req.harness, profile_id, profile.workspace or req.args.cwd)

        cache_key = allow_cache_key(profile_hash, action.action_hash(), req.user_request)
        if state is not None:
            cached_id = await self._states.cache_get(state.session_id, cache_key)
            if cached_id is not None:
                latency = timings.finish()
                resp = DecideResponse(
                    decision=DecisionKind.allow, stage=0, rule_id="cache", model=None,
                    latency_ms=latency.to_schema(), cached=True,
                    decision_id=decision_id,
                )
                rec = self._record(
                    req, decision_id, action, profile_id, profile_hash, resp, None, None, None,
                    latency.total_ms, cache_key=cache_key,
                )
                await self._do_persist(rec, None)
                return resp, rec, state

        with timings.stage(1):
            verdict = None if action.flags.unparseable else run_stage1(action, profile)
        if verdict is None:
            note = _NOTE_SKIPPED if action.flags.unparseable else _NOTE_PASSED
            with timings.stage(2):
                client = LLMClient(model_name, model_cfg, self._http)
                verdict = await run_stage2(action, req.user_request, profile, model_name, client, note)

        if state is not None and not verdict.hard and verdict.decision is not DecisionKind.ask \
                and should_escalate(state, profile.escalation):
            verdict = verdict.escalated(state.deny_consecutive)
            state.reset_after_escalation()

        if state is not None:
            state.record(verdict.decision)
            await self._states.save(state)
            if verdict.decision is DecisionKind.allow:
                await self._states.cache_put(state.session_id, cache_key, decision_id, self._cache_ttl)

        latency = timings.finish()
        resp = DecideResponse(
            decision=verdict.decision, reason=verdict.reason, suggest=verdict.suggest, stage=verdict.stage,
            rule_id=verdict.rule_id, model=verdict.model, latency_ms=latency.to_schema(),
            cached=False, decision_id=decision_id,
        )
        rec = self._record(
            req, decision_id, action, profile_id, profile_hash, resp, verdict.raw_response, verdict.error,
            latency.stage1_ms, latency.total_ms, latency.stage2_ms, cache_key=cache_key,
        )
        await self._do_persist(rec, state)
        return resp, rec, state

    async def _finish_early(
        self, req: DecideRequest, decision_id: str, timings: Timings, profile_id: str, profile_hash: str,
        rule_id: str, reason: str,
    ) -> tuple[DecideResponse, DecisionRecord, None]:
        latency = timings.finish()
        resp = DecideResponse(
            decision=DecisionKind.ask, reason=reason, stage=0, rule_id=rule_id, model=None,
            latency_ms=latency.to_schema(), decision_id=decision_id,
        )
        action = normalize(req)
        rec = self._record(
            req, decision_id, action, profile_id, profile_hash, resp, None, rule_id, None, latency.total_ms
        )
        await self._do_persist(rec, None)
        return resp, rec, None

    def _record(
        self, req: DecideRequest, decision_id: str, action: NormalizedAction, profile_id: str, profile_hash: str,
        resp: DecideResponse, raw_resp: dict | None, error: str | None, stage1_ms: int | None, total: int,
        stage2_ms: int | None = None, cache_key: str | None = None,
    ) -> DecisionRecord:
        # cache_key lets the store restore allow_cache after a restart (Task 11).
        normalized = dict(action.to_dict(), cache_key=cache_key)
        return DecisionRecord(
            id=decision_id, session_id=req.session_id, ts=datetime.now(timezone.utc), harness=req.harness,
            tool=req.tool.value, raw=req.raw, normalized=normalized, user_request=req.user_request,
            profile_id=profile_id, profile_hash=profile_hash, decision=resp.decision.value, reason=resp.reason,
            suggest=resp.suggest, stage=resp.stage, rule_id=resp.rule_id, model=resp.model,
            model_raw_response=raw_resp, latency_stage1_ms=stage1_ms, latency_stage2_ms=stage2_ms,
            latency_total_ms=total, error=error, cached=resp.cached, metadata=req.metadata,
        )

    async def _do_persist(self, rec: DecisionRecord, state: SessionState | None) -> None:
        if self._persist is None:
            return
        try:
            await self._persist(rec, state)
        except Exception:  # noqa: BLE001 - a persistence failure must not affect the decision already returned
            log.exception("persist failed for decision %s", rec.id)
