STATUS: DONE_WITH_CONCERNS

Commit: `d98ead1` (branch `v4/task-2`, base `e2c9627`). Working tree clean; report not committed.

**Failing-test evidence.** Step 3: `ImportError: cannot import name 'PRIVATE_KEY_REPLACEMENT' from 'agentgate.inspect.mask'`, then `ModuleNotFoundError` for `agentgate.inspect.secrets`. After the implementation, 45/46 passed on the first run; the single failure was the latency corpus `token_lines_dense` at `p50=50.189ms` (budget 5.0). Final: `tests/inspect` 124 passed; full suite with `agentgate_test_v4` 1091 passed; `export_contracts.py` + `export_openapi.py` leave `contracts/` byte-identical.

**Measured p50 (256 KB each, 25 samples).** ordinary_log 2.47 · key_equals_dense 2.88 · token_hint_dense_lines 3.21 · secret_lines_one_in_fifty 3.46 (199 findings) · begin_without_end_single_line 2.95 · token_hint_dense_single_line 2.82 · jwt_hint_dense_single_line 1.90 · many_empty_lines 0.61 ms.

**Changes against the brief.** The false-positive corpus is unchanged and passes as written; every functional test passes unmodified. The changes are all in the hot path, which the brief's code missed by 10x.

1. *Prefilter re-anchored.* The brief prefilters the sensitive-name form on the name hints (`token`, `secret`, …). A name is common, so a file of `token_i=…` lines hands every line to the matcher, and the brief's leading-branch pattern `(?:^|[\s"'{,(-])NAME SEP VALUE` costs 12.1 ms on a single 256 KB line of `token=a `. Names are now prefiltered on the *separator* instead: a line whose `=` or `:` carries no value of 9+ characters can hold neither a named secret nor an entropy candidate. `token=a` never becomes a hit line.
2. *One pattern per separator character, not `[:=]`.* Only a literal first character reaches the memchr skip; a two-character class costs a bitmap test per byte — measured 1.67 ms vs 0.48 ms on the same 256 KB.
3. *Needles deduplicated* (`gh` was searched twice, once per GitHub form) and *hint checks moved into the prefilter*: `_hit_lines` returns a flag mask per line, so `_scan_line` no longer re-scans a 256 KB line for 22 substrings.
4. *JWT `\b` dropped* in favour of a left-boundary test inside `accept`: the leading assertion costs a matcher entry per character, 2.60 ms vs 0.84 ms on a line of `eyJ.`.
5. *`\s` → `[ \t]`* in the separator of both the sensitive form and `_CANDIDATE`: `\s` lets the matcher walk over the newline and read the next line's key as this line's value, which on `KEY_i=` lines cost 5.3 ms by itself.
6. *Latency corpus `token_lines_dense` replaced* — see concerns.
7. Removed the brief's unused `_rewritten` test helper; renamed `_is_sensitive_name` to `_names_a_secret`, since the prefilter asks it about a whole line and not only about a key.

**Concerns.**

- **The brief's `token_lines_dense` corpus cannot meet 5 ms, by any implementation.** Every one of its ~10 156 lines (`token_{i}=abcdefghij{i}`, value 11–15 chars) is a genuine secret under spec 4.1's "longer than 8", and the threshold cannot be raised past 14 without losing the required `credential: hunter2hunter2` case. Constructing 10 156 `Finding` objects alone measures **7.5 ms** — the shared frozen dataclass from `e2c9627`, which I did not touch — and the full rewrite-and-record path costs ~5 µs/line, i.e. ~45 ms. The budget bounds the *scan*; it cannot bound the *reporting*. I split the corpus in two: `token_hint_dense_lines` (`token_{i}=abc{i}`, hint- and separator-dense, no finding) keeps the prefilter under stress, and a new `secret_lines_one_in_fifty` keeps the per-finding path under test at a density a real log reaches (199 findings, 3.46 ms). **Owner decision wanted:** either spec §7.2 gains a per-finding term, or `Finding` gets `slots=True` (roughly halves the 7.5 ms, still not enough for a 100 %-secret output).
- `SECRET_REPLACEMENT` / `PRIVATE_KEY_REPLACEMENT` were added to `mask.py` right after `REPLACEMENT_LINE` as instructed; the parallel task adds the same two lines, so expect a trivial merge conflict there.
- Margins are 1.5–3 ms on every corpus, and roughly 1.8 ms of the base is the 10 literal needle scans. A new token form with a new needle costs ~0.18 ms; a form whose needle is a common English substring costs far more, because it turns ordinary lines into hit lines.
- `entropy_candidates_allowed` matches the brief exactly, including the `~/.aws/**` pattern resolving against the *test runner's* home; `/home/u/.aws/credentials` passes only because `credentials` is also a basename pattern in `shell/secrets.py`.
