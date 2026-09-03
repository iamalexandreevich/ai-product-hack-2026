# Task 10 report: JSONL log and Gate.decide() pipeline

## Base commit correction

Worktree was created by the harness from `a9a0edd` ("docs: AgentGate v1 implementation plan"), not the required `925e1f3` (Merge task 6). Tree was clean; `git merge-base --is-ancestor HEAD 925e1f3` confirmed a clean fast-forward, so `git reset --hard 925e1f3` was run. Post-reset sanity check confirmed all seven required files exist (`stage1/chain.py`, `stage2/run.py`, `session/state.py`, `session/memory.py`, `store/repo.py`, `normalize/__init__.py`, `profiles/loader.py`), and `uv run pytest -q` gave `376 passed, 19 skipped` before any work — matches the brief's expectation verbatim.

## What was implemented

- `service/agentgate/log/__init__.py` (empty) + `service/agentgate/log/jsonl.py`: `JsonlLogger(path)` with `write(record: dict) -> None`. Creates the parent directory, appends one `json.dumps(..., ensure_ascii=False, default=str)` line. `OSError` is caught and logged via `logging.warning`, never raised.
- `service/agentgate/pipeline.py`: `Gate(profiles, default_profile, state_store, http, persist=None, cache_ttl_seconds=86400)` with `async decide(req) -> tuple[DecideResponse, DecisionRecord, SessionState | None]`. Implements the exact pipeline order from the brief: profile resolution (unknown -> ask/stage 0/`api.unknown-profile`) -> unknown model -> ask (`api.unknown-model`) -> `with_workspace` -> `normalize` -> allow-cache lookup -> stage 1 -> (if `None`, including the unparseable case) stage 2 -> escalation (only when not hard and not already `ask`) -> session-state write -> allow-cache put -> `DecisionRecord`.

Implementation follows the brief's reference code (Step 4-5) closely, verified symbol-by-symbol against the already-merged task 2-9 code before writing anything. Two deliberate deviations from the brief's literal text, both explained below and in `reports/task-10-pipeline.md` (Russian).

## Tests and results

- `service/tests/test_log.py` — 2 tests, verbatim from the brief.
- `service/tests/test_pipeline.py` — 13 tests from the brief plus one new one (`test_persist_failure_does_not_change_or_raise_the_decision`), with one brief test renamed/corrected (see below).

RED (genuine, before any implementation):
```
$ uv run pytest tests/test_log.py tests/test_pipeline.py -v
ModuleNotFoundError: No module named 'agentgate.log.jsonl'
ModuleNotFoundError: No module named 'agentgate.pipeline'
Interrupted: 2 errors during collection
```

GREEN (after implementation):
```
$ uv run pytest tests/test_log.py tests/test_pipeline.py -v
...
15 passed in 1.04s
```

Full suite:
```
$ uv run pytest -q -W error
391 passed, 19 skipped in 1.64s          # no DB
$ AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q -W error
410 passed in 4.15s                      # with DB
```
391 = 376 inherited + 15 new; 410 = 395 (task 9 baseline with DB) + 15 new. Output pristine both ways, no warnings.

### Extra RED/GREEN evidence for the persist-swallowing behavior

Added `test_persist_failure_does_not_change_or_raise_the_decision`. To prove it actually exercises the protection (not just a vacuously-passing assertion), I temporarily removed the `try/except` around `await self._persist(rec, state)` in `_do_persist` and re-ran that one test:
```
RuntimeError: db is down
FAILED tests/test_pipeline.py::test_persist_failure_does_not_change_or_raise_the_decision
```
Then restored the `try/except Exception: log.exception(...)` wrapper; the test (and the full suite) passes again.

## Deviation 1 — `test_unparseable_goes_to_llm` renamed, assertion corrected

The brief's literal test asserted `llm.calls == 1` for an unparseable shell action. This directly contradicts already-merged, already-reviewed code: `agentgate/stage2/run.py::run_stage2` short-circuits for `action.flags.unparseable` *before* building any prompt or making any HTTP call — a deliberate fix from task 7's review (commit `2d3cc47`, "refuse unparseable actions before the LLM"), explicitly covered by the existing test `tests/test_stage2_run.py::test_unparseable_action_short_circuits_without_calling_llm` using the exact same input (`'echo "unterminated'`) and asserting `calls["n"] == 0`.

Implementing the brief's literal assertion would either produce a test that can never pass without reverting task 7's security fix, or require reverting that fix — both violate "fail-closed is the spine" and "hard-deny/protections are never overridden" from this task's own instructions. I renamed the test to `test_unparseable_skips_llm`, changed `llm.calls == 1` to `llm.calls == 0`, kept the outcome assertions (`ask`, `rec.normalized["flags"]["unparseable"] is True`), and added an in-line comment with the exact citation. Behavior otherwise matches the brief.

## Deviation 2 — `_do_persist` now catches exceptions

The brief's reference `_do_persist` does not wrap `await self._persist(rec, state)` in try/except. The task's own "Constraint 3" and "Fail-closed is the spine" sections explicitly require persist failures to be swallowed and logged, never propagated, and explicitly ask for a test covering "a persist that raises". I added `try/except Exception: log.exception(...)` and the test described above. This is an addition that satisfies an explicit instruction the brief's sample code itself did not fully implement, not a correction of a factual error, but noted here for completeness since it makes the shipped code diverge from the brief's literal listing.

## Five carry-forward constraints — how each is satisfied

1. **Fixed `[STAGE1]` vocabulary.** Two module-level constants (`_NOTE_PASSED`, `_NOTE_SKIPPED`) hold the exact fixed strings from the brief; `Stage1Decision.reason`/`.suggest` only ever reach `DecideResponse`, never the prompt.
2. **No LLM retries, one call one timeout.** `LLMClient` is built on the caller-supplied `httpx.AsyncClient` with no retry configuration; the single `classify()` call (task 7 code, untouched) enforces exactly one request with one timeout.
3. **Persist after response; write failure never changes the decision.** `_do_persist` runs after `resp`/`rec` are fully built and is never awaited on the decision-producing path; its own failure is caught and logged (see Deviation 2 and its RED/GREEN evidence).
4. **FK write ordering.** `Gate` does not write to the database itself — `persist` is injected (Task 11's job). The required ordering (session upsert -> decision insert -> allow-cache row) is documented in `agentgate.store.repo`'s existing docstrings (untouched) and restated in `pipeline.py`'s module docstring as the injected `persist`'s responsibility.
5. **Cache only `allow`; hard-deny never cached/escalated.** `cache_put` is called only inside `if decision is DecisionKind.allow`; escalation is gated on `not hard and decision is not DecisionKind.ask`. Both covered by dedicated tests (`test_deny_not_cached`, `test_hard_deny_has_reason_and_is_not_escalated_to_ask`).

## Self-review

- Completeness: `JsonlLogger`, `Gate`, exact pipeline order, all rule ids (`api.unknown-profile`, `api.unknown-model`, `cache`, `escalation`, plus stage1/stage2-sourced ones), both test files present.
- Discipline: no reimplementation of stage 1, stage 2, the store, or session logic — `pipeline.py` only imports and wires the existing `run_stage1`/`run_stage2`/`LLMClient`/`should_escalate`/`allow_cache_key`/`SessionStateStore`.
- Testing: real orchestration behavior with a fake LLM (`httpx.MockTransport`), ordering/cache/escalation/fail-closed paths all covered, genuine RED before GREEN for both the module-level tests and the persist-swallowing addition, pristine `-W error` output with and without a DB.
- Scope: `git status --short` shows exactly the four new paths below — nothing else touched.

## `git status --short`

```
?? service/agentgate/log/
?? service/agentgate/pipeline.py
?? service/tests/test_log.py
?? service/tests/test_pipeline.py
```
(plus `reports/task-10-pipeline.md`, added and committed separately; this `.superpowers/task-10-report.md` file itself is per instructions and not part of the commit.)

## Concerns

- The two deviations above are the only points where I did not follow the brief's literal text. Both are narrow, evidence-backed, and tied to explicit instructions elsewhere in this same task brief (fail-closed spine, constraint 3, and the task-7 review that is cited by name and commit hash). No other part of the brief's reference code needed changes.
- `Gate` itself has no default `persist` implementation that respects the FK ordering (session -> decision -> cache) — that logic will need to be written in Task 11 when the real `DecisionRepo`/`SessionRepo` are wired in. I did not build one here since the brief does not ask for it and CLAUDE.md says to build only what's requested; the ordering requirement is documented for Task 11 to pick up.
