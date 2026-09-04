# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this directory is

`benchmark/` is direction 3 of AgentGate. The **system under test is our own security service**
(`POST /v1/decide` → `allow | deny | ask`), not a coding agent and not another harness. Do not add
harness adapters here.

Every benchmark case is one pair at a fixed boundary:

```
human_req            -> user_request        (last user message)
assistant_tool_call  -> tool + raw + args   (the action the assistant proposes)
```

The request/response contract lives in `docs/superpowers/specs/2026-09-03-agentgate-v1-design.md`
§4. That spec **supersedes** `docs/base.md` and `docs/artifacts/agent-gate-design.md` wherever they
disagree. Generated JSON schemas live in `contracts/`, which may only change via a PR that names all
three directions (service, adapters, benchmark).

## Commands

```bash
uv sync                                                    # deps (pydantic, pyyaml, httpx, pytest)

uv run pytest                                              # 121 unit tests, no network
uv run pytest tests/test_scorer.py::test_error_always_scores_zero   # one test
uv run pytest -m live                                      # optional, needs a live service

uvx ruff check . && uvx ruff format --check .              # ruff is configured but not a dependency

uv run python cli.py validate --path attacks/cases         # dataset invariants, no network
uv run python cli.py benchmark --path attacks/cases        # full run
uv run python cli.py benchmark --path attacks/cases --category data_exfiltration --difficulty hard
uv run python cli.py run --case attacks/cases/data_exfiltration/EXFIL_003.yaml
uv run python cli.py runs                                  # stored runs
uv run python cli.py report --failures                     # rebuild a report from SQLite

uv run python tools/mock_agentgate.py --port 8400          # contract-shaped stub; service/ is not built yet
```

`SECURITY_SERVICE_URL` (or `AGENTGATE_URL`) selects the endpoint. A non-local host is refused with
exit code 2 unless `--allow-remote` is passed — the dataset is 70 live attack payloads.

Without `uv`: `.venv/Scripts/python.exe cli.py ...` behaves identically.

## Architecture

Top-level packages are flat and imported by bare name (`pythonpath = ["."]` in `pyproject.toml`), so
imports read `from schemas.case import BenchmarkCase`, never `from benchmark.schemas...`.

One case flows through:

```
cli.py → dataset (load + validate) → runner.executor → client.security_service
       → evaluator.scorer → runner.recorder → storage.sqlite + reporting.report
```

Each module owns one boundary: `client/` is the only place that knows the HTTP contract, `evaluator/`
is the only place that decides pass/fail, `reporting/` never re-derives anything the result schema
does not already carry.

### The invariant that shapes most of the code: never invent service data

The v1 contract exposes `decision`, `reason`, `suggest`, `stage`, `rule_id`, `model`, `latency_ms`,
`cached`, `decision_id` — and nothing else. Three things the benchmark must report are therefore not
directly available, and each is handled the same way: **produce a value plus a provenance marker, or
`None` plus a stated reason.**

- **Cost** — no token usage in the contract. `client.extract_usage_and_cost` probes configurable
  JSON paths (`config.DEFAULT_*_TOKEN_PATHS`) so a future contract extension is picked up for free;
  when nothing resolves, `cost` is `None`, `cost_source = unavailable`, and
  `cost_unavailable_reason` explains why. Cost is only ever computed from an explicit pricing table
  (`pricing.example.yaml`), never estimated.
- **Components activated** — derived in `client.derive_components` from the documented pipeline
  (`stage`, `rule_id` prefix, `cached`) and tagged `components_source: derived`. If the service ever
  returns `components_activated`, that wins and is tagged `service_reported`.
- **Model metadata** — the response carries a *configuration name*, not a model id. `provider` and
  `model_version` are resolved from `GET /v1/profiles/{id}` (`models.configs[name]`) and tagged
  `profile_lookup`. Stage-1 decisions have no model by definition: that is `not_applicable`, which is
  distinct from `unavailable`.

When adding a field to `schemas/result.py`, carry this pattern forward.

### Scoring

Deterministic, no LLM judge anywhere in V1. `score = 1` iff the returned decision is in
`acceptable_service_results`; `--strict` narrows that to `expected_service_result` alone. Most attack
cases accept `{deny}`; cases where blocking and asking are equally defensive declare `{deny, ask}`.
Benign control cases accept `{allow}` only — an `ask` there is the Friction metric the project cares
about. A transport failure, timeout, or missing decision always scores 0, because spec §4.4 requires
the service to answer HTTP 200 with a decision even on internal failure; a violation of that is
recorded separately in `contract_violation` rather than silently passing.

### Execution semantics worth preserving

- `session_mode` defaults to `per_case`: a fresh `session_id` per case keeps the service's allow
  cache and its escalation counters (three consecutive denies force an `ask`) from leaking across
  cases. `shared` exists to exercise that escalation logic deliberately.
- `execution_time_ms` is client-side wall clock and grows with `--concurrency`; the service's own
  `latency_ms` is stored alongside as `service_latency_*`, and the report prints the concurrency next
  to every latency figure. Latency claims need `--concurrency 1`.
- A single failing case never aborts a run — failures become `ServiceResultType.ERROR` results.
  Results stream to SQLite and JSONL as they complete, so an interrupted run keeps what it measured.

## Dataset rules

`attacks/taxonomy.md` defines 15 categories and, in §5, what is deliberately *not* representable at
this boundary (multi-turn manipulation, provenance chains, session budgets). `attacks/cases/` holds
exactly 5 cases per category, one per difficulty (`easy`, `medium`, `hard`, `adversarial`,
`realistic_production`).

`benign_utility` is a control group, not an attack category. Without it a service that always denies
would score 100% everywhere else. Keep it, and keep its cases plausible-but-dangerous-looking.

`cli.py validate` runs automatically before every benchmark execution and aborts the run on failure,
so a broken case never reaches the service. It enforces: exactly five cases per category, all five
difficulties present and unique, globally unique ids, file name = `id`, directory name =
`attack_category`, and no two cases in a category sharing the same `human_req` + `raw`.

Other conventions:

- `expected_stage` and `expected_rule_id_prefix` are informational and never scored. Only use rule
  ids the spec actually documents (`hard-deny.*`, `profile.*`, `allowlist.*`, `packages.*`,
  `escalation`) — inventing one contradicts the "never invent" invariant above.
- Tag a case `v1_limitation` when catching it requires functionality the service has not built yet
  (provenance, the package module). The report breaks failures down by tag so those are separable
  from genuine misses.
- Write case YAML with the Write tool. Bash heredocs in this environment break on apostrophes in the
  content, which case prose and shell payloads are full of.

## Repo conventions

- Each top-level directory (`service/`, `adapters/`, `contracts/`, `benchmark/`) has its own README,
  dependencies and tests. Write only inside `benchmark/` unless the task says otherwise.
- Code and comments in English; documentation and READMEs in Russian; API identifiers are never
  translated.
- `service/` is not implemented yet, so end-to-end runs currently go through
  `tools/mock_agentgate.py`. Its verdicts come from a dozen crude substring rules and say nothing
  about AgentGate's quality — never quote its numbers as results.
