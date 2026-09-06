"""Gate the pinned ActBench MCP gateway at its actual execution boundary.

Only public tool calls/results enter the history. Tool definitions and task graders
never enter the service prompt. This experiment uses MCP tools exclusively.
"""

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml


def validate_response(kind, payload):
    from jsonschema import Draft202012Validator

    contract = Path(__file__).resolve().parents[2] / "contracts" / f"{kind}_response.schema.json"
    Draft202012Validator(json.loads(contract.read_text(encoding="utf-8"))).validate(payload)
    if payload.get("protocol") != 1:
        raise ValueError("unsupported service protocol")


class GuardedTools:
    def __init__(self, gateway, tasks: Path, post, record, *, inspect_outputs=True):
        self.gateway, self.tasks = gateway, tasks
        self.post, self.record = post, record
        self.inspect_outputs = inspect_outputs
        self.histories = {}

    def dispatch(self, params):
        gate = self.gateway
        try:
            name, arguments = params["name"], params["arguments"]
            context = gate.REGISTRY.get(arguments["context_id"])
            if name not in gate.TOOL_HANDLERS:
                raise ValueError("unsupported ActBench tool")
            # The pinned backend names its workspace parent after the task. The
            # benign counterpart carries the same prompt and a _baseline suffix.
            task_id = context.workspace.parent.name.removesuffix("_baseline")
            task_path = self.tasks / task_id / "task.yaml"
            if task_path.resolve().parent.parent != self.tasks.resolve():
                raise ValueError("invalid task identity")
            task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
            if task.get("sessions"):
                raise ValueError("multi-session tasks need explicit current-turn attribution")
            session = hashlib.sha256(context.context_id.encode()).hexdigest()
            history = self.histories.setdefault(session, [])
            call_id = uuid.uuid4().hex
            public_args = {k: v for k, v in arguments.items() if k != "context_id"}
            body = {
                "protocol": 1,
                "session_id": session,
                "call_id": call_id,
                "harness": "actbench",
                "tool": "mcp_call",
                "raw": json.dumps(public_args, ensure_ascii=False),
                "args": {
                    "cwd": str(context.workspace),
                    "mcp": {"server": "actbench", "tool": name, "arguments": public_args},
                },
                "user_request": task["prompt"],
                "history": list(history),
            }
            decision = self.post("decide", body)
            event = {
                "session_id": session,
                "task_id": task_id,
                "call_id": call_id,
                "role": "benign"
                if context.workspace.parent.name.endswith("_baseline")
                else "attack",
                "phase": "decide",
                "response": decision,
            }
            self.record(event)
            if decision.get("decision") != "allow":
                result = gate._mcp_tool_error(
                    decision.get("reason") or "AgentGate withheld this action"
                )
            else:
                try:
                    result = gate._mcp_tool_result(gate.TOOL_HANDLERS[name](**arguments))
                except Exception as exc:  # noqa: BLE001 - tool failures are inspectable output
                    result = gate._mcp_tool_error(str(exc))
                if self.inspect_outputs:
                    output = "\n".join(b["text"] for b in result["content"])
                    request = {k: v for k, v in body.items() if k != "raw"}
                    request.update(
                        tool_name=name,
                        output=output,
                        status="error" if result.get("isError") else "completed",
                        provenance={"kind": "mcp", "server": "actbench", "tool": name},
                    )
                    inspection = self.post("inspect", request)
                    self.record({**event, "phase": "inspect", "response": inspection})
                    verdict = inspection.get("verdict")
                    if verdict == "mask" and isinstance(inspection.get("output"), str):
                        result = {
                            "content": [{"type": "text", "text": inspection["output"]}],
                            "isError": result.get("isError", False),
                        }
                    elif verdict != "pass":
                        result = gate._mcp_tool_error(
                            inspection.get("reason") or "AgentGate withheld this result"
                        )
            history.extend(
                [
                    {
                        "role": "toolcall",
                        "author": "agent",
                        "tool": name,
                        "call_id": call_id,
                        "content": body["raw"],
                    },
                    {
                        "role": "toolresult",
                        "author": "system",
                        "tool": name,
                        "call_id": call_id,
                        "content": "\n".join(b["text"] for b in result["content"]),
                    },
                ]
            )
            gate._maybe_record_tool_trace(name=name, arguments=arguments, result=result)
            return result
        except Exception as exc:  # noqa: BLE001 - every integration failure must stop execution
            # The failure is observable separately from an intentional policy denial.
            self.record({"phase": "error", "error": type(exc).__name__})
            return gate._mcp_tool_error("AgentGate integration failed closed")


def serve(source: Path, output: Path, port: int, mode: str):
    from baselines.actbench import verify_source

    verify_source(source)
    sys.path.insert(0, str(source / "scripts"))
    import lib_mcp_gateway as gateway
    import uvicorn

    url = os.environ.get("SECURITY_SERVICE_URL") or os.environ.get("AGENTGATE_URL")
    if not url:
        raise ValueError("guarded runs require SECURITY_SERVICE_URL or AGENTGATE_URL")
    parsed_url = urlsplit(url)
    public_url = parsed_url._replace(
        netloc=parsed_url.netloc.rsplit("@", 1)[-1], query="", fragment=""
    ).geturl()
    headers = {}
    token = os.environ.get("SECURITY_SERVICE_TOKEN") or os.environ.get("AGENTGATE_TOKEN")
    if token:
        headers["authorization"] = f"Bearer {token}"
    log = output / "gate-events.jsonl"

    def record(event):
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    with httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=30) as client:
        profile = os.environ.get("ACTBENCH_GATE_PROFILE")
        model = os.environ.get("ACTBENCH_GATE_MODEL")
        health = client.get("/healthz")
        health.raise_for_status()
        profile_response = client.get(f"/v1/profiles/{profile or 'default'}")
        profile_response.raise_for_status()
        (output / "service.json").write_text(
            json.dumps(
                {
                    "url": public_url,
                    "health": health.json(),
                    "profile": profile,
                    "model": model,
                    "profile_digest": hashlib.sha256(
                        json.dumps(profile_response.json(), sort_keys=True).encode()
                    ).hexdigest(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        def post(kind, body):
            if profile:
                body = {**body, "profile_id": profile}
            if kind == "decide" and model:
                body = {**body, "model": model}
            started = time.perf_counter()
            response = client.post(f"/v1/{kind}", json=body)
            if response.status_code != 200:
                raise ValueError(f"service HTTP {response.status_code}")
            payload = response.json()
            validate_response(kind, payload)
            record(
                {
                    "phase": "latency",
                    "kind": kind,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                }
            )
            return payload

        guarded = GuardedTools(
            gateway, source / "tasks", post, record, inspect_outputs=mode == "decide-inspect"
        )
        gateway._dispatch_tool_call = guarded.dispatch
        uvicorn.run(gateway.app, host="127.0.0.1", port=port, log_level="warning")


def start_gateway(source: Path, output: Path, mode: str, *, profile=None, model=None):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env = {
        "ACTBENCH_MCP_AUTOSTART": "0",
        "ACTBENCH_MCP_PORT": str(port),
        "ACTBENCH_MCP_HOST": "127.0.0.1",
        "ACTBENCH_MCP_URL": f"http://127.0.0.1:{port}/mcp",
        "ACTBENCH_MCP_ADMIN_TOKEN": token,
    }
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "tools/actbench_gateway.py"),
        "--source",
        str(source),
        "--output",
        str(output),
        "--port",
        str(port),
        "--mode",
        mode,
    ]
    process = subprocess.Popen(
        command,
        env={
            **os.environ,
            **env,
            "ACTBENCH_GATE_PROFILE": profile or "",
            "ACTBENCH_GATE_MODEL": model or "",
        },
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("guarded gateway failed to start")
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
                if response.status_code == 200:
                    return process, env
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError("guarded gateway health check timed out")
    except BaseException:
        process.terminate()
        process.wait(timeout=10)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--mode", choices=["decide", "decide-inspect"], required=True)
    args = parser.parse_args()
    serve(args.source, args.output, args.port, args.mode)
