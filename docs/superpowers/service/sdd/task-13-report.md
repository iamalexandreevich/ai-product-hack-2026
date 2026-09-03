# Task 13 Report: CLAUDE.md and documentation

## Base commit correction

The worktree was checked out at `a9a0edd` ("docs: AgentGate v1 implementation plan"), which is an
ancestor of the required base `d79b5fa` ("Merge API keys: generation, storage, CLI, hot-path
verification") — `git merge-base --is-ancestor HEAD d79b5fa` held, so per the task instructions I
ran `git reset --hard d79b5fa`. Working tree was clean before the reset (no stashed/uncommitted
work lost). Sanity-checked files (`service/agentgate/api/app.py`, `service/agentgate/cli.py`,
`service/agentgate/store/models.py`, `contracts/openapi.yaml`, `service/README.md`,
`contracts/README.md`) all exist at the corrected base.

## What was implemented

### 1. Root `CLAUDE.md` (new)

Created at the repo root (the one file this task is permitted to add there). Corrects the stale
paths from the brief (`docs/superpowers/specs/...` → `docs/superpowers/service/specs/...`,
`docs/superpowers/plans/...` → `docs/superpowers/service/plans/...`) and adds links to
`context-versions-roadmap.md`, `api-keys.md`, `deploy.md`, `adapter-contract-gap-analysis.md` (all
present in `docs/superpowers/service/specs/`). Content:
- What AgentGate is, the `/v1/decide` cascade, and that all four HTTP endpoints
  (`POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz`) are actually
  implemented (verified against `service/agentgate/api/app.py`'s route decorators).
- Folder ownership (`service/`, `adapters/`, `benchmark/`, `contracts/`).
- The v1 invariants from the brief, verbatim in spirit: fail-closed everywhere, hard-deny
  non-overridable, decisions only from `NormalizedAction`, stage-2 reasoning-blind closed prompt
  list, allow-only cache, single YAML profile, Postgres-only, no LLM retries, and the explicit
  "not in v1" list.
- An "API-ключи" section describing the actual (not spec-aspirational) auth behavior: keys minted
  only via CLI, bearer accepted if it matches either `AGENTGATE_TOKEN` or a valid issued key
  (additive, not a replacement), verification cached in-process with a short TTL.
- Working rules: regenerate `contracts/` on schema change, hard-deny obfuscation coverage, stage-1
  p50 latency test, secrets via env only, English code/Russian docs.
- A "Известные ограничения / roadmap" section (see below) covering the gaps this task was asked to
  surface, kept short and pointed.

### 2. `service/README.md` (modified)

- Replaced the "Запуск (после реализации)" placeholder paragraph with two real run paths: local
  (`uv sync` → `alembic upgrade head` → `python -m agentgate`, both against a Postgres started via
  `docker compose up -d db`) and full Docker Compose (`docker compose up -d --build`, noting the
  `Dockerfile`'s `CMD` already runs `alembic upgrade head` before `python -m agentgate` on
  container start).
- Added an environment-variable table: `AGENTGATE_DB_URL`, `AGENTGATE_TOKEN`, `AGENTGATE_BIND`,
  `AGENTGATE_PROFILES_DIR`, `AGENTGATE_LOG_PATH`, `AGENTGATE_DEFAULT_PROFILE`,
  `AGENTGATE_API_KEY_CACHE_TTL_SECONDS`, and LLM provider keys (named per-profile via
  `models.configs.<name>.api_key_env`) — all cross-checked against `agentgate/config.py`'s
  `Settings` fields (pydantic-settings `env_prefix="AGENTGATE_"`).
- Added a "Тесты" section: plain `uv run pytest -q` (no DB) vs. `AGENTGATE_TEST_DB_URL=... uv run
  pytest -q` (with DB), naming which test modules are DB-gated.
- Added a "Профили" section: where the active profile lives (`AGENTGATE_PROFILES_DIR`, default
  `profiles/`, currently `profiles/default-dev.yaml`), what a profile controls, and specifically how
  to add a model — a new key under `models.configs` with `base_url`, `model`, `api_key_env` (plus
  `timeout_ms`, `structured_output`) — verified against `profiles/default-dev.yaml` and
  `agentgate/profiles/schema.py`'s `model_config_for`. Documented that `DecideRequest.model` selects
  a config by key at request time (falling back to `models.default`), and that an unknown model
  name yields `ask` / `rule_id: api.unknown-model` (`agentgate/pipeline.py:90-94`).
- Left the existing "API-ключи" section (written in an earlier task) as-is — it already covers
  minting/listing/revoking, the "shown once" guarantee, the additive-bearer behavior, and the TTL
  revocation-lag caveat, matching what this task's brief asked for.
- Did not touch the "База для тестов хранилища" section (still accurate).

### 3. `contracts/README.md` (modified)

- Fixed the stale spec path already present (`docs/superpowers/specs/...` →
  `docs/superpowers/service/specs/...` — same correction as the brief note).
- Corrected the claim that `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz` are "not yet
  implemented" — they are, per `service/agentgate/api/app.py`. Flagged, instead, that
  `contracts/openapi.yaml`'s generator (`service/scripts/export_openapi.py`) still marks those three
  routes `provisional`/"not yet implemented" in its prose, which is now inaccurate; fixing the
  generator is explicitly out of this task's scope (would touch `service/scripts/`, i.e. service
  logic) so it's called out as a known doc/generator drift rather than silently fixed or silently
  left unmentioned. I did **not** hand-edit `openapi.yaml` itself — `test_openapi_matches_generated_document`
  in `test_contracts.py` asserts the committed file equals fresh generator output, so a hand edit
  would immediately break that test.
- Added a "hook_client.py — пример вызова" section: a runnable `echo '<hook json>' | AGENTGATE_URL=...
  AGENTGATE_TOKEN=... python3 contracts/hook_client.py --user-request ... --profile ...` example
  built from the actual CLI surface in `contracts/hook_client.py` (`--user-request`/`--url`/
  `--profile`, `AGENTGATE_USER_REQUEST`/`AGENTGATE_URL`/`AGENTGATE_PROFILE`/`AGENTGATE_TOKEN` env
  fallbacks), plus an exit-code table (0 allow / 2 deny / 3 ask, the latter also covering an
  unreachable service or a malformed hook — verified against the `EXIT` dict and the fail-closed
  `try/except` in `main()`).

### 4. `service/Makefile` (new)

Targets: `deploy`, `logs`, `ps`, `rollback`, plus an internal `check-clean` prerequisite of
`deploy`.

- `check-clean` fails the build if `git status --porcelain -- .` (run from `service/`, i.e. scoped
  to the service tree that gets shipped) is non-empty.
- `deploy`: `check-clean` → `rsync -az --delete` of the current directory to
  `$(DEPLOY_HOST):$(DEPLOY_DIR)/`, excluding `.venv/`, `.env`, `logs/`, `__pycache__/`,
  `.pytest_cache/`, and `.previous_gate_image` (a state file `deploy` itself writes on the server —
  excluded so it's never overwritten by a stale/absent local copy) → over one SSH connection: record
  the current `gate` image id (for rollback), `docker compose up -d --build`, `docker compose exec
  -T gate alembic upgrade head` → a final `curl -fsS http://localhost:$(HEALTHZ_PORT)/healthz` on
  the server, whose failure is treated as a failed deploy (non-zero `make deploy` exit) with a
  pointer to `make logs`/`make ps`/`make rollback`.
- `logs` / `ps`: thin SSH wrappers around `docker compose logs -f` / `docker compose ps` in
  `$(DEPLOY_DIR)`.
- `rollback`: best-effort, one generation deep — reads the image id `deploy` saved just before its
  last build, re-tags it onto the name `docker compose config --images gate` reports, and restarts
  `gate` with `--no-build` from that tag, then re-checks `/healthz`. No automatic rollback on a
  failed deploy; the operator runs it by hand.
- `DEPLOY_HOST ?= agentgate` (an `~/.ssh/config` alias name, not a hostname), `DEPLOY_DIR ?=
  /opt/agentgate`. A comment block at the top of the file states, in English, that the operator must
  define a `Host agentgate` entry in their own `~/.ssh/config` (`HostName`, `User`, `IdentityFile`)
  and run `ssh-copy-id agentgate` once, and that this file must never carry an IP, an `IdentityFile`,
  or a password/token.

**Secret-free verification**: `grep -niE 'password|secret|token|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|identityfile' service/Makefile`
matches nothing except the word "password"/"token" inside the explanatory comment prose itself (no
literal credential, IP, or `IdentityFile` value appears). `ssh`/`rsync` calls reference only
`$(DEPLOY_HOST)` and `$(DEPLOY_DIR)`, both plain variables with non-secret defaults.

I did **not** run `make deploy` — the target server is behind a VPN and unreachable from this
environment, and the task explicitly reserves running it for the user. Syntax-checked with
`make -n deploy` / `make -n check-clean` (dry run) from `service/`; both expand correctly and `make`
raised no "missing separator" errors, confirming the recipe lines use real tabs.

### 5. `service/agentgate/store/models.py` docstring correction

`ApiKeyRow`'s docstring (around line 78) claimed `id` "doubles as the public `key_id` used in the
CLI, in `DecisionRow`/log attribution, and for revocation." Verified against
`service/agentgate/api/deps.py` and `service/agentgate/store/keys.py`: the only thing written back
for a key at request time is `last_used_at` (via `_touch_last_used_safe`, a `BackgroundTasks` call
after the response is sent). Nothing in `agentgate/api/app.py`'s decision-persistence path or the
JSONL logger reads or stores `key_id`. Corrected the docstring to state plainly that the id is the
`key_id` used by the CLI (`keys list`/`keys revoke`) only, and added a `NOTE:` paragraph stating
that per-decision key attribution (`key_id` in `DecisionRow`/JSONL, as the spec calls for) is not
wired yet and remains a roadmap item — no code changes beyond the docstring.

## Known limitations documented (root `CLAUDE.md`, "Известные ограничения / roadmap")

- Per-decision key attribution (`key_id` → `DecisionRow`/JSONL) not wired — only `last_used_at`.
- The key-verification cache is per-process (`agentgate/api/deps.py`'s `_KeyVerifier`): in a
  multi-worker deploy, revocation lag is per-worker, bounded by each worker's own TTL, not a single
  service-wide bound.
- v2→v4 context roadmap (dialogue history, tool-result evaluation, Context Guard) — not implemented,
  pointer to `context-versions-roadmap.md`.
- The adapter-contract fail-open-vs-our-fail-closed posture conflict (`adapter-contract-gap-analysis.md`)
  — unresolved by the product owner as of this task.
- `AGENTGATE_TOKEN` on a non-localhost bind is still accepted alongside issued keys; the spec's
  stricter "non-localhost accepts only keys" posture was deliberately not implemented (already
  documented in `agentgate/api/deps.py`'s own docstring; now also surfaced at the root level).

## Test suite result

`cd service && uv run pytest -q` (no `AGENTGATE_TEST_DB_URL`, no Postgres running in this
environment): **446 passed, 43 skipped** (all 43 skips are `AGENTGATE_TEST_DB_URL not set`, i.e.
`tests/test_store.py`, `tests/test_main.py`, `tests/test_keys.py`, `tests/test_cli_keys.py`,
`tests/e2e/test_e2e.py`).

**Discrepancy from the task's stated baseline** ("expect 466 passed / 23 skipped without a DB"):
actual is 446/43. Both runs total 489 tests either way (446+43 = 466+23 = 489), matching the "489
with `AGENTGATE_TEST_DB_URL`" figure the task text also states, so the *total* test count lines up
with expectations — only the no-DB split differs, by exactly 20 tests that are apparently now
DB-gated (or newly added and DB-gated) that the 466/23 baseline predates. This is consistent with
the Task 12 API-keys/e2e merge (`test_keys.py`, `test_cli_keys.py`, `tests/e2e/`) adding DB-gated
tests after that baseline figure was written. I did not attempt to reconcile this further — no test
file was touched by this task, and a stale expected-count in a prior task's brief is not something
T13's scope (docs + Makefile + one docstring) covers. Flagging it here rather than silently ignoring
it or silently "fixing" the number by editing tests.

I did not have a running Postgres in this environment, so I could not additionally verify the
"489 with `AGENTGATE_TEST_DB_URL`" figure directly — it is inferred from the no-DB skip count
matching that arithmetic, not independently confirmed.

## Files changed

- `CLAUDE.md` (new, repo root)
- `service/README.md` (modified)
- `contracts/README.md` (modified)
- `service/Makefile` (new)
- `service/agentgate/store/models.py` (docstring only)
- `.superpowers/task-13-report.md` (this file, new, not committed per instructions — only
  `reports/task-13-docs.md` is committed)
- `reports/task-13-docs.md` (new, Russian, committed)

## `git status --short` (before commit)

```
 M contracts/README.md
 M service/README.md
 M service/agentgate/store/models.py
?? CLAUDE.md
?? reports/task-13-docs.md
?? service/Makefile
```

(`.superpowers/` is untracked/ignored at the repo root and not part of this diff.)
