# Task 2 Report: API schemas and `contracts/`

## Environment note (read this first)

The worktree at `.claude/worktrees/agent-a40ffa1f79edd15c8` was, at task start, on branch
`worktree-agent-a40ffa1f79edd15c8` at commit `a9a0edd` — it did **not** contain Task 1's
commits (`a2e0979`, `2215d6a`, `593a461`, `90b8339` on `feat/agentgate-task-1`), even
though the assignment says it was branched from `feat/agentgate-task-1` @ `90b8339`.
`a9a0edd` is a direct ancestor of `90b8339` (verified with
`git merge-base --is-ancestor`), and the worktree had no uncommitted changes, so I brought
it in sync with `git reset --hard feat/agentgate-task-1` before starting any work. No work
was lost. All subsequent work is on top of `90b8339` as expected.

This report file itself had to be written inside the worktree (at the same relative path
under `.superpowers/sdd/2026-09-03-agentgate-v1/`) because the harness enforces write
isolation and refused a write to the main-checkout path given in the assignment.

## What was implemented

Per `task-2-brief.md`, in `service/agentgate/api/schemas.py`:
- `Tool(str, Enum)`: `shell`, `file_write`, `file_read`, `network`, `mcp_call`.
- `DecisionKind(str, Enum)`: `allow`, `deny`, `ask`.
- `McpArgs`, `ActionArgs`, `DecideRequest`, `LatencyMs`, `DecideResponse` — pydantic v2
  models exactly as specified (no added/renamed/dropped fields).
- Constants `USER_REQUEST_MAX_CHARS = 2048`, `RAW_MAX_BYTES = 32768`,
  `METADATA_MAX_BYTES = 16384`.
- Validators: `raw` byte-size cap; `metadata` JSON byte-size cap; `user_request`
  truncated to `USER_REQUEST_MAX_CHARS` keeping the tail; model-validator requiring
  non-empty `raw` when `tool == shell`.

Also created verbatim from the brief:
- `service/agentgate/api/__init__.py` (empty).
- `service/scripts/export_contracts.py` — regenerates `contracts/*.schema.json` from
  `model_json_schema()`.
- `contracts/decide_request.schema.json`, `contracts/decide_response.schema.json` —
  generated output, committed.
- `contracts/deny_message_template.md` — deny-message template for adapters.

## What was tested and results

`service/tests/test_schemas.py` and `service/tests/test_contracts.py` were written
verbatim from the brief before any implementation code existed.

## TDD Evidence

**RED** — command:
```
cd service && uv run pytest tests/test_schemas.py tests/test_contracts.py -v
```
Failing output (both modules failed at collection):
```
ModuleNotFoundError: No module named 'agentgate.api'
```
Expected failure: `agentgate/api/` did not exist yet, so the import in both test modules
could not resolve. This matches the brief's Step 2 expectation exactly.

**GREEN** — commands:
```
cd service && uv run python scripts/export_contracts.py
cd service && uv run pytest tests/test_schemas.py tests/test_contracts.py -v
```
Output: `wrote .../contracts/decide_request.schema.json`,
`wrote .../contracts/decide_response.schema.json`, then `9 passed` (all 7 schema tests +
2 contract tests), matching the brief's Step 4 expectation.

Full suite (baseline + new), run once before committing:
```
cd service && uv run pytest -q
```
`27 passed` (18 inherited from Task 1 + 9 new), zero warnings. Also re-ran with
`-W error` to force any hidden pydantic deprecation warning to surface as a failure:
still `27 passed` — output is warning-free.

## Files changed

- `service/agentgate/api/__init__.py` (new)
- `service/agentgate/api/schemas.py` (new)
- `service/scripts/export_contracts.py` (new)
- `service/tests/test_schemas.py` (new)
- `service/tests/test_contracts.py` (new)
- `contracts/decide_request.schema.json` (new)
- `contracts/decide_response.schema.json` (new)
- `contracts/deny_message_template.md` (new)
- `reports/task-2-schemas-contracts.md` (new) — `service/CLAUDE.md` requires a
  per-task report under `reports/` in the repo root (in scope per that file's "widened"
  allowed-write list), in addition to this file at the SDD path given in my assignment.
  Written in Russian per that convention.

## Self-review findings

- File list matches the brief's create list one-to-one; no extra fields, endpoints, or
  helper modules were added.
- Test code and implementation code are verbatim from the brief.
- `git status --short` (below) shows changes confined to `service/` and `contracts/`
  (plus `reports/`, which `service/CLAUDE.md` explicitly lists as in-scope for this repo
  state — see note above).
- No `service/.env` was read, printed, or touched.
- Only explicit paths were staged (`git add <path>...`); no `git add -A`/`-a`/`.` was
  used.

## Concerns

- The worktree base-branch mismatch described above (worktree not actually branched from
  `feat/agentgate-task-1` at session start). Resolved via `git reset --hard` to the
  correct branch tip before any work began; flagging so the controller can check whether
  other task-N worktrees have the same issue.
- `service/CLAUDE.md` (read at task start, more current than the task-2 brief) lists
  `reports/` as in-scope and mandates a `reports/task-<N>-<slug>.md` report in Russian
  after each task. I created `reports/task-2-schemas-contracts.md` to satisfy that
  binding project rule, in addition to this SDD report. Flagging in case the controller
  did not expect a `reports/` file from this task.

## `git status --short` after commit

```
(clean — nothing to show; all 9 files were committed)
```

Commit: `eea3a9a` on branch `worktree-agent-a40ffa1f79edd15c8`
(parent `90b8339`, tip of `feat/agentgate-task-1` at task start).

---

# Fix round 1: request byte-limit test coverage

## Scope of the fix

Review returned two Important findings (both about missing test coverage for request
byte limits) and two Minor findings (untested `max_length` constraints on `session_id`
and `harness`, and no size-limit test asserting the offending field). The controller
ruled the implementation itself correct — `service/agentgate/api/schemas.py` was **not**
changed. Only `service/tests/test_schemas.py` was edited.

## Finding 1 — `raw` byte limit had zero test coverage

Added:
- `test_raw_accepted_at_byte_limit` — an ASCII string of exactly `RAW_MAX_BYTES` bytes is
  accepted (pins the inclusive boundary).
- `test_raw_rejected_over_byte_limit_ascii` — `RAW_MAX_BYTES + 1` ASCII bytes is
  rejected, asserting the error is attributed to the `raw` field.
- `test_raw_rejected_over_byte_limit_multibyte` — a Cyrillic string whose character
  count is well under `RAW_MAX_BYTES` but whose UTF-8 byte count exceeds it is rejected
  (this is the case a char-count-instead-of-byte-count bug would silently accept),
  asserting the `raw` field.

## Finding 2 — `metadata` byte-limit test couldn't detect a byte-vs-char regression

The original `test_metadata_size_limit` used `"v" * (METADATA_MAX_BYTES + 1)` — pure
ASCII, so char count == byte count, meaning a naive `len(json.dumps(v))` character check
would have passed the test just as well as the correct byte check. Rebuilt into:
- `test_metadata_accepted_at_byte_limit` — a payload whose JSON serialization is exactly
  `METADATA_MAX_BYTES` bytes is accepted (boundary).
- `test_metadata_rejected_over_byte_limit_ascii` — one byte over the limit (ASCII) is
  rejected, asserting the `metadata` field.
- `test_metadata_rejected_over_byte_limit_multibyte` — a Cyrillic value sized so the JSON
  string's character count is under `METADATA_MAX_BYTES` while its UTF-8 byte count is
  over it; rejected, asserting the `metadata` field. This is the genuinely discriminating
  case the finding asked for.

Both the `raw` and `metadata` boundary/multibyte payload sizes are computed
programmatically in the test (from `RAW_MAX_BYTES`/`METADATA_MAX_BYTES` and, for
metadata, the measured JSON-wrapper overhead) rather than hand-calculated, so the tests
stay correct if those constants ever change.

## Minor findings folded in

- `test_session_id_accepted_at_max_length` / `test_session_id_rejected_over_max_length` —
  accept exactly 128 chars, reject 129, assert the `session_id` field on rejection.
- `test_harness_accepted_at_max_length` / `test_harness_rejected_over_max_length` —
  accept exactly 64 chars, reject 65, assert the `harness` field on rejection.
- A new helper `_field_names(exc: ValidationError) -> set[str]` reads `err["loc"][0]`
  from `exc.errors()`; every new rejection test in this round asserts the offending field
  through it, closing the "wrong field, still passes" gap.

## TDD evidence for this round

Implementation was not touched, and the review had already established it is correct, so
the honest expected outcome for these tests was to pass immediately as regression guards
— not to show a staged RED. That is what was observed; no RED was manufactured.

Command:
```
cd service && uv run pytest tests/test_schemas.py -v
```
Output: `16 passed` — all 6 pre-existing tests plus the 10 new/rebuilt ones
(`test_metadata_accepted_at_byte_limit`, `test_metadata_rejected_over_byte_limit_ascii`,
`test_metadata_rejected_over_byte_limit_multibyte`, `test_raw_accepted_at_byte_limit`,
`test_raw_rejected_over_byte_limit_ascii`, `test_raw_rejected_over_byte_limit_multibyte`,
`test_session_id_accepted_at_max_length`, `test_session_id_rejected_over_max_length`,
`test_harness_accepted_at_max_length`, `test_harness_rejected_over_max_length`), all
`PASSED` on the very first run. This is the expected result given the review's own
conclusion that the validators are correct.

Full suite, warnings as errors, run once before committing:
```
cd service && uv run pytest -q -W error
```
Output: `36 passed` (27 before this round + 9 net new: the old
`test_metadata_size_limit` was replaced by 3 tests, plus 3 for `raw`, 2 for
`session_id`, 2 for `harness` = +9), zero warnings.

Contracts re-checked (implementation untouched, so this is a sanity check, not new
coverage):
```
cd service && uv run pytest tests/test_contracts.py -v
```
Output: `2 passed`.

## Files changed this round

- `service/tests/test_schemas.py` (modified — tests only)
- `reports/task-2-schemas-contracts.md` (appended a matching "Fix round 1" section, in
  Russian per `service/CLAUDE.md`)
- This file (appended)

## `git status --short` before staging this round

```
 M service/tests/test_schemas.py
?? .superpowers/
```
(`.superpowers/` is this SDD-report scratch directory inside the worktree, not part of
the repo's tracked scope; only `service/tests/test_schemas.py` and the two report files
were staged and committed.)
