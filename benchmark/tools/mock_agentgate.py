"""Заглушка AgentGate для проверки самого бенчмарка.

Это НЕ модель поведения сервиса и не бейзлайн. Единственная задача — отдать ответы в
формате контракта (`docs/superpowers/specs/2026-09-03-agentgate-v1-design.md` §4.3), чтобы
можно было прогнать конвейер бенчмарка целиком, пока сервис ещё не поднят. Решения тут
принимаются десятком грубых подстрочных правил; любые цифры, полученные против заглушки,
ничего не говорят о качестве AgentGate.

    python tools/mock_agentgate.py --port 8400
    SECURITY_SERVICE_URL=http://127.0.0.1:8400 python cli.py benchmark --path attacks/cases
"""

from __future__ import annotations

import argparse
import json
import random
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HARD_DENY = (
    (("curl", "|"), "hard-deny.pipe-exec"),
    (("wget", "|"), "hard-deny.pipe-exec"),
    ((".env",), "hard-deny.exfil"),
    (("id_rsa",), "hard-deny.exfil"),
    (("id_ed25519",), "hard-deny.exfil"),
    ((".aws",), "profile.path"),
    ((".ssh",), "profile.path"),
    (("rm -rf /",), "hard-deny.destructive"),
    (("sudo",), "hard-deny.privilege"),
    (("--force",), "hard-deny.git-force"),
    ((".claude",), "hard-deny.protected-write"),
)
ALLOWLIST_PREFIXES = ("pytest", "npm test", "npm run lint", "git status", "git diff", "ls ")

PROFILE = {
    "id": "default",
    "models": {
        "default": "mock",
        "configs": {"mock": {"base_url": "http://127.0.0.1:9/v1", "model": "mock-classifier-0.1"}},
    },
}


def decide(body: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    raw = f"{body.get('raw', '')} {json.dumps(body.get('args', {}))}".lower()

    for needles, rule_id in HARD_DENY:
        if all(needle in raw for needle in needles):
            return _response("deny", 1, rule_id, started, "matched " + rule_id)

    if any(raw.strip().startswith(prefix) for prefix in ALLOWLIST_PREFIXES):
        return _response("allow", 1, "allowlist.readonly", started, "")

    time.sleep(random.uniform(0.01, 0.05))  # pretend an LLM call happened
    return _response("ask", 2, None, started, "no rule matched, escalating to the user", "mock")


def _response(
    decision: str,
    stage: int,
    rule_id: str | None,
    started: float,
    reason: str,
    model: str | None = None,
) -> dict[str, Any]:
    total = (time.perf_counter() - started) * 1000
    return {
        "decision": decision,
        "reason": reason,
        "suggest": "",
        "stage": stage,
        "rule_id": rule_id,
        "model": model,
        "latency_ms": {
            "stage1": round(min(total, 1.0), 2),
            "stage2": round(total - 1.0, 2) if stage == 2 else None,
            "total": round(total, 2),
        },
        "cached": False,
        "decision_id": uuid.uuid4().hex[:26].upper(),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send(200, {"status": "ok", "version": "mock-0.1", "db": "n/a"})
        elif self.path.startswith("/v1/profiles/"):
            self._send(200, PROFILE)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/decide":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            # Fail-closed, exactly as the contract requires (section 4.4).
            self._send(200, _response("ask", 0, None, time.perf_counter(), "invalid request body"))
            return
        self._send(200, decide(body))

    def log_message(self, *args: object) -> None:
        pass

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8400)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"mock AgentGate on http://{args.host}:{args.port} (not a real gate)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
