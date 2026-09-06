"""AgentGate Benchmark V1 — command line interface.

    python cli.py validate  --path attacks/cases
    python cli.py run       --case attacks/cases/data_exfiltration/DATA_EXFIL_003.yaml
    python cli.py benchmark --path attacks/cases --category data_exfiltration
    python cli.py benchmark --path attacks/cases
    python cli.py report    --run-id <uuid>
    python cli.py runs

The service endpoint comes from ``--url`` or ``SECURITY_SERVICE_URL`` / ``AGENTGATE_URL``
and defaults to a local address. Sending attack traffic to a non-local host requires an
explicit ``--allow-remote``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from automode.claude_code import ClaudeCodeAutomodeAdapter
from automode.server import ServerAutomodeAdapter
from client.security_service import SecurityServiceClient
from config import service_config_from_env
from dataset.loader import DatasetLoadError, load_dataset
from dataset.validator import difficulty_coverage, validate_dataset
from reporting.report import (
    build_summary,
    render_comparison,
    render_failures,
    render_text,
    write_reports,
)
from runner.executor import BenchmarkRunner
from runner.inspect_cli import add_inspect_commands
from runner.inspect_cli import command as inspect_command
from runner.recorder import Recorder
from schemas.case import DatasetSource
from schemas.result import ExecutionMode, HistoryMode, RunConfig
from schemas.rules import load_rules
from storage.sqlite import BenchmarkStore

DEFAULT_DATASET = "attacks/cases"
DEFAULT_DB = "results/benchmark.sqlite3"
DEFAULT_OUT = "results"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"})

logger = logging.getLogger("benchmark")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if not args.verbose:
        # one INFO line per request from httpx would bury the run output
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    match args.command:
        case "inspect" | "inspect-report":
            return inspect_command(args, _endpoint_allowed)
        case "validate":
            return _cmd_validate(args)
        case "run" | "benchmark":
            return _cmd_benchmark(args)
        case "report":
            return _cmd_report(args)
        case "compare":
            return _cmd_compare(args)
        case "runs":
            return _cmd_runs(args)
        case _:  # pragma: no cover - argparse guarantees a command
            parser.print_help()
            return 2


# ---------------------------------------------------------------- commands --


def _cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    report = validate_dataset(path, require_full_categories=not args.subset)

    for issue in report.issues:
        print(issue, file=sys.stderr)

    coverage = difficulty_coverage(report.cases)
    print(f"cases: {len(report.cases)} in {len(coverage)} categor(ies)")
    for category, difficulties in sorted(coverage.items()):
        print(f"  {category:<32} {len(difficulties):>2}  {', '.join(sorted(difficulties))}")
    sources = Counter(case.dataset_source.value for case in report.cases)
    print(
        "dataset sources: "
        + (", ".join(f"{name} {n}" for name, n in sorted(sources.items())) or "none")
    )
    print(f"errors: {len(report.errors)}   warnings: {len(report.warnings)}")

    if args.strict_warnings and report.warnings:
        return 1
    return 0 if report.ok else 1


def _cmd_benchmark(args: argparse.Namespace) -> int:
    dataset_path = Path(args.case) if args.command == "run" else Path(args.path)
    single_case = args.command == "run"

    validation = validate_dataset(
        dataset_path,
        require_full_categories=not (single_case or args.subset or _has_filters(args)),
    )
    if not validation.ok:
        for issue in validation.errors:
            print(issue, file=sys.stderr)
        print("dataset validation failed; nothing was sent to the service", file=sys.stderr)
        return 1

    try:
        cases = load_dataset(
            dataset_path,
            categories=args.category or None,
            difficulties=args.difficulty or None,
            case_ids=args.case_id or None,
            dataset_sources=args.dataset_source or None,
        )
    except DatasetLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not cases:
        print("no cases selected by the given filters", file=sys.stderr)
        return 1

    if args.rules:
        try:
            selected_rules = load_rules(args.rules)
        except (ValueError, OSError) as exc:
            print(f"invalid rules: {exc}", file=sys.stderr)
            return 2
        cases = [case.model_copy(update={"rules": case.rules or selected_rules}) for case in cases]

    if args.adapter == "claude-code" and any(case.rules for case in cases):
        print("client rules require --adapter server", file=sys.stderr)
        return 2

    if args.adapter == "claude-code":
        return _run_claude_code(args, cases, dataset_path)

    service_config = service_config_from_env(
        url=args.url,
        token=args.token,
        profile_id=args.profile_id,
        model=args.model,
        timeout_s=args.timeout,
        harness=args.harness,
        resolve_model_metadata=not args.no_model_lookup,
        pricing_table_path=args.pricing_table,
    )

    if not _endpoint_allowed(service_config.url, allow_remote=args.allow_remote):
        print(
            f"refusing to send attack traffic to non-local endpoint {service_config.url}.\n"
            "Pass --allow-remote if this endpoint is a test deployment you control.",
            file=sys.stderr,
        )
        return 2

    run_config = RunConfig(
        adapter_name=ServerAutomodeAdapter.name,
        service_url=service_config.url,
        profile_id=service_config.profile_id,
        model=service_config.model,
        harness=service_config.harness,
        concurrency=args.concurrency,
        timeout_s=service_config.timeout_s,
        strict_scoring=args.strict,
        category_filter=list(args.category or []),
        difficulty_filter=list(args.difficulty or []),
        case_filter=list(args.case_id or []),
        dataset_source_filter=list(args.dataset_source or []),
        dataset_path=str(dataset_path),
        pricing_table_path=service_config.pricing.source_path,
        session_mode=args.session_mode,
        execution_mode=ExecutionMode(args.execution_mode),
        history_mode=_history_mode(args),
        service_revision=args.service_revision,
    )

    if args.rules:
        run_config.rules = selected_rules.model_dump(mode="json")
        run_config.rules_digest = selected_rules.digest()

    if args.dry_run:
        print(f"{len(cases)} case(s) selected; dry run, no requests sent:")
        for case in cases:
            print(f"  {case.id:<38} {case.attack_category}/{case.difficulty.value}")
        return 0

    run_id = args.run_id or str(uuid.uuid4())
    store = None if args.no_db else BenchmarkStore(Path(args.db))
    out_dir = Path(args.out)

    try:
        results = asyncio.run(
            _execute(
                cases=cases,
                service_config=service_config,
                run_config=run_config,
                run_id=run_id,
                store=store,
                out_dir=out_dir,
                check_health=not args.no_health_check,
                progress=not args.quiet,
            )
        )
    finally:
        if store is not None:
            store.close()

    return _report_and_exit(results, run_config, run_id, out_dir, args, db_shown=store is not None)


def _run_claude_code(args: argparse.Namespace, cases: list, dataset_path: Path) -> int:
    """Composition root for the Claude Code adapter path.

    Kept separate from the server path on purpose: there is no service URL, no health
    check, and no ``--allow-remote`` gate here; instead there is a hard sandbox gate,
    because a classifier allow executes the case's command.
    """
    if not args.sandbox or not args.i_have_a_sandbox:
        print(
            "the claude-code adapter runs real commands when the classifier allows them.\n"
            "Pass --sandbox <dir> pointing at a disposable, network-isolated sandbox and "
            "--i-have-a-sandbox to affirm it. The dataset contains reverse shells and "
            "destructive removals; do not point this at a real machine.",
            file=sys.stderr,
        )
        return 2

    run_config = RunConfig(
        adapter_name=ClaudeCodeAutomodeAdapter.name,
        # RunConfig is still partly server-shaped (service_url has no default); record the
        # sandbox here so the stored run and the report header say what was measured.
        service_url=f"claude-code-sdk (sandbox: {args.sandbox})",
        model=args.claude_model,
        harness="claude-code",
        concurrency=args.concurrency,
        strict_scoring=args.strict,
        category_filter=list(args.category or []),
        difficulty_filter=list(args.difficulty or []),
        case_filter=list(args.case_id or []),
        dataset_source_filter=list(args.dataset_source or []),
        dataset_path=str(dataset_path),
        session_mode="per_case",
        execution_mode=ExecutionMode(args.execution_mode),
        history_mode=_history_mode(args),
        service_revision=args.service_revision,
    )

    if args.dry_run:
        print(f"{len(cases)} case(s) selected; dry run, no Claude Code sessions started:")
        for case in cases:
            print(f"  {case.id:<38} {case.attack_category}/{case.difficulty.value}")
        return 0

    run_id = args.run_id or str(uuid.uuid4())
    store = None if args.no_db else BenchmarkStore(Path(args.db))
    out_dir = Path(args.out)
    adapter = ClaudeCodeAutomodeAdapter(
        args.sandbox,
        sandbox_confirmed=True,
        model=args.claude_model,
        send_history=run_config.history_mode is HistoryMode.FULL,
    )

    try:
        results = asyncio.run(
            _run_with_adapter(
                adapter=adapter,
                cases=cases,
                run_config=run_config,
                run_id=run_id,
                store=store,
                out_dir=out_dir,
                progress=not args.quiet,
            )
        )
    finally:
        if store is not None:
            store.close()

    return _report_and_exit(results, run_config, run_id, out_dir, args, db_shown=store is not None)


def _report_and_exit(
    results: list,
    run_config: RunConfig,
    run_id: str,
    out_dir: Path,
    args: argparse.Namespace,
    *,
    db_shown: bool,
) -> int:
    summary = build_summary(results, run_id=run_id, config=run_config)
    paths = write_reports(results, summary, out_dir, run_id=run_id)

    print()
    print(render_text(summary))
    print(f"reports: {paths['summary_json']}, {paths['results_jsonl']}, {paths['failures_txt']}")
    if db_shown:
        print(f"database: {args.db}")

    failed = summary["totals"]["failed"]
    return 1 if failed and args.fail_on_error else 0


async def _run_with_adapter(
    *,
    adapter,
    cases: list,
    run_config: RunConfig,
    run_id: str,
    store: BenchmarkStore | None,
    out_dir: Path,
    progress: bool,
    service_version: str | None = None,
) -> list:
    """Store, stream and run one already-constructed adapter. Shared by every path."""
    if store is not None:
        store.upsert_cases(cases)
        store.start_run(run_id, run_config, total_cases=len(cases), service_version=service_version)

    if progress:
        print(f"run {run_id}: {len(cases)} case(s) -> adapter={adapter.name}")

    with Recorder(
        store=store,
        jsonl_path=out_dir / f"stream-{run_id}.jsonl",
        progress=progress,
    ) as recorder:
        runner = BenchmarkRunner(adapter, run_config, run_id=run_id, on_result=recorder.record)
        results = await runner.run(cases)

    if store is not None:
        store.finish_run(run_id)
    return results


async def _execute(
    *,
    cases: list,
    service_config,
    run_config: RunConfig,
    run_id: str,
    store: BenchmarkStore | None,
    out_dir: Path,
    check_health: bool,
    progress: bool,
) -> list:
    async with SecurityServiceClient(service_config) as client:
        service_version = None
        if check_health:
            healthy, payload = await client.healthz()
            if not healthy:
                logger.warning(
                    "health check on %s failed; running anyway (every case will be recorded "
                    "as an error if the service is down)",
                    service_config.health_url,
                )
            run_config.service_health = payload
            if payload:
                version = payload.get("version") or payload.get("service_version")
                service_version = str(version) if version else None

        run_config.profile_snapshot_digest = await client.profile_digest()
        adapter = ServerAutomodeAdapter(
            client,
            session_mode=run_config.session_mode,
            send_history=run_config.history_mode is HistoryMode.FULL,
        )
        return await _run_with_adapter(
            adapter=adapter,
            cases=cases,
            run_config=run_config,
            run_id=run_id,
            store=store,
            out_dir=out_dir,
            progress=progress,
            service_version=service_version,
        )


def _cmd_report(args: argparse.Namespace) -> int:
    with BenchmarkStore(Path(args.db)) as store:
        run_id = args.run_id or store.latest_run_id()
        if run_id is None:
            print("no runs stored yet", file=sys.stderr)
            return 1
        results = store.load_results(run_id)
        config = store.run_config(run_id)

    if not results:
        print(f"run {run_id} has no results", file=sys.stderr)
        return 1

    summary = build_summary(results, run_id=run_id, config=config)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(render_text(summary))
    if args.failures:
        print(render_failures(results))
    if args.out:
        paths = write_reports(results, summary, Path(args.out), run_id=run_id)
        print(f"reports written to {paths['summary_json'].parent}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    run_a, run_b = args.run_ids
    with BenchmarkStore(Path(args.db)) as store:
        results_a = store.load_results(run_a)
        results_b = store.load_results(run_b)
        config_a = store.run_config(run_a)
        config_b = store.run_config(run_b)

    missing = [rid for rid, res in ((run_a, results_a), (run_b, results_b)) if not res]
    if missing:
        print(f"no results stored for run(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    if args.json:
        from evaluator.metrics import compare_runs

        print(
            json.dumps(
                compare_runs(
                    results_a,
                    results_b,
                    history_ablation=args.history_ablation,
                    config_a=config_a,
                    config_b=config_b,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(
        render_comparison(
            results_a,
            results_b,
            label_a=run_a[:16],
            label_b=run_b[:16],
            history_ablation=args.history_ablation,
            config_a=config_a,
            config_b=config_b,
        )
    )
    return 0


def _cmd_runs(args: argparse.Namespace) -> int:
    with BenchmarkStore(Path(args.db)) as store:
        runs = store.list_runs(limit=args.limit)
    if not runs:
        print("no runs stored yet")
        return 0
    print(f"{'run_id':<38} {'started':<34} {'status':<10} {'passed':>7} {'executed':>9}")
    for run in runs:
        print(
            f"{run['run_id']:<38} {run['started_at']:<34} {run['status']:<10} "
            f"{run['passed']:>7} {run['executed']:>9}"
        )
    return 0


# ------------------------------------------------------------------ parser --


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py", description="AgentGate Benchmark V1 (direction 3)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    add_inspect_commands(sub)

    validate = sub.add_parser("validate", help="validate the benchmark dataset")
    validate.add_argument("--path", default=DEFAULT_DATASET)
    validate.add_argument(
        "--subset",
        action="store_true",
        help="skip the difficulty-coverage rules per category (validating a subset)",
    )
    validate.add_argument("--strict-warnings", action="store_true")

    run = sub.add_parser("run", help="run a single case file")
    run.add_argument("--case", required=True, help="path to one case YAML file")
    _add_execution_args(run)

    benchmark = sub.add_parser("benchmark", help="run the dataset (optionally filtered)")
    benchmark.add_argument("--path", default=DEFAULT_DATASET)
    benchmark.add_argument("--category", action="append", default=None)
    benchmark.add_argument("--difficulty", action="append", default=None)
    benchmark.add_argument("--case-id", action="append", default=None)
    benchmark.add_argument(
        "--dataset-source",
        action="append",
        choices=[source.value for source in DatasetSource],
        default=None,
        help="run only cases from this population (baseline / team); repeatable",
    )
    benchmark.add_argument(
        "--subset",
        action="store_true",
        help="skip the difficulty-coverage validation rules per category",
    )
    _add_execution_args(benchmark)

    report = sub.add_parser("report", help="rebuild a report from stored results")
    report.add_argument("--db", default=DEFAULT_DB)
    report.add_argument("--run-id")
    report.add_argument("--json", action="store_true")
    report.add_argument("--failures", action="store_true", help="print full failure details")
    report.add_argument("--out", help="also write report files to this directory")

    compare = sub.add_parser(
        "compare", help="compare two stored runs, guardrail-vs-guardrail (paired on shared cases)"
    )
    compare.add_argument("--db", default=DEFAULT_DB)
    compare.add_argument(
        "run_ids",
        nargs=2,
        metavar="RUN_ID",
        help="two run ids to compare (baseline then candidate)",
    )
    compare.add_argument("--json", action="store_true")
    compare.add_argument(
        "--history-ablation",
        action="store_true",
        help="compare full versus stripped history; other compatibility checks remain",
    )

    runs = sub.add_parser("runs", help="list stored runs")
    runs.add_argument("--db", default=DEFAULT_DB)
    runs.add_argument("--limit", type=int, default=20)

    return parser


def _add_execution_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--adapter",
        choices=("server", "claude-code"),
        default="server",
        help="which automode to measure: server (our AgentGate service, default) or "
        "claude-code (Claude Code's native auto-mode classifier via the Agent SDK)",
    )
    parser.add_argument(
        "--sandbox",
        help="claude-code only: workspace directory for the Claude Code sessions. MUST be a "
        "disposable sandbox with no secrets and no network egress — an allowed command runs.",
    )
    parser.add_argument(
        "--i-have-a-sandbox",
        action="store_true",
        help="claude-code only: affirm --sandbox is a disposable, network-isolated sandbox. "
        "Required, because a classifier allow executes the case's command.",
    )
    parser.add_argument(
        "--claude-model",
        help="claude-code only: model alias/id for the Claude Code session (default: the SDK's)",
    )
    parser.add_argument("--url", help="service base URL (env SECURITY_SERVICE_URL)")
    parser.add_argument("--token", help="bearer token (env SECURITY_SERVICE_TOKEN)")
    parser.add_argument("--profile-id", help="AgentGate profile_id to evaluate against")
    parser.add_argument("--model", help="stage-2 model configuration name")
    parser.add_argument("--rules", help="JSON/YAML client rules; case rules take precedence")
    parser.add_argument("--service-revision", help="deployed service commit or image digest")
    parser.add_argument("--harness", default="bench", help="value of the 'harness' request field")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request timeout, seconds")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--session-mode",
        choices=("per_case", "shared", "none"),
        default="per_case",
        help="per_case isolates the service cache and escalation counters (default)",
    )
    parser.add_argument(
        "--execution-mode",
        choices=[mode.value for mode in ExecutionMode],
        default=ExecutionMode.SINGLE_DECISION.value,
        help=(
            "what the run measures: single_decision (default) sends one action per case; "
            "harness_loop marks a run driven by a real harness, where wall clock covers the "
            "whole task including deny-retry and ask-wait loops"
        ),
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help=(
            "drop the dialogue history the cases carry and send the action alone. Pair it "
            "with a normal run and compare the two: the difference is what the history "
            "itself did to the decision"
        ),
    )
    parser.add_argument("--strict", action="store_true", help="score only the primary expectation")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--run-id")
    parser.add_argument("--pricing-table", help="YAML pricing table for cost computation")
    parser.add_argument("--no-model-lookup", action="store_true")
    parser.add_argument("--no-health-check", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="exit non-zero when any case fails (useful in CI)",
    )
    parser.set_defaults(
        subset=False, category=None, difficulty=None, case_id=None, dataset_source=None
    )


def _has_filters(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "category", None)
        or getattr(args, "difficulty", None)
        or getattr(args, "case_id", None)
        or getattr(args, "dataset_source", None)
    )


def _history_mode(args: argparse.Namespace) -> HistoryMode:
    """``--no-history`` strips the dialogue; the run records which of the two it was."""
    return HistoryMode.STRIPPED if getattr(args, "no_history", False) else HistoryMode.FULL


def _endpoint_allowed(url: str, *, allow_remote: bool) -> bool:
    if allow_remote:
        return True
    host = urlparse(url).hostname or ""
    return host in LOCAL_HOSTS


if __name__ == "__main__":
    raise SystemExit(main())
