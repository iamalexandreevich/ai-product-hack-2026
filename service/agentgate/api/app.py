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
  (see agentgate.engine.gate's docstring) -- this handler is the outermost
  fail-closed boundary that converts any escape (a bug in normalize, a
  synchronous store error, anything) into `ask`/200. `allow` on error is not
  reachable through this path.

Persistence (the Postgres decision/session rows and the JSONL line) happens
*after* the response is sent, via `BackgroundTasks.add_task`, delegated to
the injected `DecisionWriter` -- see agentgate.store.writer. A write failure
there is logged and swallowed by the writer itself; it must never surface to
a client that already has its answer.
"""

import logging
from collections.abc import Awaitable, Callable

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from ulid import ULID

from agentgate.api.deps import make_require_token
from agentgate.api.schemas import DecideRequest, DecideResponse, LatencyMs
from agentgate.config import Settings
from agentgate.domain.verdict import Verdict
from agentgate.engine.gate import Gate
from agentgate.profiles.schema import Profile
from agentgate.store.writer import DecisionWriter

log = logging.getLogger(__name__)


def _refuse(rule_id: str, reason: str) -> DecideResponse:
    verdict = Verdict.ask(rule_id, reason, stage=0)
    return DecideResponse(
        decision=verdict.decision, reason=verdict.reason, stage=verdict.stage,
        rule_id=verdict.rule_id, model=None,
        latency_ms=LatencyMs(stage1=None, stage2=None, total=0), decision_id=str(ULID()),
    )


def create_app(
    settings: Settings,
    gate: Gate,
    writer: DecisionWriter,
    decision_repo,
    profiles: dict[str, Profile],
    db_probe: Callable[[], Awaitable[bool]] | None = None,
    key_repo=None,
) -> FastAPI:
    app = FastAPI(title="AgentGate", version="0.1.0")
    auth = Depends(make_require_token(settings, key_repo=key_repo, cache_ttl_seconds=settings.api_key_cache_ttl_seconds))

    @app.post("/v1/decide", response_model=DecideResponse, dependencies=[auth])
    async def decide(request: Request, background: BackgroundTasks) -> DecideResponse:
        try:
            payload = await request.json()
        except ValueError:
            return _refuse("api.invalid-request", "request body is not valid JSON")
        try:
            parsed = DecideRequest.model_validate(payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            return _refuse("api.invalid-request", f"invalid request: {location}: {first.get('msg')}")
        try:
            decision = await gate.decide(parsed)
        except Exception as exc:  # noqa: BLE001 - fail-closed: no exception may escape as a 500
            log.exception("Gate.decide failed")
            return _refuse("api.internal-error", f"internal error: {type(exc).__name__}")
        background.add_task(writer.write, decision)
        return decision.to_response()

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
                log.warning("database probe failed", exc_info=True)
                db_ok = False
        status = "ok" if db_ok else "degraded"
        return JSONResponse({"status": status, "db": db_ok, "llm": None}, status_code=200)

    return app
