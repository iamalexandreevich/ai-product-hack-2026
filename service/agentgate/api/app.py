"""The HTTP API: POST /v1/decide, and the read endpoints.

The single most important property of this module: `POST /v1/decide` returns
HTTP 200 for every decision outcome (`allow`, `deny`, `ask`). The only
non-200 status it can return is 401 from the auth dependency. That holds for
two extra failure paths beyond the pipeline's own three outcomes:

- an invalid request body (malformed JSON, or JSON that fails
  `DecideRequest` validation) -> 200 `ask`, stage 0, rule_id
  "api.invalid-request" ("api.unsupported-protocol" for an unknown `protocol`,
  "api.history-too-large" for `history` over its turn or byte limit). The
  route takes a raw `Request` rather than a `DecideRequest` parameter
  specifically so FastAPI's automatic body validation (which would raise
  `RequestValidationError` -> 422) never triggers; the body is parsed and
  validated by hand instead, and both failure modes are caught explicitly.
- any exception escaping `Gate.decide` -> 200 `ask`, rule_id
  "api.internal-error". `Gate.decide` has no top-level try/except of its own
  (see agentgate.engine.gate's docstring) -- this handler is the outermost
  fail-closed boundary that converts any escape (a bug in normalize, a
  synchronous store error, anything) into `ask`/200. `allow` on error is not
  reachable through this path.

Persistence of the decision (the Postgres row and the JSONL line) happens
*after* the response is sent, via `BackgroundTasks.add_task`, delegated to
the injected `DecisionWriter` -- see agentgate.store.writer. A write failure
there is logged and swallowed by the writer itself; it must never surface to
a client that already has its answer.

This module is also the source of the published contract: the summaries and
descriptions on the routes below are the ones contracts/openapi.yaml carries.
FastAPI renders the document out of the routes and the models and
scripts/export_openapi.py only writes it to disk, so the document cannot
describe an endpoint the service does not serve.
"""

import importlib.metadata
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Annotated, Any, Literal, Protocol

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Path, Query, Request
from pydantic import ValidationError
from ulid import ULID

from agentgate.api.deps import make_require_token
from agentgate.api.examples import (
    INSPECT_REQUEST_EXAMPLES,
    INSPECT_RESPONSE_EXAMPLES,
    REQUEST_EXAMPLES,
    RESPONSE_EXAMPLES,
)
from agentgate.api.openapi import install_openapi
from agentgate.api.responses import DecisionListResponse, Error, Health
from agentgate.api.schemas import (
    IDEMPOTENCY_KEY_MAX_CHARS,
    METADATA_MAX_BYTES,
    OUTPUT_MAX_BYTES,
    PROTOCOL,
    RAW_MAX_BYTES,
    USER_REQUEST_MAX_CHARS,
    DecideRequest,
    DecideResponse,
    HistoryTooLarge,
    InspectRequest,
    InspectResponse,
    InspectVerdict,
    LatencyMs,
    OutputTooLarge,
    RulesTooLarge,
    UnsupportedProtocol,
    UnsupportedRules,
)
from agentgate.config import Settings
from agentgate.domain.replay import Replay, ReplayKey, ReplayStore
from agentgate.domain.verdict import Verdict
from agentgate.engine.gate import Gate
from agentgate.engine.inspector import Inspector
from agentgate.profiles.schema import Profile
from agentgate.store.keys import ApiKeyRepo
from agentgate.store.repo import DecisionRepo
from agentgate.store.writer import DecisionWriter

log = logging.getLogger(__name__)

API_DESCRIPTION = f"""\
AgentGate is a harness-agnostic gate for coding-agent actions. A harness calls
`POST /v1/decide` from its pre-tool-use hook with one proposed action and the
user's last message; the service answers `allow`, `deny` or `ask` after a
two-stage cascade (deterministic stage 1, then an LLM classifier as stage 2).

## The five things an integrator must get right

**1. Every decision is HTTP 200 — including refusals.** `deny` and `ask` are
values of `decision` in the response body, never HTTP error statuses. A client
that treats a non-2xx status as "denied" misreads this contract. The only
non-200 status the service returns on purpose is `401` for a missing or wrong
bearer token.

**2. The service is fail-closed: every failure resolves to `ask`.** An invalid
request body, an unknown `tool`, an exceeded size limit, an unparseable
command, an LLM timeout, an LLM error, an invalid LLM response, or an internal
error — all of them return HTTP 200 with `decision: "ask"` and a `reason`
explaining why. `allow` on error is impossible by design. A client must never
translate a transport failure, a timeout or an unparseable response into
`allow`; treat anything you cannot parse as `ask` and put the action in front
of the user.

**3. `deny` always carries `reason` and `suggest`.** There is no separate
"deny but continue" decision. On `deny` the caller renders `reason` back to the
model as the tool result (the wording template lives in
`contracts/deny_message_template.md`) and shows `suggest` — the safe
alternative — to the user. `suggest` is an empty string only when there is
nothing to suggest.

**4. Request limits are enforced by the models, and one of them is in bytes.**
`session_id` at most 128 characters; `harness` 1–64 characters; `raw` at most
{RAW_MAX_BYTES} **bytes** of UTF-8 — not characters, so Cyrillic text costs 2
bytes per character and a 20000-character Russian command is already over the
limit; `metadata` at most {METADATA_MAX_BYTES} bytes when serialized as JSON;
`user_request` is not rejected when too long but truncated server-side to
{USER_REQUEST_MAX_CHARS} characters **keeping the tail**, because the end of the
message is the part that describes the current task. Violating a hard limit is
not an error status — per rule 2 it comes back as `ask`.

**5. Policy lives on the server.** A harness knows only the service URL, the
bearer token and optionally a `profile_id`. It never sees, uploads or caches
policy. Two consequences: `args.paths` and `args.domains` are **ignored for
`tool: shell`** (the service extracts paths and domains itself from the
command's AST, so a client that sends them is not wrong, just ignored), and
`raw` is **required and non-blank for `tool: shell`** because the raw command
line is what stage 1 parses.

## Inspect (v3)

`POST /v1/inspect` judges one tool result, held back from the model until the
service answers. There are three verdicts: `pass` (the result reaches the
model unchanged), `mask` (the authoritative rewrite in `output` replaces it),
and `drop` (the result is withheld entirely). Unlike `/v1/decide`, the
fail-closed answer here is `drop`, not `ask` — there is no human to ask about
a result that already happened, and passing it through unjudged is the one
outcome this route must never produce. This is also why an adapter that
cannot reach the service should treat that as fail-open on purpose: showing
the model an unreviewed result beats never showing it one at all, and that
tradeoff is the adapter's to make, not this service's. `output` is capped at
{OUTPUT_MAX_BYTES} bytes of UTF-8; over the limit the call is refused as
`drop` with `rule_id: api.output-too-large`. Judged content is cached by a
digest of its bytes plus the profile, so identical output seen twice is not
re-scanned.

## User rules (v3)

A request may carry `rules` — one client-declared `allow`/`ask`/`deny` list.
Priority for one match, highest first: hard-deny, then the client's own
`deny` rules, then the server profile's own path/domain denials, then the
client's `ask` and `allow` rules, then the rest of stage 1 (the server
allowlist and the packages rule). A client can forbid more than the profile
does but cannot permit what hard-deny or the profile forbids.

## Sessions

`session_id` is optional but load-bearing. With it, the service keeps
per-session counters (consecutive denials, a window over the last 50 outcomes)
that escalate a repeatedly blocked agent to `ask`, and an `allow` cache that
returns a previously granted identical action with `cached: true` and
`stage: 0`. Only `allow` is ever cached; `deny` and `ask` never are. Without a
`session_id` there are no counters and no cache, and the decision is recorded
with a null session.

## History and idempotency (v2)

`history` is the dialogue that preceded the action, oldest turn first, each turn
with a `role` and an `author`; only `author: human` turns count as the user's
words. The service truncates it to the profile budget before it reaches the
stage-2 model and never shows it to stage 1. A repeat of a call with the same
`Idempotency-Key` header replays the stored decision unchanged.

## Implementation status

All five routes are implemented, tested and running. `POST /v1/decide`,
`POST /v1/inspect`, `GET /v1/decisions`, `GET /v1/profiles/{{id}}` and
`GET /healthz` are the settled contract; the request/response schemas below
are generated from the running pydantic models. Integrate against these
shapes.
"""

SERVERS = [
    {
        "url": "http://127.0.0.1:8400",
        "description": "Default local bind (`AGENTGATE_BIND`). One instance in v1.",
    }
]

TAGS = [
    {"name": "decide", "description": "The gate itself. Implemented and contract-stable."},
    {"name": "inspect", "description": "Verdicts on tool results, held back from the model until judged."},
    {"name": "read", "description": "Read/ops endpoints: decision feed, profile read, liveness."},
]

UNAUTHORIZED: dict[str, Any] = {
    "model": Error,
    "description": """\
Missing or invalid bearer token. This is the only status the service returns
on purpose instead of a decision — every other failure is a `200` with
`decision: "ask"`. The body shape is not fixed by the design spec.
""",
}


def _self_parsed_route(ref: str, examples: Mapping[str, Any], key_description: str) -> dict[str, Any]:
    """`openapi_extra` for a route that parses its own body.

    Both `/v1/decide` and `/v1/inspect` take a raw `Request` and validate the
    body by hand (see their docstrings), so FastAPI collects no body schema
    for either and would otherwise document a 422 for the header the auth
    dependency reads. This restores the request shape each route really
    validates and states that a validation failure is never what a caller
    gets back -- it is always a 200 fail-closed answer.
    """
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}, "examples": examples}
            },
        },
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
                "description": key_description,
            }
        ],
        "responses": {"422": None},
    }


DECIDE_OPENAPI: dict[str, Any] = _self_parsed_route(
    "DecideRequest",
    REQUEST_EXAMPLES,
    "Opaque key a harness attaches to one tool call and repeats on a retry. A "
    "repeat with the same key and the same request — a sha256 over everything but "
    "`metadata` — returns the stored decision unchanged, without touching session "
    "counters or storing a second row. A key longer than 128 characters is ignored. "
    "A repeat under the same key with a different request is decided afresh.",
)

INSPECT_OPENAPI: dict[str, Any] = _self_parsed_route(
    "InspectRequest",
    INSPECT_REQUEST_EXAMPLES,
    "Same semantics as on `POST /v1/decide`: a repeat under the same key and "
    "the same request replays the stored verdict unchanged.",
)


class Outcome(Protocol):
    """What `/v1/decide` and `/v1/inspect` both produce: `Decision` and
    `Inspection` share this shape without a common base class."""

    idempotency_key: str | None
    key_id: str | None

    def to_record(self) -> Any: ...
    def to_response(self) -> Any: ...


@dataclass(frozen=True)
class RouteSpec:
    """Everything that differs between `/v1/decide` and `/v1/inspect` once
    the parse -> replay -> run -> remember -> write -> respond shape is
    shared. `refuse` builds this route's fail-closed answer (`ask` or
    `drop`) for a rule id and a reason; `refusal_rules` maps a validation
    exception type to the rule id it earns, scanned in `_refusal_for`;
    `run` is the engine call (`Gate.decide` or `Inspector.inspect`);
    `log_label` names the failing call in the internal-error log line.
    """

    model: type[Any]
    refuse: Callable[[str, str], Any]
    refusal_rules: Mapping[type[Exception], str]
    run: Callable[[Any], Awaitable[Outcome]]
    log_label: str


def _refuse(rule_id: str, reason: str) -> DecideResponse:
    verdict = Verdict.ask(rule_id, reason, stage=0)
    return DecideResponse(
        decision=verdict.decision, reason=verdict.reason, stage=verdict.stage,
        rule_id=verdict.rule_id, model=None,
        latency_ms=LatencyMs(stage1=None, stage2=None, total=0), decision_id=str(ULID()),
    )


def _refuse_inspect(rule_id: str, reason: str) -> InspectResponse:
    return InspectResponse(
        verdict=InspectVerdict.drop, reason=reason, stage=0, rule_id=rule_id, model=None,
        latency_ms=LatencyMs(total=0), decision_id=str(ULID()),
    )


def _reason_for(error: Mapping[str, Any]) -> str:
    location = ".".join(str(part) for part in error.get("loc", ()))
    return f"invalid request: {location}: {error.get('msg')}"


def _refusal_for(errors: list[Any], refuse: Callable[[str, str], Any], rules: Mapping[type[Exception], str]) -> Any:
    """The fail-closed answer to a failed body validation.

    Several limits get their own rule ids so an integrator can tell them from
    a malformed body. They are recognised by the exception type the
    validator raised -- pydantic hands it back under ``ctx.error`` -- rather
    than by the wording of a message, which is free to change. A body can
    fail several fields at once, so every error is scanned, not just the
    first; `rules` maps each recognised exception type to its rule id.
    """
    for error in errors:
        raised = error.get("ctx", {}).get("error")
        rule_id = next((rule_id for exc_type, rule_id in rules.items() if isinstance(raised, exc_type)), None)
        if rule_id is not None:
            return refuse(rule_id, _reason_for(error))
    return refuse("api.invalid-request", _reason_for(errors[0]))


DECIDE_REFUSAL_RULES: Mapping[type[Exception], str] = {
    UnsupportedProtocol: "api.unsupported-protocol",
    HistoryTooLarge: "api.history-too-large",
    UnsupportedRules: "api.unsupported-rules",
    RulesTooLarge: "api.rules-too-large",
}

INSPECT_REFUSAL_RULES: Mapping[type[Exception], str] = {
    OutputTooLarge: "api.output-too-large",
}


async def _replayed(replay: ReplayStore, key: str) -> Replay | None:
    """A replay store that is down means "no replay", never a 500: the client
    would read a 5xx as fail-open and run the action unjudged."""
    try:
        return await replay.get(key)
    except Exception:  # noqa: BLE001 - fail-closed: decide normally instead of failing the call
        log.exception("replay store lookup failed")
        return None


async def _remember(replay: ReplayStore, key: str, entry: Replay, ttl_seconds: int) -> None:
    """A store that cannot keep the answer costs a retry one extra decision,
    which is the price of the answer this call already has."""
    try:
        await replay.put(key, entry, ttl_seconds)
    except Exception:  # noqa: BLE001 - fail-closed: an unstorable decision is still a decision
        log.exception("replay store write failed")


def _replay_key(request: Request) -> str | None:
    key = request.headers.get("idempotency-key", "")
    if not key or len(key) > IDEMPOTENCY_KEY_MAX_CHARS:
        return None
    return key


async def _answer(
    request: Request,
    background: BackgroundTasks,
    spec: RouteSpec,
    replay: ReplayStore,
    settings: Settings,
    writer: DecisionWriter,
    key_id: str | None,
) -> Any:
    """The shape shared by `/v1/decide` and `/v1/inspect`: parse the body,
    refuse a bad one, replay an identical retry, run the engine fail-closed,
    remember the answer under an idempotency key, write it in the
    background, and respond. `spec` supplies everything that differs.
    """
    try:
        payload = await request.json()
    except ValueError:
        return spec.refuse("api.invalid-request", "request body is not valid JSON")
    try:
        parsed = spec.model.model_validate(payload)
    except ValidationError as exc:
        return _refusal_for(exc.errors(), spec.refuse, spec.refusal_rules)
    key = _replay_key(request)
    replay_key = ReplayKey.of(key_id, parsed.session_id, key) if key is not None else None
    if replay_key is not None:
        replayed = await _replayed(replay, replay_key.storage_key())
        if replayed is not None and replayed.answers(parsed, replay_key.principal):
            return replayed.response
    try:
        outcome = await spec.run(parsed)
    except Exception as exc:  # noqa: BLE001 - fail-closed: no exception may escape as a 500
        log.exception("%s failed", spec.log_label)
        return spec.refuse("api.internal-error", f"internal error: {type(exc).__name__}")
    # Attribution is attached before the replay entry is built: Replay.of
    # derives its principal from key_id, so a store restore must recover the
    # same principal the live decision was scoped to, not just the same key.
    outcome = replace(outcome, key_id=key_id)
    if replay_key is not None:
        outcome = replace(outcome, idempotency_key=key)
        await _remember(
            replay, replay_key.storage_key(), Replay.of(outcome.to_record()),
            settings.allow_cache_ttl_seconds,
        )
    background.add_task(writer.write, outcome)
    return outcome.to_response()


def create_app(
    settings: Settings,
    gate: Gate,
    writer: DecisionWriter,
    decision_repo: DecisionRepo,
    profiles: Mapping[str, Profile],
    db_probe: Callable[[], Awaitable[bool]] | None = None,
    key_repo: ApiKeyRepo | None = None,
    *,
    replay: ReplayStore,
    inspector: Inspector,
) -> FastAPI:
    """Assemble the ASGI app around already-built collaborators.

    `replay` and `inspector` are required and keyword-only on purpose: this
    module has no opinion on which implementation of either goes into a
    running service -- that recipe belongs to the composition root
    (`agentgate.bootstrap.build_service`) alone, and a caller assembling its
    own app (a test) must say so explicitly too.
    """
    app = FastAPI(
        title="AgentGate",
        version=importlib.metadata.version("agentgate"),
        summary="Harness-agnostic action gate for coding agents.",
        description=API_DESCRIPTION,
        servers=SERVERS,
        openapi_tags=TAGS,
    )
    auth = Depends(make_require_token(settings, key_repo=key_repo, cache_ttl_seconds=settings.api_key_cache_ttl_seconds))
    # Routes that need the credential's identity take it as a parameter:
    # `dependencies=[auth]` runs the dependency but throws its value away.
    KeyId = Annotated[str | None, auth]
    decide_spec = RouteSpec(
        model=DecideRequest, refuse=_refuse, refusal_rules=DECIDE_REFUSAL_RULES,
        run=gate.decide, log_label="Gate.decide",
    )
    inspect_spec = RouteSpec(
        model=InspectRequest, refuse=_refuse_inspect, refusal_rules=INSPECT_REFUSAL_RULES,
        run=inspector.inspect, log_label="Inspector.inspect",
    )

    @app.post(
        "/v1/decide",
        response_model=DecideResponse,
        operation_id="decide",
        summary="Decide on one proposed agent action",
        tags=["decide"],
        responses={
            200: {
                "description": (
                    "The decision. Returned for `allow`, `deny` and `ask` alike, "
                    "including when the request was invalid or an internal "
                    "failure forced a fail-closed `ask`."
                ),
                "content": {"application/json": {"examples": RESPONSE_EXAMPLES}},
            },
            401: UNAUTHORIZED,
        },
        openapi_extra=DECIDE_OPENAPI,
    )
    async def decide(request: Request, background: BackgroundTasks, key_id: KeyId) -> DecideResponse:
        """Decide on one proposed agent action. This is the call a harness makes from its
        pre-tool-use hook (`PreToolUse` in Claude Code, `tool.execute.before` in
        OpenCode) while the tool call is suspended.

        The service normalizes the action, checks the allow cache, runs deterministic
        stage 1, escalates to the stage-2 LLM classifier if stage 1 did not settle it,
        applies session escalation, and answers. The whole exchange is one round trip
        and one decision; there are no retries and no streaming.

        **Always HTTP 200.** `deny` and `ask` come back as `decision` values with a 200
        status. Invalid bodies, size-limit violations, unparseable commands, LLM
        timeouts and internal errors all come back as HTTP 200 with `decision: "ask"`
        — never as an error status and never as `allow`. The single non-200 the service
        returns on purpose is 401 for a bad bearer token; if you receive anything else
        (a 5xx from a proxy, a connection reset, a timeout), treat it as `ask` and ask
        the user, never as `allow`.

        **Per-tool requirements.** `raw` must be present and non-blank for
        `tool: shell`. `args.paths` and `args.domains` are ignored for `shell` — the
        service derives both from the command's syntax tree. `args.mcp` carries the
        server, tool and arguments for `tool: mcp_call`.

        **Reading the answer.** `stage` says who decided: `1` deterministic, `2` the
        LLM, `0` a cache hit or an API-level refusal. `rule_id` names the stage-1 rule
        (`hard-deny.exfil`, `profile.path`, `allowlist.readonly`, `escalation`, …) and
        is `null` when stage 2 decided. `latency_ms` reports per stage, with `null` for
        a stage that did not run. `decision_id` is a ULID and the primary key of the
        stored decision — quote it in bug reports.

        **v2 fields.** `history` carries the dialogue that preceded the action
        (see the `Turn` schema); it is optional, and an empty history behaves exactly
        like v1. `protocol` names the contract version; this service answers `1` and
        refuses any other value as `ask` with `rule_id: api.unsupported-protocol`. An
        `Idempotency-Key` request header makes a repeat of the same call return the
        same decision (same `decision_id`) without touching session counters or
        storing a second row. The key is opaque to the service, and three of its
        properties are load-bearing: a key longer than 128 characters is ignored
        (the call is decided normally); a repeat under the same key is replayed
        only when the request is byte-for-byte the same request — the identity is
        a sha256 over everything but `metadata`, so a difference anywhere else
        (`args.paths`, `history`, `user_request`, `profile_id`, …) is decided
        afresh; and the key is global to the service, scoped neither by session
        nor by credential, so a harness must make it unique per tool call.

        The request and response examples below are paired by name: `allow_safe_test`,
        `deny_unknown_package`, `ask_uncertain_db_cleanup`. The fourth response example
        has no request counterpart because it shows what an invalid request produces.
        """
        return await _answer(request, background, decide_spec, replay, settings, writer, key_id)

    @app.post(
        "/v1/inspect",
        response_model=InspectResponse,
        operation_id="inspect",
        summary="Judge one tool result",
        tags=["inspect"],
        responses={
            200: {
                "description": (
                    "The verdict. Returned for `pass`, `mask` and `drop` alike, "
                    "including when the request was invalid or an internal "
                    "failure forced a fail-closed `drop`."
                ),
                "content": {"application/json": {"examples": INSPECT_RESPONSE_EXAMPLES}},
            },
            401: UNAUTHORIZED,
        },
        openapi_extra=INSPECT_OPENAPI,
    )
    async def inspect(request: Request, background: BackgroundTasks, key_id: KeyId) -> InspectResponse:
        """Judge one tool result, held back from the model until this call answers.

        **Always HTTP 200.** `pass`, `mask` and `drop` come back as `verdict` values
        with a 200 status. Invalid bodies, oversized output and internal errors all
        come back as HTTP 200 with `verdict: "drop"` -- the fail-closed answer here,
        since there is no human to ask about a result that already happened. The
        single non-200 the service returns on purpose is 401 for a bad bearer token.

        **Reading the answer.** `output` is set and authoritative for `mask`; the
        adapter substitutes it verbatim. `reason` explains a `drop` and is recorded
        for a `mask`. An `Idempotency-Key` request header replays the stored verdict
        for a repeat of the same request, the same way it does on `/v1/decide`.
        """
        return await _answer(request, background, inspect_spec, replay, settings, writer, key_id)

    @app.get(
        "/v1/decisions",
        response_model=DecisionListResponse,
        dependencies=[auth],
        operation_id="listDecisions",
        summary="Decision feed",
        tags=["read"],
        responses={
            200: {"description": "A page of decisions, newest first."},
            401: UNAUTHORIZED,
        },
    )
    async def decisions(
        session_id: Annotated[
            str | None,
            Query(description="Return only decisions of this session."),
        ] = None,
        model: Annotated[
            str | None,
            Query(
                description=(
                    "Return only decisions taken with this model configuration. "
                    "Decisions settled by stage 1 have no model."
                ),
            ),
        ] = None,
        limit: Annotated[
            int, Query(ge=1, le=500, description="Page size, 1..500, default 100.")
        ] = 100,
        before: Annotated[
            str | None,
            Query(
                description=(
                    "Cursor: return decisions whose `decision_id` sorts before this "
                    "ULID, i.e. older ones."
                )
            ),
        ] = None,
        kind: Annotated[
            Literal["decide", "inspect"] | None,
            Query(description="Return only rows of this kind. Omitted, both kinds are returned."),
        ] = None,
        key_id: Annotated[
            str | None,
            Query(
                description=(
                    "Return only decisions authenticated with this issued API key. "
                    "Decisions taken under the static token carry no key and are never returned."
                ),
            ),
        ] = None,
    ) -> DecisionListResponse:
        """Decision feed for the dashboard and the benchmark. Returns stored decisions
        newest first as `{items, next_before}`, with cursor pagination by `decision_id`:
        pass the `next_before` from a page back as the `before` query parameter to get
        the next (older) page. `next_before` is `null` when the last page was returned.
        Because `decision_id` is a ULID, ordering by it is ordering by time.

        `limit` is `1..500`, default `100`; a value outside that range is a `422`
        (this is a read endpoint, not the always-200 decide path). Requires the bearer
        credential when the service is configured with one.
        """
        rows = await decision_repo.list(
            session_id=session_id, model=model, limit=limit, before=before, kind=kind,
            key_id=key_id or None,
        )
        next_before = rows[-1].id if len(rows) == limit else None
        return DecisionListResponse(items=rows, next_before=next_before)

    @app.get(
        "/v1/profiles/{id}",
        response_model=Profile,
        dependencies=[auth],
        operation_id="getProfile",
        summary="Read a policy profile",
        tags=["read"],
        responses={
            200: {"description": "The profile as loaded, without secret values."},
            401: UNAUTHORIZED,
            404: {"model": Error, "description": "No such profile id."},
        },
    )
    async def get_profile(
        profile_id: Annotated[
            str,
            Path(
                alias="id",
                description="Profile identifier, e.g. `default` or `default-dev`.",
            ),
        ],
    ) -> Profile:
        """Reads a policy profile exactly as the service loaded it, returned as the
        `Profile` object directly (200) or a `404` for an unknown id. The `Profile`
        schema is generated from the running pydantic model
        (`agentgate/profiles/schema.py`). Profiles are
        server-side configuration: a harness passes at most a `profile_id` and never
        reads policy in normal operation. This endpoint exists for operators and the
        dashboard.

        **Secrets are not returned.** Model API keys are never stored in a profile in
        the first place — a profile holds only the *name* of the environment variable
        (`api_key_env`), and that name is what comes back.
        """
        profile = profiles.get(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
        return profile

    @app.get(
        "/healthz",
        response_model=Health,
        operation_id="healthz",
        summary="Liveness probe",
        tags=["read"],
        responses={200: {"description": "Service liveness. `degraded` is still a 200."}},
    )
    async def healthz() -> Health:
        """Liveness probe, no credential required. Returns `{"status": "ok" | "degraded",
        "db": <bool>, "llm": null}`: `status` is `degraded` when the database probe
        fails, `ok` otherwise; `db` is the boolean result of that probe; `llm` is
        reserved and currently always `null`. The status code is always `200` — a
        `degraded` body, not a 5xx, signals a dependency is down. `git_sha` is the
        commit the running image was built from, or `null` for a build without it. A single `/healthz`
        right after a container start can briefly report `db: false` during connection
        warmup and then recover. `protocol` is the contract version served on
        `POST /v1/decide`.
        """
        db_ok = True
        if db_probe is not None:
            try:
                db_ok = await db_probe()
            except Exception:  # noqa: BLE001 - a broken probe means "not ok", not a 500 from /healthz
                log.warning("database probe failed", exc_info=True)
                db_ok = False
        return Health(
            status="ok" if db_ok else "degraded", db=db_ok, llm=None,
            git_sha=settings.git_sha, protocol=PROTOCOL,
        )

    install_openapi(app)
    return app
