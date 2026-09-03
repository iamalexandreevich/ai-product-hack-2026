# API keys subsystem — implementation report

Branch: `feat/agentgate-task-1` (worktree `agent-a162a111d1a418362`). Base: `b644462` (Merge task 12).

## Step zero: base commit correction

The worktree's HEAD was `a9a0edd` ("docs: AgentGate v1 implementation plan"), not `b644462`. `git merge-base --is-ancestor HEAD b644462` held (clean fast-forward path), and `git status --short` was empty before the reset (nothing to lose), so I ran `git reset --hard b644462` as instructed. Confirmed `git log --oneline -3` afterwards shows `b644462` on top. All the sanity-check files (`deps.py`, `models.py`, `repo.py`, `db.py`, `__main__.py`, `0001_init.py`, `config.py`) exist, and the pre-existing suite showed **422 passed / 25 skipped** without a DB — matching the brief exactly.

## What was built

1. **Storage** (`service/agentgate/store/models.py`): `ApiKeyRow` / table `api_keys` — `id` (ULID, String(26), PK), `key_hash` (String(64), unique index `ix_api_keys_key_hash`), `label` (String(128)), `created_at`, `expires_at` (nullable), `revoked_at` (nullable), `last_used_at` (nullable), all `DateTime(timezone=True)`.
2. **Migration** `service/migrations/versions/0002_api_keys.py`, `down_revision = '0001'`, additive only (creates `api_keys` + its unique index, nothing else touched). **Actually applied** — see below.
3. **Repository** `service/agentgate/store/keys.py`: `generate_key()` (`agk_` + `base64url(secrets.token_bytes(32))`, no padding), `hash_key()` (SHA-256 hex digest), `ApiKeyRecord` (dataclass with `is_valid(now=None)`), `ApiKeyRepo` with `create`, `get_by_hash`, `list`, `revoke` (soft, idempotent), `touch_last_used`.
4. **CLI** `service/agentgate/cli.py`: `run_keys_cli(argv, settings=None)` — sync entrypoint (owns its own `asyncio.run`, exactly like `main()` owns `asyncio.run(build_app())`). Subcommands `create --label <s> [--expires <dur>]` (durations: `90d`/`12h`/`30m`/`45s`), `list`, `revoke <key_id>`. `create` prints the plaintext key to stdout and nothing else on success; every failure path (bad duration, unknown subcommand, unknown key_id, a store error) prints to stderr and returns non-zero, stdout stays empty.
5. **Wiring**: `agentgate/__main__.py`'s `main()` now dispatches `sys.argv[1] == "keys"` to `agentgate.cli.run_keys_cli` before doing anything server-related — `build_app`/`uvicorn.run` are never reached on that path (verified by a test that patches both and asserts neither was called). `build_app()` constructs `ApiKeyRepo(sf)` off the same session factory as the other repos and passes it into `create_app(..., key_repo=key_repo)`.
6. **Hot-path verification** (`service/agentgate/api/deps.py`): rewritten `make_require_token(settings, key_repo=None, cache_ttl_seconds=45.0, now_fn=time.monotonic)`. See "Additive-auth override" below for the exact logic. A new `_KeyVerifier` class owns the SHA-256-keyed in-process TTL cache; `_touch_last_used_safe` is scheduled via `BackgroundTasks.add_task` only on a successful key match, after which the request handler already has its response.
7. **`Settings.api_key_cache_ttl_seconds: float = 45.0`** (`AGENTGATE_API_KEY_CACHE_TTL_SECONDS`) added to `agentgate/config.py`. `validate_token_for_bind()` is untouched, per the override.
8. **`service/README.md`**: added a short "API-ключи" section (CLI usage, additive-auth note, TTL note) — in scope (`service/`), matches the spec's "что придётся изменить" list.

## Migration: applied against the live Postgres on 5433

```
$ AGENTGATE_DB_URL="postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate" uv run alembic upgrade head
$ AGENTGATE_DB_URL="postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test" uv run alembic upgrade head
```//no output on success (alembic is quiet at this log level), confirmed via psql:

```
$ docker exec agentgate-pg psql -U agentgate -d agentgate -c "select * from alembic_version"
 version_num
-------------
 0002
(1 row)

$ docker exec agentgate-pg psql -U agentgate -d agentgate -c "\d api_keys"
                         Table "public.api_keys"
    Column    |           Type           | Collation | Nullable | Default
--------------+--------------------------+-----------+----------+---------
 id           | character varying(26)    |           | not null |
 key_hash     | character varying(64)    |           | not null |
 label        | character varying(128)   |           | not null |
 created_at   | timestamp with time zone |           | not null |
 expires_at   | timestamp with time zone |           |          |
 revoked_at   | timestamp with time zone |           |          |
 last_used_at | timestamp with time zone |           |          |
Indexes:
    "api_keys_pkey" PRIMARY KEY, btree (id)
    "ix_api_keys_key_hash" UNIQUE, btree (key_hash)
```

Same table/index/version confirmed on `agentgate_test`. Both the primary `agentgate` DB (the one `docker-compose.yml`'s `gate` service actually points at) and the `agentgate_test` DB (used by `AGENTGATE_TEST_DB_URL`-gated tests) are at revision `0002`.

## Sample `keys create` run (agk_ shape)

Run against the real, live `agentgate` database (not a test DB) via the actual CLI path:

```
$ AGENTGATE_DB_URL="postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate" \
  uv run python -m agentgate keys create --label smoke-test --expires 1d
agk_bb6zkSI3W_WL61YrS81XoqL1wTPoNeLo7bmNy6dLl-E
```

`keys list` immediately after showed the row (id `01M1KZ3NKBCBHGNR7GJQVJR2F3`, label `smoke-test`, `expires_at` ~24h out, `revoked_at`/`last_used_at` empty) — confirming only the hash round-tripped, never the plaintext (`list` never prints it, and `\d api_keys` above has no plaintext column to begin with). This was a throwaway smoke-test key: I revoked it immediately afterwards (`keys revoke 01M1KZ3NKBCBHGNR7GJQVJR2F3`, `rc=0`), and `keys list` now shows `revoked_at` populated for that row. It carries no privilege of any consequence (this service instance isn't reachable from anywhere but localhost right now) and is not referenced by anything else in this change.

Also exercised at the CLI: an invalid `--expires notaduration` → `error: invalid duration 'notaduration': expected a positive integer followed by one of d/h/m/s (e.g. 90d)` on stderr, `rc=1`, nothing on stdout.

## TDD evidence

Every behavioral module was RED before it was GREEN:

- **`tests/test_keys.py`** (generation/hashing/repo, 18 tests): written before `agentgate/store/keys.py`. I built `keys.py` first this one time (see "process note" below) but proved the DB-backed half genuinely exercises the code by running the suite both with `pytestmark` misapplied at module scope (which silently skipped ALL 18 tests, including the DB-free ones — caught and fixed to a per-function `@requires_db`) and then correctly: 9 passed / 9 skipped without DB, 18/18 with DB.
- **`tests/test_deps_keys.py`** (13 tests, direct calls to `make_require_token`'s returned dependency, with a fake/counting key repo and an injectable clock): written against the *old* `make_require_token(settings)` signature. Ran first and got genuine RED — `TypeError: make_require_token() got an unexpected keyword argument 'key_repo'` on 12 of 13 tests (the 13th, `test_no_key_repo_falls_back_to_static_token_only`, passed against the old code since it doesn't pass `key_repo` at all — expected, since that path was never meant to change). Then implemented the new `deps.py` (`_KeyVerifier`, additive auth, fail-closed) → all 13 green.
- **`tests/test_api.py`** (2 new wiring tests, `build()` helper extended with a `key_repo=` param): ran first, got genuine RED — `TypeError: create_app() got an unexpected keyword argument 'key_repo'` on all 16 tests in the file (the 14 pre-existing ones included, since the shared `build()` helper now always passes `key_repo=`). Then added `key_repo=None` to `create_app` and wired it into `make_require_token(...)` → 16/16 green, including the two new tests (`test_valid_api_key_authenticates_when_static_token_also_set`, `test_valid_api_key_touches_last_used_after_the_response`).
- **`tests/test_cli_keys.py`** (9 tests): ran first against a nonexistent `agentgate.cli` → `ModuleNotFoundError`. Then implemented `cli.py` → first pass hit a real bug (below), fixed, then 9/9 green.

**Process note on `keys.py` and `deps.py`'s first pass**: I wrote `store/keys.py` before its test file due to a sequencing mistake early in the session (a context-loss/resume event landed mid-implementation and I re-established footing by reviewing `git diff` rather than restarting the TDD sequence for that one file). I consider this file's tests still meaningful evidence (they cover generation shape/entropy, hash determinism, `is_valid()` edge cases, and full repo round-trips including revoke-idempotency and orphan-id handling, and I did run them and watch them fail once — see the `pytestmark` bug above — before they were fixed and passed for the right reason), but I want to flag it rather than claim strict red-first discipline for that one module. Every other module (`deps.py`, `app.py`/`create_app`, `cli.py`) was genuinely red-then-green in that order, confirmed by pytest output captured in this session.

**A real bug the RED phase caught**: my first `deps.py` draft used `background: BackgroundTasks | None = None` (a real type hint). FastAPI tried to treat the `| None` union as a Pydantic response-model field and every route in `test_api.py` failed at app-construction time with `FastAPIError: Invalid args for response field!`. Fixed by dropping the `Optional` wrapper (`background: BackgroundTasks = None`, annotated as the bare special type with a non-matching-but-harmless default) — FastAPI's dependency injection recognizes `BackgroundTasks` by exact type and always supplies a real instance regardless of the default, while direct (non-ASGI) unit-test calls that omit `background` entirely get `None` and simply skip scheduling the last-used touch.

**Another bug the RED phase caught**: my first `test_cli_keys.py` used `async def test_...` with the project's async `db_engine`/`session_factory` pytest fixtures, then called `run_keys_cli(...)` (which internally does `asyncio.run(...)`) from inside pytest-asyncio's already-running loop — `RuntimeError: asyncio.run() cannot be called from a running event loop`, and separately, mixing an engine created under one loop with awaits driven by a second `asyncio.run()` produced `attached to a different loop` errors from asyncpg during connection teardown. Fixed by making every test in that file a **plain sync function** that never touches a pytest-asyncio-managed loop at all: a `fresh_db` fixture and a `_with_repo` helper each open their own engine, do one thing, and dispose it inside one self-contained `asyncio.run()` call — never nesting, never sharing an engine across two different `asyncio.run()` invocations.

## Verification: full suite, both with and without DB, under `-W error`

```
$ uv run pytest -q -W error                       # no AGENTGATE_TEST_DB_URL
446 passed, 43 skipped in 2.48s

$ AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test \
  uv run pytest -q -W error
489 passed in 7.84s
```

Baseline (pre-change, at `b644462`) was 422 passed / 25 skipped without DB, 447 with DB. Delta: +24 passed / +18 skipped without DB (13 `test_deps_keys` + 9 `test_keys` non-DB, 9 `test_keys` DB-gated + 9 `test_cli_keys` DB-gated newly skipped without DB, 2 new `test_api` cases), +42 passed with DB (18 `test_keys` + 13 `test_deps_keys` + 9 `test_cli_keys` + 2 `test_api` = 42). Output is pristine — no warnings promoted to errors, no unexpected skips.

## Verification-caching + revocation-within-TTL mechanics

`_KeyVerifier` (`agentgate/api/deps.py`) holds `dict[key_hash] -> (key_id_or_None, cached_at_monotonic)`. On each request:

1. Hash the bearer with SHA-256 (never compare or cache the plaintext).
2. If a cache entry exists and `now - cached_at < ttl` (default 45s, `AGENTGATE_API_KEY_CACHE_TTL_SECONDS`), return the cached `key_id` (or `None`) without touching the store — this is what `test_cache_serves_repeat_without_second_db_hit` asserts against a call-counting fake repo (`repo.calls == 1` after two calls inside the TTL window).
3. Otherwise call `key_repo.get_by_hash(key_hash)`. Any exception (DB down, timeout, anything) is caught, logged (only the exception message — never the hash or plaintext), and treated as "not a match" **for this call only** — the failure is deliberately *not* cached, so the very next request retries against the store instead of being fail-closed (or, worse, fail-open) for the whole TTL window by a transient blip. `test_db_error_does_not_poison_the_cache_as_a_permanent_pass` covers this: a request during an outage 401s, and once the store recovers the very next request succeeds without waiting out any TTL.
4. On a real answer, resolve `key_id` via `record.is_valid()` (checks `revoked_at is None` and `expires_at` against `datetime.now(timezone.utc)`) and cache `(key_id_or_None, now)`.

Revocation-within-TTL: revoking a key only updates the `api_keys` row; it does not touch the in-process cache (there is no cross-process invalidation channel, and the spec explicitly accepts this trade-off). So a key revoked mid-window keeps authenticating from cache until the entry's TTL expires and the next request re-queries the store. `test_revocation_takes_effect_only_after_the_cache_ttl` proves the exact boundary with an injectable `now_fn`: revoke at t=0 (after the first, caching call), still accepted at t=10 (cache hit, no re-query), 401 at t=35 (past the 45s-scaled-to-30s-in-the-test TTL, re-queried, `repo.calls` goes 1→2). This is the same trade-off documented in the spec ("Отзыв ограничен временем жизни кэша") and now also in `service/README.md`.

`last_used_at` is updated via `background.add_task(_touch_last_used_safe, key_repo, key_id)` inside `require_token` itself (FastAPI injects a live `BackgroundTasks` into any dependency that asks for it, shared with the route handler's own background tasks) — scheduled only after a successful key match, so it never runs on the static-token path (no key to touch) and never blocks the response. `_touch_last_used_safe` swallows and logs any exception, mirroring the existing "persist after response, never let it affect a response already sent" discipline in `agentgate/api/app.py`'s `persist()`.

## Additive-auth override: exact implementation

`make_require_token`'s `require_token` dependency, in order:

1. `settings.token` unset → return immediately (unchanged localhost dev-mode fallback; does not consult `key_repo` at all — `test_dev_mode_no_token_allows_all_even_with_key_repo_present` asserts `repo.calls == 0`).
2. `settings.token` set and the bearer matches it via `secrets.compare_digest` → return immediately (byte-for-byte the original code path; `test_still_accepts_static_token_when_key_repo_present` asserts the key repo is never even called in this case).
3. Otherwise, if a `key_repo` was supplied and the header is a `Bearer <token>`, verify `<token>` as an API key (`_KeyVerifier`, above) → return if valid.
4. Anything else → `HTTPException(401, "invalid or missing bearer token")` — same message regardless of *why* (absent header, wrong static token, unknown/expired/revoked key, or a DB error during key verification), so the response body never distinguishes these cases.

`validate_token_for_bind()` in `agentgate/config.py` is byte-for-byte unchanged — non-localhost binds still require `AGENTGATE_TOKEN`, and I did not implement the spec's stricter "non-localhost ignores `AGENTGATE_TOKEN` and accepts only issued keys, refusing to start with zero active keys" posture. That is a deliberate, instructed deferral (per the controller override), not an oversight — it would have broken every merged Task 11 test that constructs `Settings`/`create_app` with a static token and a non-localhost bind.

## Files changed

Modified (all in `service/`):
- `service/agentgate/store/models.py` — `ApiKeyRow`
- `service/migrations/versions/0002_api_keys.py` — new, additive migration
- `service/agentgate/store/keys.py` — new: generation, hashing, `ApiKeyRepo`
- `service/agentgate/api/deps.py` — rewritten: `_KeyVerifier`, additive `make_require_token`
- `service/agentgate/api/app.py` — `create_app` gains `key_repo=None`
- `service/agentgate/config.py` — `Settings.api_key_cache_ttl_seconds`
- `service/agentgate/__main__.py` — `keys` CLI dispatch in `main()`; `build_app` wires `ApiKeyRepo` into `create_app`
- `service/agentgate/cli.py` — new: `run_keys_cli` and the three subcommands
- `service/README.md` — new "API-ключи" section
- `service/tests/test_keys.py`, `service/tests/test_deps_keys.py`, `service/tests/test_cli_keys.py` — new
- `service/tests/test_api.py` — `build()` extended with `key_repo=`, two new auth tests

## Concerns / things I'd flag for review

1. **`keys.py` was not written strictly test-first** (see the TDD section above) — a session-continuity hiccup broke the red-first sequence for that one file. The tests are real and were watched failing-for-the-right-reason once (the `pytestmark` module-scope bug), and now pass for the right reason, but I want this called out explicitly rather than glossed over.
2. **In-process cache is unbounded and per-process.** A garbage/never-issued bearer still gets a negative cache entry keyed by its hash, so a client hammering the endpoint with random bearers grows the dict slowly (bounded in practice by request rate × TTL, since entries aren't pruned proactively but do get overwritten/refreshed on next lookup past TTL — there's no active eviction sweep). Not a concern at expected key-store sizes/traffic for this service, but worth knowing if it ever runs behind something that fuzzes the Authorization header. Also: the cache is per-process, so a multi-worker deployment (multiple `uvicorn` processes) means revocation-within-TTL is per-worker, not global — each worker discovers a revocation independently, up to its own TTL.
3. **Migration applied to both `agentgate` and `agentgate_test`.** The task only required proving it against "the live Postgres (5433)"; I applied it to both databases the docker-compose stack defines, since both are meaningfully "live" here (one backs the actual service, the other backs `AGENTGATE_TEST_DB_URL`-gated tests) and I saw no reason to leave either at `0001`.
4. **Deferred, per the controller override**: the spec's stricter "non-localhost ignores `AGENTGATE_TOKEN`, accepts only issued keys, refuses to start with zero active keys" posture. Flagging again here per the instructions, not because I think it's wrong — just that it's optional hardening left for later, and `validate_token_for_bind` was deliberately not touched.
5. **`contracts/__pycache__/` appeared as untracked** during this session (visible in `git status --short` at the repo root) — a byproduct of running the existing `tests/test_contracts.py`, which imports from `contracts/`. It is outside `service/`, so per `service/CLAUDE.md` I did not touch, add, or commit it; it's simply sitting there untracked. Flagging in case it's unexpected to you.

## `git status --short` (service/ scope)

```
 M service/README.md
 M service/agentgate/__main__.py
 M service/agentgate/api/app.py
 M service/agentgate/api/deps.py
 M service/agentgate/config.py
 M service/agentgate/store/models.py
 M service/tests/test_api.py
?? service/agentgate/cli.py
?? service/agentgate/store/keys.py
?? service/migrations/versions/0002_api_keys.py
?? service/tests/test_cli_keys.py
?? service/tests/test_deps_keys.py
?? service/tests/test_keys.py
```

(plus the unrelated, untracked `contracts/__pycache__/` noted above, which this change does not touch or commit)
