# Task 11: HTTP API — implementer report

## Base commit

Worktree was created from `a9a0edd` (not `b02d5c9`), but `git merge-base --is-ancestor HEAD b02d5c9`
held (clean fast-forward), so I ran `git reset --hard b02d5c9` as instructed. Post-reset baseline:
`391 passed, 19 skipped` (`uv run pytest -q`), confirmed before touching any code.

## What I implemented

- `service/agentgate/api/deps.py` — `make_require_token(settings)`, the single authentication
  dependency shared by every protected route. Token unset → allow (safe because
  `validate_token_for_bind()` already refuses to start on a non-localhost bind without a token).
  Token set → require `Authorization: Bearer <token>`, compared with `secrets.compare_digest`
  (never `==`) per the api-keys spec's "Проверка на горячем пути" discipline.
- `service/agentgate/api/app.py` — `create_app(settings, gate, decision_repo, session_repo,
  profiles, jsonl, db_probe=None) -> FastAPI` with the four routes from the brief:
  - `POST /v1/decide` — parses/validates the body by hand (`request.json()` then
    `DecideRequest.model_validate`) instead of declaring a pydantic-typed body parameter, so
    FastAPI's automatic `RequestValidationError`/422 path never triggers; a malformed JSON body or
    a validation failure both return 200 `ask`, stage 0, `rule_id "api.invalid-request"`, with
    `reason` set to the first validation error. `gate.decide(req)` is wrapped in `try/except
    Exception` — any escape becomes 200 `ask`, `rule_id "api.internal-error"`. On success, the
    persist callback is scheduled via `background.add_task(persist, rec, state)`, i.e. after the
    response is built (BackgroundTasks run after the response is sent).
  - `GET /v1/decisions` — `limit: int = Query(default=100, ge=1, le=500)`, passed straight to
    `DecisionRepo.list` (which independently clamps to `[1, 1000]`); response is
    `{"items": [...], "next_before": str | None}`, cursor pagination by `decision_id`
    (`next_before` set only when a full page came back).
  - `GET /v1/profiles/{id}` — `profile.public_dict()` or 404.
  - `GET /healthz` — no auth dependency attached; calls `db_probe()` if given, reports
    `{"status": "ok"|"degraded", "db": bool, "llm": None}`.
  - `persist(rec, state)` — writes the JSONL line, then (in one try/except) upserts the session row
    *before* inserting the decision row (FK order: `decisions.session_id → sessions.id`), then
    writes the allow-cache row when `decision == "allow" and not cached`. Any exception in that
    block is logged and swallowed — never surfaces to the client, which already has its response.
- `service/agentgate/__main__.py` — `build_app(settings=None)` does all the wiring (read
  `Settings`, `validate_token_for_bind()`, `load_profiles`, build engine/repos, restore session
  state + allow-cache into `InMemorySessionStateStore` from Postgres, build `Gate` with no
  `persist` callback of its own — the API layer's `persist` does that job — and `create_app`).
  `main()` is a thin wrapper: `asyncio.run(build_app())` then `uvicorn.run(...)`.

## Deviation from the brief's literal code

The brief's Step 3 `persist` writes `jsonl.write(rec.to_dict())`. `DecisionRecord.to_dict()` uses
the dataclass's own field name, `"id"`, not `"decision_id"`. But the brief's own Step 1 test
(`test_decide_allow_and_persist`) asserts
`json.loads(lines[0])["decision_id"] == data["decision_id"]` — the JSONL line as specified would
fail that exact assertion. I changed `persist` to write `dict(rec.to_dict(), decision_id=rec.id)`,
matching the convention already used for `/v1/decisions` items (`dict(r.to_dict(),
decision_id=r.id)`). Verified by the RED run below.

## TDD evidence

1. Wrote `service/tests/test_api.py` (brief's tests plus deny/ask outcome tests, an
   `api.internal-error` test with a raising `Gate` stub, auth edge cases, and a couple of shape
   tests) before writing `app.py`/`deps.py`.
2. RED: `uv run pytest tests/test_api.py -v` → `ModuleNotFoundError: No module named
   'agentgate.api.app'` (1 error, 0 collected) — genuine failure.
3. Implemented `deps.py` and `app.py`.
4. GREEN: `uv run pytest tests/test_api.py -v` → 14 passed.
5. Wrote `service/tests/test_main.py` (DB-backed, behind the project's `requires_db` skip guard)
   *after* `__main__.py` already existed (I wrote `__main__.py` per the brief's Step 4 in the same
   pass as `app.py`/`deps.py`, since Step 4 has no RED step of its own in the brief). To still get
   genuine RED for `test_main.py` specifically (service/CLAUDE.md: "a test that didn't fail before
   the implementation doesn't count"), I moved `agentgate/__main__.py` aside and reran:
   `ImportError: cannot import name '__main__' from 'agentgate'` — genuine failure — then restored
   it and reran: 3 passed.
6. Full suite, no DB: `uv run pytest -q -W error` → `405 passed, 22 skipped` (391+14 passed,
   19+3 skipped over baseline — arithmetic checks out).
7. Full suite, with `AGENTGATE_TEST_DB_URL` set: `uv run pytest -q -W error` → `427 passed`
   (410 baseline + 14 + 3). Pristine, no warnings promoted to errors.

## Auth seam for the API-key follow-on

`make_require_token(settings)` is the *only* place any route touches the `Authorization` header;
every route attaches the same `Depends(make_require_token(settings))` instance
(`auth = Depends(...)` built once in `create_app`, passed via `dependencies=[auth]`). Swapping
static-token verification for the generated-API-key design in
`docs/superpowers/service/specs/api-keys.md` means rewriting the body of `require_token` (hash the
bearer, look it up, check `revoked_at`/`expires_at`, cache briefly in-process) — no route decorator
changes. `secrets.compare_digest` is already used for the static-token comparison, matching the
discipline the API-key design commits to for its own hash comparison.

## Fail-closed 200 boundary

Two explicit paths, both tested:
- Malformed/invalid body never reaches FastAPI's own body-validation machinery: the route takes a
  raw `Request`, not a `DecideRequest`-typed parameter, so `RequestValidationError`/422 cannot be
  raised by the framework on this path. `request.json()` failures (`ValueError`) and
  `DecideRequest.model_validate` failures (`pydantic.ValidationError`) are both caught explicitly
  and turned into `_ask("api.invalid-request", ...)`.
- Any exception escaping `gate.decide(req)` — a bug in `normalize`, a synchronous store hiccup, or
  anything else `Gate.decide` doesn't already guard (see `agentgate/pipeline.py`'s docstring: it has
  no top-level try/except of its own) — is caught by a `try/except Exception` around the call and
  turned into `_ask("api.internal-error", ...)`. Verified with a `Gate` stub whose `decide` raises
  `RuntimeError` (`test_decide_raises_is_ask_200_internal_error`): 200, `ask`,
  `rule_id == "api.internal-error"`, and nothing persisted (the pipeline never produced a
  `DecisionRecord`).

Neither path can produce `allow`.

## What's tested vs. boot-only in `__main__`

Tested (via `build_app`, DB-backed, `tests/test_main.py`):
- Settings → `validate_token_for_bind()` (raises for non-localhost bind without a token).
- `load_profiles` + the default-profile-existence check (`SystemExit` when missing).
- Engine/session-factory/repo construction against a real Postgres.
- Session-state restore: seeded a `SessionRow` via `SessionRepo.upsert`, monkeypatched
  `InMemorySessionStateStore` with a capturing fake, and asserted `store.preload(...)` was called
  with the correct restored `SessionState` (`deny_total`, `decisions_total` round-trip correctly).
- Allow-cache restore: seeded a `DecisionRow` + an `AllowCacheRow` via `SessionRepo.cache_put`,
  asserted `store.cache_put(session_id, action_hash, decision_id, ttl_seconds)` was called with the
  right values and a TTL consistent with `expires_at - now`.
- The resulting `app`'s `/healthz` reports `db: True` against the real database (`db_probe` wired
  correctly).

Boot-only, not exercised by any test: `main()`'s body — `logging.basicConfig`, the `asyncio.run`
call itself, and `uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)`. `uvicorn.run`
blocks the calling thread running its own event loop and cannot be driven from inside a unit test;
this is why `build_app()` is factored out as the testable unit. I did not perform a full manual
`docker compose` + `alembic upgrade head` + `uv run python -m agentgate` + `curl` smoke run (the
brief's Step 6): there is no `alembic/versions/` directory yet in this worktree (migrations are not
set up), so that step isn't currently runnable as written. The DB-backed tests above already
exercise the identical code path (`build_app`) against the same live Postgres instance
(`localhost:5433`), just via `Base.metadata.create_all` (through the `session_factory` fixture)
rather than `alembic upgrade head`; a real `python -m agentgate` boot would additionally exercise
`uvicorn.run` itself, `logging.basicConfig`, and reading `Settings` from real environment variables,
none of which is meaningfully more likely to break at those specific lines than what's tested.

## Files changed

- `service/agentgate/api/deps.py` (new)
- `service/agentgate/api/app.py` (new)
- `service/agentgate/__main__.py` (new)
- `service/tests/test_api.py` (new)
- `service/tests/test_main.py` (new)

## Concerns / notes for review

- The allow-cache TTL used in `persist` (`_CACHE_TTL_SECONDS = 86400`) is a hardcoded constant
  matching `Gate.__init__`'s own default `cache_ttl_seconds`, since there is no `Settings` field for
  it and `build_app` constructs `Gate` with the default. If a future task makes this configurable
  on `Gate`, `create_app`'s `persist` needs the same value threaded through (currently `create_app`
  has no way to know what TTL the caller's `Gate` was built with — it's a latent coupling, not a bug
  today).
- `GET /v1/decisions` with `limit` outside `[1, 500]` returns FastAPI's own 422 (via `Query(ge=1,
  le=500)`), not a 200. The brief's 200-always contract is scoped to `POST /v1/decide` only, so this
  is intentional, but flagging it since it's the one endpoint in this module that isn't always-200.
- `git status --short` in the worktree shows exactly the five new files listed above — nothing else
  touched.

## `git status --short`

```
?? service/agentgate/__main__.py
?? service/agentgate/api/app.py
?? service/agentgate/api/deps.py
?? service/tests/test_api.py
?? service/tests/test_main.py
```
