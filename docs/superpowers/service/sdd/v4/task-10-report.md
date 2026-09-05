STATUS: DONE

Commit: d7f73e0 (branch v4/task-10, base e2c9627)

## Failing-test evidence (step 2, before implementation)
`tests/engine/test_inspection.py::test_a_cache_hit_keeps_spans_redaction_and_redacted_text_but_not_rejections` etc. failed with:
`TypeError: Inspection.__init__() got an unexpected keyword argument 'spans'`
(4 failures: 3 in test_inspection.py, 1 in test_repo.py — exactly as the brief predicted).

## What changed
`agentgate/engine/inspection.py`: added `spans: tuple[Span, ...] = ()`, `redacted: int = 0`,
`spans_rejected: int = 0`, `redacted_output: str | None = None` to `Inspection`; `as_cached`
now carries `spans`/`redacted`/`redacted_output` forward (not `spans_rejected` — a
per-call validation count, not part of the verdict); `to_record` sets
`raw=request.output if redacted_output is None else redacted_output` and passes
`spans=list(self.spans)`, `redacted`, `spans_rejected` into `DecisionRecord`.

Followed the brief exactly, including its own prediction: `DecisionRepo.insert` needed
no change — `Cost` already proved pydantic's `model_dump()` recurses into nested models
for JSONB, and the Postgres roundtrip test (`test_inspect_spans_and_redaction_roundtrip_through_postgres`)
confirms `Span` round-trips including the confidence-drop serializer.

Test files updated exactly as specified in task-10.md (imports added, four tests appended
to test_inspection.py, one appended to test_repo.py).

## Verification
- `tests/engine tests/store tests/api` in isolation: 228 passed (first clean run).
- Targeted 10 tests (the 5 new + the 5 pre-existing that touch the same code path) reran
  three times isolated: 10/10 pass every time.
- Full suite (`pytest -q`) first clean run: 1018 passed, 3 e2e errors — those were
  self-inflicted (I ran `alembic upgrade head` by hand against the same DB moments
  earlier without the e2e fixture's own schema reset, leaving stray tables); rerunning
  `tests/e2e/test_e2e.py` alone: 3 passed.
- Later full-suite and scoped reruns showed 6-11 unrelated failures (test_keys.py,
  test_cli_keys.py, and later even test_repo.py tests I did not touch) that changed
  shape between runs — duplicate idempotency-key IntegrityErrors, NoResultFound,
  UndefinedTableError on tables other tests just dropped/created. None reference
  `Span`/`spans`/`Inspection`. This is concurrent contention on the shared
  `agentgate_test_v4` Postgres database from other v4 task worktrees running their own
  drop_all/create_all cycles at the same time (per constraints.md, all v4-task-N
  worktrees point at the same DB) — not something this task's code can or should fix.

## Concerns
- The shared-DB contention above is an operational hazard for any v4 task run
  concurrently with others; worth flagging to whoever coordinates the task wave so
  DB-touching test runs across worktrees are serialized.
- No other deviations from the brief.
