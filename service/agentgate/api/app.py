"""The HTTP API: POST /v1/decide, and the read endpoints.

The single most important property of this module: `POST /v1/decide` returns
HTTP 200 for every decision outcome (`allow`, `deny`, `ask`). The only
non-200 status it can return is 401 from the auth dependency. That holds for
two extra failure paths beyond the pipeline's own three outcomes:

- an invalid request body (malformed JSON, or JSON that fails
  `DecideRequest` validation) -> 200 `ask`, stage 0, rule_id
  "api.invalid-request". The route takes a raw `Request` rather than a
  `DecideRequest` parameter specifically so FastAPI's automatic body
  validation (which would raise `RequestValidationError` -> 422) never
  triggers; the body is parsed and validated by hand instead, and both
  failure modes are caught explicitly.
- any exception escaping `Gate.decide` -> 200 `ask`, rule_id
  "api.internal-error". `Gate.decide` has no top-level try/except of its own
  (see agentgate.pipeline's docstring) -- this handler is the outermost
  fail-closed boundary that converts any escape (a bug in normalize, a
  synchronous store error, anything) into `ask`/200. `allow` on error is not
  reachable through this path.

Persistence (the Postgres decision/session rows and the JSONL line) happens
*after* the response is sent, via `BackgroundTasks.add_task`, and a
persistence failure is logged and swallowed -- it must never surface to a
client that already has its answer. Within that persist step, the session
row is written before the decision row (`decisions.session_id` is a foreign
key to `sessions.id` -- see agentgate.store.repo.DecisionRepo's docstring),
so a failed session upsert also means the decision row and the allow-cache
row are skipped for this call: better a decision missing from the feed than
a swallowed IntegrityError silently dropping it anyway.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from ulid import ULID

from agentgate.api.deps import make_require_token
from agentgate.api.schemas import DecideRequest, DecideResponse, DecisionKind, LatencyMs
from agentgate.config import Settings
from agentgate.log.jsonl import JsonlLogger
from agentgate.pipeline import Gate
from agentgate.profiles.schema import Profile
from agentgate.session.state import SessionState
from agentgate.store.repo import DecisionRecord

log = logging.getLogger(__name__)

# The allow-cache TTL used when restoring the persistent (Postgres) allow
# cache row after a call -- matches Gate's own default `cache_ttl_seconds`
# (agentgate.pipeline.Gate.__init__), since __main__.build_app constructs
# Gate without overriding it.
_CACHE_TTL_SECONDS = 86400


def _ask(rule_id: str, reason: str) -> DecideResponse:
    return DecideResponse(
        decision=DecisionKind.ask, reason=reason, stage=0, rule_id=rule_id, model=None,
        latency_ms=LatencyMs(stage1=None, stage2=None, total=0), decision_id=str(ULID()),
    )


def create_app(
    settings: Settings,
    gate: Gate,
    decision_repo,
    session_repo,
    profiles: dict[str, Profile],
    jsonl: JsonlLogger,
    db_probe: Callable[[], Awaitable[bool]] | None = None,
    key_repo=None,
) -> FastAPI:
    app = FastAPI(title="AgentGate", version="0.1.0")
    auth = Depends(make_require_token(settings, key_repo=key_repo, cache_ttl_seconds=settings.api_key_cache_ttl_seconds))

    async def persist(rec: DecisionRecord, state: SessionState | None) -> None:
        # rec.to_dict() carries the field as "id" (DecisionRecord's own
        # field name); the API's public shape calls it "decision_id"
        # everywhere else (the response body, /v1/decisions items) -- keep
        # the JSONL line consistent with that so a client correlating a
        # decide response against the log by "decision_id" can do so.
        jsonl.write(dict(rec.to_dict(), decision_id=rec.id))
        try:
            if session_repo is not None and state is not None:
                await session_repo.upsert(state)
            if decision_repo is not None:
                await decision_repo.insert(rec)
            if session_repo is not None and state is not None and rec.decision == "allow" and not rec.cached:
                cache_key = rec.normalized.get("cache_key") or rec.id
                await session_repo.cache_put(
                    state.session_id, cache_key, rec.id,
                    datetime.now(timezone.utc) + timedelta(seconds=_CACHE_TTL_SECONDS),
                )
        except Exception as exc:  # noqa: BLE001 - persistence must never affect a response already sent
            log.error("persist failed for decision %s: %s", rec.id, exc)

    @app.post("/v1/decide", response_model=DecideResponse, dependencies=[auth])
    async def decide(request: Request, background: BackgroundTasks) -> DecideResponse:
        try:
            payload = await request.json()
        except ValueError:
            return _ask("api.invalid-request", "request body is not valid JSON")
        try:
            req = DecideRequest.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(x) for x in first.get("loc", ()))
            return _ask("api.invalid-request", f"invalid request: {loc}: {first.get('msg')}")
        try:
            resp, rec, state = await gate.decide(req)
        except Exception as exc:  # noqa: BLE001 - fail-closed: no exception from decide() may escape as a 500
            log.exception("Gate.decide failed")
            return _ask("api.internal-error", f"internal error: {type(exc).__name__}")
        background.add_task(persist, rec, state)
        return resp

    @app.get("/v1/decisions", dependencies=[auth])
    async def decisions(
        session_id: str | None = None,
        model: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        before: str | None = None,
    ) -> dict:
        if decision_repo is None:
            return {"items": [], "next_before": None}
        rows = await decision_repo.list(session_id=session_id, model=model, limit=limit, before=before)
        items = [dict(r.to_dict(), decision_id=r.id) for r in rows]
        next_before = rows[-1].id if len(rows) == limit else None
        return {"items": items, "next_before": next_before}

    @app.get("/v1/profiles/{profile_id}", dependencies=[auth])
    async def get_profile(profile_id: str) -> dict:
        p = profiles.get(profile_id)
        if p is None:
            raise HTTPException(status_code=404, detail="profile not found")
        return p.public_dict()

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        db_ok = True
        if db_probe is not None:
            try:
                db_ok = await db_probe()
            except Exception:  # noqa: BLE001 - a broken probe means "not ok", not a 500 from /healthz
                db_ok = False
        status = "ok" if db_ok else "degraded"
        return JSONResponse({"status": status, "db": db_ok, "llm": None}, status_code=200)

    return app
