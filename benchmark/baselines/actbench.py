"""Pinned native ActBench runner. Never converts trajectory labels into action labels."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "baselines/actbench.lock.json").read_text(encoding="utf-8"))


def git(source, *args):
    return subprocess.check_output(
        ["git", "-C", str(source), *args], text=True, encoding="utf-8"
    ).strip()


def verify_source(source: Path) -> dict:
    if git(source, "rev-parse", "HEAD") != LOCK["revision"]:
        raise ValueError("ActBench revision differs from baselines/actbench.lock.json")
    if git(source, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("ActBench has modified tracked files; use an unmodified pinned checkout")
    tasks = sorted((source / "tasks").glob("task_B*_T*/task.yaml"))
    if len(tasks) != LOCK["task_pairs"]:
        raise ValueError(f"expected {LOCK['task_pairs']} task pairs, found {len(tasks)}")
    digest = hashlib.sha256()
    inventory = []
    for path in tasks:
        raw = path.read_bytes()
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(raw)
        task = yaml.safe_load(raw)
        clean = task.get("clean_source", {}).get("bundle_path")
        if not clean or not (source / "tasks" / clean).is_dir():
            raise ValueError(f"{task['id']}: missing matched clean source")
        inventory.append({"task_id": task["id"], "behavior_id": task["behavior_id"]})
    return {**LOCK, "task_manifest_digest": digest.hexdigest(), "inventory": inventory}


def native_command(source, output, *, self_test, model, suite, score_mode, judge_model):
    command = [
        sys.executable,
        str(source / "scripts/actbench_test.py"),
        "--tasks-dir",
        str(source / "tasks"),
        "--output-root",
        str(output),
    ]
    if self_test:
        return command + ["--self-test"]
    command += [
        "--backend",
        "claudecode",
        "--model",
        model,
        "--suite",
        suite,
        "--runs",
        "1",
        "--run-workers",
        "1",
        "--score-mode",
        score_mode,
    ]
    if judge_model:
        command += ["--judge-model", judge_model]
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "verify", "plan", "self-test", "run"])
    parser.add_argument("--source", type=Path, default=ROOT / "results/vendor/actbench")
    parser.add_argument("--output", type=Path, default=ROOT / "results/actbench")
    parser.add_argument("--model")
    parser.add_argument("--suite", default="representative")
    parser.add_argument("--guard", choices=["off", "decide", "decide-inspect"], default="off")
    parser.add_argument("--score-mode", choices=["automated", "combined-ags"], default="automated")
    parser.add_argument("--judge-model")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--service-model")
    args = parser.parse_args(argv)
    source = args.source.resolve()
    if args.command == "fetch" and not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--no-checkout", LOCK["repository"], str(source)], check=True
        )
        subprocess.run(
            ["git", "-C", str(source), "checkout", "--detach", LOCK["revision"]], check=True
        )
    provenance = verify_source(source)
    if args.command in ("fetch", "verify"):
        print(json.dumps({k: v for k, v in provenance.items() if k != "inventory"}, indent=2))
        return 0
    self_test = args.command == "self-test"
    if not self_test and not args.model:
        parser.error("--model is required for a real run or plan")
    if args.score_mode == "combined-ags" and not args.judge_model:
        parser.error("combined-ags requires an explicit --judge-model")
    if args.score_mode == "automated" and args.judge_model:
        parser.error("--judge-model is only used with combined-ags")
    if self_test and args.guard != "off":
        parser.error(
            "self-test exercises upstream plumbing; use tools/check_actbench_gate.py for guard checks"
        )
    if args.command == "run" and not Path("/.dockerenv").exists():
        parser.error(
            "live Claude Code runs require the disposable Docker sandbox; see baselines/README.md"
        )
    if args.guard != "off":
        from cli import _endpoint_allowed

        url = os.environ.get("SECURITY_SERVICE_URL") or os.environ.get("AGENTGATE_URL")
        if not url or not _endpoint_allowed(url, allow_remote=args.allow_remote):
            parser.error("set a local service URL, or explicitly pass --allow-remote")
    output = args.output.resolve() / uuid.uuid4().hex
    command = native_command(
        source,
        output,
        self_test=self_test,
        model=args.model,
        suite=args.suite,
        score_mode=args.score_mode,
        judge_model=args.judge_model,
    )
    plan = {
        "source": provenance,
        "guard": args.guard,
        "command": command,
        "profile": args.profile,
        "service_model": args.service_model,
        "self_test": self_test,
        "execution_mode": "harness_task",
    }
    if args.command == "plan":
        print(json.dumps(plan, indent=2))
        return 0
    output.mkdir(parents=True)
    (output / "source.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        ACTBENCH_BASELINE_CACHE_DIR=str(output / "clean-cache"),
        ACTBENCH_CLAUDECODE_TOOLS="",
        ACTBENCH_CLAUDECODE_ENABLE_ACTBENCH_MCP="1",
        ACTBENCH_CLAUDECODE_ALLOWED_TOOLS=",".join(
            f"mcp__actbench__actbench_{name}"
            for name in ("list_files", "read_file", "write_file", "get_api_endpoints", "call_api")
        ),
    )
    gateway = None
    try:
        if args.guard != "off":
            from baselines.actbench_gateway import start_gateway

            gateway, gateway_env = start_gateway(
                source, output, args.guard, profile=args.profile, model=args.service_model
            )
            env.update(gateway_env)
        result = subprocess.run(command, cwd=source, env=env, check=False)
        if result.returncode or self_test or not args.judge_model:
            return result.returncode
        # Native utility scoring needs clean execution evidence and an explicit
        # judge. Automated-only runs keep utility unavailable instead of guessing it.
        invocation = next(output.glob("*/one_click_result.json"))
        manifest = json.loads(invocation.read_text(encoding="utf-8"))
        collection = invocation.parent / manifest["collection"]["result_path"]
        commands = [
            [
                sys.executable,
                str(source / "scripts/actbench_pack_raw_by_task.py"),
                "--result",
                str(collection),
                "--dataset-name",
                "measured",
                "--raw-by-task-root",
                str(output / "raw-by-task"),
            ],
            [
                sys.executable,
                str(source / "scripts/actbench_utility_score.py"),
                "--raw-by-task-root",
                str(output / "raw-by-task"),
                "--raw-by-task-dataset",
                "measured",
                "--judge-model",
                args.judge_model,
                "--output",
                str(output / "utility.json"),
            ],
        ]
        for index, native in enumerate(commands):
            with (output / f"utility-{index}.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    native, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, check=False
                )
            if result.returncode:
                return result.returncode
        return 0
    finally:
        if gateway is not None:
            gateway.terminate()
            try:
                gateway.wait(timeout=10)
            except subprocess.TimeoutExpired:
                gateway.kill()
                gateway.wait()
