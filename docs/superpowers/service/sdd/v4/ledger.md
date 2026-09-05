# SDD ledger — AgentGate v4 Context Guard

Plan: docs/superpowers/service/plans/2026-09-05-agentgate-v4-context-guard.md
Branch: feat/v4-context-guard (worktree …/ai-product-hack-2026-wt/v4), from main 8ab67c5
Test DB: service-db-1 on localhost:5433, AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test_v4
Neighbor: feat/v3.1-strictness-mcp-domains runs in the main checkout (another session); merge order v3.1 → main → this branch before wave 2.

| Wave | Task | Base | Head | Implementer | Review | State |
|---|---|---|---|---|---|---|
| 0 | 0 foundation | 1be575d | e2c9627 | controller inline | — | **done**, 1044 green |
| 1 | 1 mask.apply | e2c9627 | | opus, wt v4-t1 | | running |
| 1 | 2 secrets | e2c9627 | | opus, wt v4-t2 | | running |
| 1 | 4 segments | e2c9627 | | sonnet, wt v4-t4 | | running |
| 1 | 5 prompt + schema | e2c9627 | | opus, wt v4-t5 | | running |
| 1 | 7 spans | e2c9627 | | sonnet, wt v4-t7 | | running |
| 1 | 9 cache key | e2c9627 | | sonnet, wt v4-t9 | | running |
| 1 | 10 storage | e2c9627 | | sonnet, wt v4-t10 | | running |
| 2 | 8 cascade + acceptance | | | | | |
| 3 | 12 docs + report | | | | | |

## Cadence

Wave 1 runs seven implementers in parallel worktrees; reviews (spec, then quality) run on the merged wave, not per task.
Known merge points inside wave 1: tasks 2 and 7 each add one constant to `inspect/mask.py` that task 1 also adds — identical lines, resolved by taking task 1's file.
