"""Composition and reporting for the separate inspect suite."""

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path

import yaml

from client.inspect import build_inspect_request
from client.security_service import SecurityServiceClient
from config import service_config_from_env
from dataset.inspect_validator import tier_coverage, validate_inspect_dataset
from dataset.loader import DatasetLoadError
from evaluator.inspection import summarize_inspections
from runner.inspection import run_inspections
from storage.inspection import load_inspections as load_stored
from storage.inspection import save_inspections


def add_inspect_commands(sub) -> None:
    parser = sub.add_parser("inspect", help="evaluate recorded tool results using /v1/inspect")
    parser.add_argument("--path", default="attacks/inspect")
    parser.add_argument("--url")
    parser.add_argument("--token")
    parser.add_argument("--profile-id")
    parser.add_argument(
        "--model", help="unsupported for inspect; use a profile with models.default"
    )
    parser.add_argument("--service-revision")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--pricing-table")
    parser.add_argument("--cache-mode", choices=("cold", "warm", "observe"), default="cold")
    parser.add_argument(
        "--paired",
        action="store_true",
        help="gate pre_action before replaying its recorded output; no tools execute",
    )
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--out", default="results")
    parser.add_argument("--db", default="results/benchmark.sqlite3")
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="check the corpus and exit; validation runs before every execution anyway",
    )
    report = sub.add_parser("inspect-report", help="rebuild an inspection report from SQLite")
    report.add_argument("--db", default="results/benchmark.sqlite3")
    report.add_argument("--run-id", required=True)
    report.add_argument("--out")
    report.set_defaults(validate_only=False)


def command(args: argparse.Namespace, endpoint_allowed) -> int:
    try:
        if args.command == "inspect-report":
            config, results = load_stored(Path(args.db), args.run_id)
            summary = summarize_inspections(results, config)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            if args.out:
                write_reports(Path(args.out), args.run_id, results, summary)
            return 0
        # Validation is not optional and not a separate command the caller may forget:
        # the decide path aborts a run on an invalid dataset, and this path does too.
        path = Path(args.path)
        # A single file or category is a deliberate subset. Layout and schema checks
        # still apply; corpus-wide provenance and control coverage cannot apply there.
        full_corpus = path.is_dir() and any(p.is_dir() for p in path.iterdir())
        report = validate_inspect_dataset(path, require_full_corpus=full_corpus)
        for issue in report.issues:
            print(str(issue), file=sys.stderr)
        if not report.ok:
            print(f"dataset invalid: {len(report.errors)} error(s)", file=sys.stderr)
            return 1
        if args.validate_only:
            print(f"cases: {len(report.cases)}, errors: 0, warnings: {len(report.warnings)}")
            for category, tiers in tier_coverage(report.cases).items():
                print(f"  {category}: {', '.join(tiers)}")
            return 0

        cases = report.cases
        service = service_config_from_env(
            url=args.url,
            token=args.token,
            profile_id=args.profile_id,
            model=args.model,
            timeout_s=args.timeout,
            pricing_table_path=args.pricing_table,
        )
        if service.model:
            raise ValueError(
                "inspect has no model override: unset AGENTGATE_MODEL/--model and "
                "select --profile-id whose models.default is the desired classifier"
            )
        if not endpoint_allowed(service.url, allow_remote=args.allow_remote):
            raise ValueError("remote endpoint requires --allow-remote")
        if args.paired and any(c.pre_action is None or c.api_refusal for c in cases):
            raise ValueError(
                "paired replay needs pre_action on every case and no API refusal cases"
            )
        if args.dry_run:
            client = SecurityServiceClient(service)
            for case in cases:
                print(
                    json.dumps(
                        build_inspect_request(
                            case,
                            client,
                            session_id="dry-run",
                            call_id=case.id,
                            send_history=not args.no_history,
                        ),
                        ensure_ascii=False,
                    )
                )
            return 0
        run_id = args.run_id or str(uuid.uuid4())
        check_run_id(run_id)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)

        async def execute():
            async with SecurityServiceClient(service) as client:
                _, health = await client.healthz()
                profile = await client.profile_snapshot()
                config = {
                    "run_id": run_id,
                    "suite": "inspect",
                    "scoring_version": 2,
                    "dataset_digest": hashlib.sha256(
                        json.dumps(
                            [case.model_dump(mode="json") for case in cases],
                            sort_keys=True,
                            ensure_ascii=True,
                        ).encode()
                    ).hexdigest(),
                    "service_url": service.url,
                    "service_revision": args.service_revision,
                    "service_health": health,
                    "profile_id": service.profile_id or "default",
                    "profile_snapshot_digest": hashlib.sha256(
                        json.dumps(profile, sort_keys=True).encode()
                    ).hexdigest()
                    if profile
                    else None,
                    "profile_models": profile.get("models") if profile else None,
                    "inspect_settings": profile.get("inspect") if profile else None,
                    "cache_mode": args.cache_mode,
                    "concurrency": 1,
                    "paired": args.paired,
                    "history_mode": "stripped" if args.no_history else "full",
                    "dataset_path": args.path,
                    "timeout_s": service.timeout_s,
                    "pricing_table": args.pricing_table,
                }
                # Exclusive creation protects a previous or interrupted run. Flush each
                # measurement before sending another request so cancellation loses none.
                with (out / f"inspect-{run_id}.jsonl").open("x", encoding="utf-8") as stream:

                    def record(result):
                        stream.write(result.model_dump_json() + "\n")
                        stream.flush()

                    results = await run_inspections(
                        cases,
                        client,
                        run_id=run_id,
                        cache_mode=args.cache_mode,
                        paired=args.paired,
                        send_history=not args.no_history,
                        on_result=record,
                    )
                return config, results

        config, results = asyncio.run(execute())
        summary = summarize_inspections(results, config)
        write_reports(Path(args.out), run_id, results, summary)
        if not args.no_db:
            save_inspections(Path(args.db), run_id, config, results)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return int(args.fail_on_error and any(not r.score for r in results))
    except (ValueError, OSError, DatasetLoadError, yaml.YAMLError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def check_run_id(run_id: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", run_id):
        raise ValueError("run-id must contain 1–128 letters, digits, hyphens or underscores")


def write_reports(out: Path, run_id: str, results: list, summary: dict) -> None:
    check_run_id(run_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"inspect-{run_id}.jsonl").write_text(
        "".join(r.model_dump_json() + "\n" for r in results),
        encoding="utf-8",
    )
    (out / f"inspect-summary-{run_id}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
