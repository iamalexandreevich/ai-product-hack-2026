# SDD ledger — AgentGate v1.5 SOLID refactor

Plan: docs/superpowers/service/plans/2026-09-04-agentgate-v1.5-solid-refactor.md
Branch: refactor/solid-v1.5
Baseline: a878160 (plan + review committed), 509 tests green in 8.1s
Test DB: agentgate-pg on localhost:5433, AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test

| Task | Base | Head | Implementer | Review | State |
|---|---|---|---|---|---|
| 1 Verdict | a878160 | 449eb8d | sonnet DONE | APPROVED, 0 crit / 0 imp | **done** |
| 2 Decision + writer | 449eb8d | bab29cc | sonnet DONE | NEEDS FIXES, 2 important | fix queued |
| 3 RuleChain | bab29cc | cb5da99 | opus DONE_W_CONCERNS | APPROVED, 1 important | fix queued |
| 4 shell/ + ParsedArgv | f9be1cb | | opus running | | implementing |
| 5 Policy | | | | | pending |
| 6 protocols + bootstrap | | | | | pending |
| 7 CommandSpec | | | | | pending |
| 8 OpenAPI | | | | | pending |
| 9 docs | | | | | pending |

## Cadence (changed after task 1, at the user's request)

Reviews are OFF the critical path: dispatch the reviewer in background right after a task commits,
then start the next task immediately. Findings arrive as notifications and are fixed at the next
natural boundary. Nothing blocks on a review.

Guards that replace the blocking reviews — I run these myself, they are deterministic and cheap:
- Every task: full suite green, `contracts/` diff empty on regeneration, and zero changes to
  tests/test_stage1_hard_deny.py, tests/test_stage1_chain.py, tests/test_normalize_shell.py.
- Task 4 specifically: `tests/equivalence/baseline.json` must be committed BEFORE the secret-list
  merge and NOT modified afterwards. A silently regenerated baseline makes the corpus worthless —
  this is the one failure mode a reviewer would have caught, so check it with git, not by reading.
- Task 5: the four regression tests in tests/domain/test_workspace_binding.py must be committed
  red-then-green (they reproduce the F4 hole before they prove it closed).

## Rulings
- R1 (plan): unparseable settled by stage 1 (stage=1, model=null). Task 3.
- R2 (plan): policy workspace binds to the session, per spec 6. Task 5.

## Log

- Task 1 DONE at 449eb8d. 524/524 green (509 baseline + 15 new), contracts diff empty.
  Table tests (hard_deny, chain, normalize_shell, stage2_run): zero changes — the refactor invariant held.
  Implementer left hard_deny.py comment history in place; correct, Task 3 owns that file's split.
- Cadence change: reviews moved to background. Task 2 dispatched at 449eb8d without waiting
  for task 1's review, which is still running.
- Task 1 review: Approved. Reviewer checked all 14 verdict-construction sites one by one;
  every rule_id and every hard=True survived. Noted that Verdict.deny/ask make `hard` and
  `stage` keyword-only, which forecloses the field-order defect class by construction.
- Only Minor: lint not run. Investigated — the service has NO linter configured at all
  (no [tool.ruff], ruff not in dev deps), so nothing was skipped. But the code carries
  ~8 dead `# noqa: BLE001` directives nothing enforces. Out of plan scope; spawned as a
  separate task (task_2bd83946) rather than widening this branch.
- Task 2 DONE at bab29cc. 543 green (I re-ran: 7.79s). Guards: table tests untouched,
  contracts clean. Implementer added a transitional DecisionRecord.to_view() =
  DecisionView(**self.__dict__) so two repo tests needed no edits; it asserts the shapes
  match rather than copying them, and task 6 deletes DecisionRecord. Flagged to the
  reviewer for judgment: DecisionRepo.insert is annotated (decision: Decision) but really
  accepts anything with .to_view() — the annotation understates its contract.
- Task 3 dispatched on OPUS (not sonnet): 922-line file into 7 modules, security core,
  and the shared-vs-private split needs judgment the brief cannot fully enumerate.
- Task 2 review: Needs fixes. BOTH findings are defects in MY PLAN, not the implementer's work —
  the implementer transcribed the brief faithfully and the brief was wrong.
  (a) IMPORTANT, real behaviour regression: `api.unknown-model` now records profile_hash="" where
      the old pipeline.py recorded base_profile.profile_hash(). The profile WAS resolved; only the
      model lookup failed, so the hash existed and was being written. Plan Step 8's sample had the
      literal "" for both early-exit branches. Reviewer confirmed empirically. Affects the persisted
      audit row and the JSONL line, not the HTTP response.
  (b) IMPORTANT, style 5.3: tests/factories.py:46 docstring says "Replaced by a Classifier fake in
      task 6" — a task reference in shipped code, copied verbatim from plan Step 1.
  Minor, accepted as-is: DecisionRepo.insert annotation understates its duck-typed contract
  (reviewer judged it acceptable transitional debt; task 6 deletes DecisionRecord); writer.py
  module docstring overclaims "never raises" for the two leaf writers; _finish has 10 params.
  Reviewer also noted CompositeDecisionWriter FIXED a latent bug: jsonl.write used to sit outside
  the try/except in app.py's persist closure, so a JSONL failure could abort the Postgres writes.

- SOURCE PATCHED so the regression cannot survive task 5: added the required `_resolve` early-exit
  form plus a mandated regression test (test_unknown_model_still_records_the_profile_hash) to BOTH
  the plan and task-5-brief.md. Code fix itself is QUEUED: gate.py and factories.py are being
  edited by task 3 right now, so touching them would clobber it. Apply immediately after task 3
  commits, before task 4.

## Deploy state (PAUSED at user's request — refactor first)
VDS 109.172.95.51 (Ubuntu 26.04, 3.9GB RAM, docker 29.7.2). Deploy dir /opt/agentgate (rsync, not git),
holding PRE-refactor code (agentgate/pipeline.py present) plus a Makefile that came from another branch.
Done: installed a dedicated passphrase-less deploy key ~/.ssh/agentgate_deploy (personal id_ed25519 is
passphrase-protected and the agent was empty, so it could not be used non-interactively); added
`restart: unless-stopped` to both services (was RestartPolicy=no — nothing would survive a reboot);
brought the stack up. Backup of the original compose at /opt/agentgate/docker-compose.yml.bak.
UNRESOLVED: /healthz returns {"status":"degraded","db":false} — gate cannot reach db. Logs show asyncpg
"Event loop is closed" in _terminate_graceful_close. Containers are Up; the earlier exit 137 was NOT a
crash (OOMKilled=false, db exited 0 alongside) — someone ran `docker compose down` 3h earlier.
Open question for later: how to version server code from a monorepo carrying three separate tracks.

- Task 3 review: APPROVED. Reviewer traced all 38 constants/helpers of the deleted 930-line file to
  their new homes (none dropped) and probed 17 branches the 174 table cases do not cover — all match.
  Endorsed the implementer's deviation from the brief's shared.py list: "I would have flagged the
  brief's list had it been followed" (14 of ~20 names had one client). shared.py is 110 lines and
  genuinely shared. Best observation: the old module's single long "unresolved expansion" essay became
  three rule-specific halves, each stating only its own rule's invariant — a decomposition, not a move.

- QUEUED FIX (apply after task 4, before task 7 touches these rules):
  IMPORTANT. WrapperUnresolvedRule's "runs after all six" was STRUCTURAL when it was a tail block
  inside check_hard_deny; as list element 7 it is a line anyone can move, and moving it to the head
  leaves all 574 tests green while turning `rm -rf / && env -S 'x'` from hard-deny.destructive into
  ask/ambiguous.wrapper-opaque — a hard deny replaced by an ask, which CLAUDE.md forbids outright.
  No case in the suite is a mixed action, so nothing notices. Fix: add ONE case to DENY_CASES:
  ("rm -rf / && env -S 'x'", "hard-deny.destructive"). Adding a case does not violate the
  byte-identical constraint — that forbids CHANGING an expectation, not strengthening the table.
  Minor, also queued: document `id` vs a verdict's narrower `rule_id` in rules/base.py (three rules
  differ); restore the dropped `flags.has_heredoc` reasoning into docs/reports/task-5-hard-deny.md;
  strip ~35 process-comment lines from the moved test files (deferred, not out of scope).

- DONE now (safe, docs only): spec 5.2 rewritten to describe the RuleChain of objects instead of a
  list of functions taking SessionState; module map and packages slot repointed from stage1/ to
  rules/. Commit 2db2830.
