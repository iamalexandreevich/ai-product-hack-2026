# SDD ledger — AgentGate v4 Context Guard

Plan: docs/superpowers/service/plans/2026-09-05-agentgate-v4-context-guard.md
Branch: feat/v4-context-guard (worktree …/ai-product-hack-2026-wt/v4), from main 8ab67c5
Test DB: service-db-1 on localhost:5433, AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test_v4
Neighbor: feat/v3.1-strictness-mcp-domains runs in the main checkout (another session); merge order v3.1 → main → this branch before wave 2.

| Wave | Task | Base | Head | Implementer | Review | State |
|---|---|---|---|---|---|---|
| 0 | 0 foundation | 1be575d | e2c9627 | controller inline | — | **done**, 1044 green |
| 1 | 1 mask.apply | e2c9627 | 8355636 | opus DONE_W_CONCERNS: two plan tests crossed DROP_SHARE, inputs widened | wave review pending | merged be5d3ea |
| 1 | 2 secrets | e2c9627 | d98ead1 | opus DONE_W_CONCERNS: hot path rebuilt to fit 5 ms; all-secret corpus split, owner question | wave review pending | merged be5d3ea |
| 1 | 4 segments | e2c9627 | 4cd7db2 | sonnet DONE | wave review pending | merged be5d3ea |
| 1 | 5 prompt + schema | e2c9627 | 10f3a97 | opus DONE_W_CONCERNS: unused stage1 param in _outcome_from | wave review pending | merged be5d3ea |
| 1 | 7 spans | e2c9627 | 5cc1532 | sonnet DONE | wave review pending | merged be5d3ea |
| 1 | 9 cache key | e2c9627 | a43b718 | sonnet DONE_W_CONCERNS: shared-DB flakiness only | wave review pending | merged be5d3ea |
| 1 | 10 storage | e2c9627 | d7f73e0 | sonnet DONE | wave review pending | merged be5d3ea |
| 2 | 8 cascade + acceptance | be5d3ea | | opus, wt v4-t8 | | running |
| 3 | 12 docs + report | | | | | |

## Cadence

Wave 1 runs seven implementers in parallel worktrees; reviews (spec, then quality) run on the merged wave, not per task.
Known merge points inside wave 1: tasks 2 and 7 each add one constant to `inspect/mask.py` that task 1 also adds — identical lines, resolved by taking task 1's file.

Wave 1 merged as be5d3ea: 1140 tests green on agentgate_test_v4, contracts clean. Reviews of the merged wave run in the background while task 8 starts.
