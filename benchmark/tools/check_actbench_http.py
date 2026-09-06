"""Check HTTP wiring against the real ActBench gateway, using a local fake guard."""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(source):
    from baselines.actbench_gateway import start_gateway

    state = {"decision": "deny", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, body):
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            self.reply({"status": "ok", "protocol": 1})

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, request))
            body = {
                "reason": "test policy",
                "suggest": "",
                "stage": 1,
                "rule_id": "client.deny",
                "model": None,
                "cached": False,
                "decision_id": "test-decision",
                "protocol": 1,
                "latency_ms": {"stage1": 0, "stage2": None, "total": 0},
            }
            if self.path == "/v1/decide":
                body["decision"] = state["decision"]
            else:
                body.update(verdict="mask", output="replacement over HTTP")
            self.reply(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    previous = os.environ.get("SECURITY_SERVICE_URL")
    os.environ["SECURITY_SERVICE_URL"] = f"http://127.0.0.1:{server.server_port}"
    process = None
    (ROOT / "results").mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=ROOT / "results") as temporary:
            output = Path(temporary)
            workspace = output / "task_B9_T01/workspace"
            workspace.mkdir(parents=True)
            try:
                process, env = start_gateway(source, output, "decide-inspect")
                url = env["ACTBENCH_MCP_URL"].removesuffix("/mcp")
                with httpx.Client(base_url=url, timeout=10) as client:
                    response = client.post(
                        "/admin/contexts",
                        headers={"Authorization": f"Bearer {env['ACTBENCH_MCP_ADMIN_TOKEN']}"},
                        json={"context_id": "http-probe", "workspace": str(workspace)},
                    )
                    response.raise_for_status()
                    params = {
                        "name": "actbench_write_file",
                        "arguments": {
                            "context_id": "http-probe",
                            "path": "probe.txt",
                            "content": "changed",
                        },
                    }

                    def call():
                        response = client.post(
                            "/mcp",
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "tools/call",
                                "params": params,
                            },
                        )
                        response.raise_for_status()
                        return response.json()["result"]

                    assert call()["isError"]
                    assert not (workspace / "probe.txt").exists()
                    state["decision"] = "allow"
                    result = call()
                    assert (workspace / "probe.txt").read_text() == "changed"
                    assert result["content"][0]["text"] == "replacement over HTTP"
                    events = [
                        json.loads(s)
                        for s in (output / "gate-events.jsonl").read_text().splitlines()
                    ]
                    assert not any(e["phase"] == "error" for e in events)
                    assert [p for p, _ in state["requests"]] == [
                        "/v1/decide",
                        "/v1/decide",
                        "/v1/inspect",
                    ]
            finally:
                if process is not None:
                    process.terminate()
                    process.wait(timeout=10)
    finally:
        if previous is None:
            os.environ.pop("SECURITY_SERVICE_URL", None)
        else:
            os.environ["SECURITY_SERVICE_URL"] = previous
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print(
        "ActBench HTTP integration passed: deny blocks, allow executes, inspect replacement delivered"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "results/vendor/actbench")
