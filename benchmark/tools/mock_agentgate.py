"""Заглушка AgentGate для проверки самого бенчмарка.

Это НЕ модель поведения сервиса и не бейзлайн. Единственная задача — отдать ответы в
формате контракта (`docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md` §4.3), чтобы
можно было прогнать конвейер бенчмарка целиком, пока сервис ещё не поднят. Решения тут
принимаются десятком грубых подстрочных правил; любые цифры, полученные против заглушки,
ничего не говорят о качестве AgentGate.

    python tools/mock_agentgate.py --port 8400
    SECURITY_SERVICE_URL=http://127.0.0.1:8400 python cli.py benchmark --path attacks/cases
"""

from __future__ import annotations

import argparse
import fnmatch
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


def _matches(rules: dict[str, Any], mode: str, command: str) -> bool:
    """Грубое glob-совпадение по сырой строке.

    Сервис матчит по канонической форме нормализованного действия, а не по сырой
    команде, и отказывается от `allow` при редиректах и подстановках. Заглушка этого
    не воспроизводит: она нужна только чтобы поле `rules` дошло до ответа.
    """
    return any(fnmatch.fnmatch(command, pattern) for pattern in rules.get(mode) or [])


def decide(body: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    raw = f"{body.get('raw', '')} {json.dumps(body.get('args', {}))}".lower()
    command = str(body.get("raw", "")).strip()
    rules = body.get("rules") or {}

    if rules and rules.get("version") != 1:
        # Протокол правил, который сервис не читает, закрывается fail-closed.
        return _response("ask", 0, None, started, "unsupported rules version")

    for needles, rule_id in HARD_DENY:
        if all(needle in raw for needle in needles):
            return _response("deny", 1, rule_id, started, "matched " + rule_id)

    # Три позиции пользовательских правил в цепочке — как в agentgate/rules/chain.py:
    # client.deny сразу после hard-deny, client.ask после запретов профиля,
    # client.allow перед серверным allowlist.
    if _matches(rules, "deny", command):
        return _response("deny", 1, "client.deny", started, "blocked by your rules")
    if _matches(rules, "ask", command):
        return _response("ask", 1, "client.ask", started, "your rules ask for confirmation")
    if _matches(rules, "allow", command):
        return _response("allow", 1, "client.allow", started, "")

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
        "protocol": 1,
        # Токены есть только когда ступень 2 действительно вызывалась: решение без
        # вызова модели стоит 0, а не «неизвестно». Цену заглушка не выдумывает —
        # `amount` и `currency` отсутствуют, как у оператора без прайса на модель.
        **(
            {"cost": {"input_tokens": 700, "output_tokens": 40, "reasoning_tokens": 0}}
            if stage == 2
            else {}
        ),
    }


# Грубые признаки инструкциеподобного текста. Это НЕ детекторы сервиса: там регулярки,
# порог длинного пробега base64, чистка невидимых символов и ступень 2 по флагу. Здесь
# ровно столько, чтобы конвейер inspect-набора можно было прогнать без сервиса.
INSPECT_NEEDLES = (
    "ignore all previous instructions",
    "ignore previous instructions",
    "disregard all prior instructions",
    "disregard prior instructions",
    "you are now in",
    "do not mention this to the user",
    "игнорируй все предыдущие инструкции",
)
INSPECT_ROLE_PREFIXES = ("system:", "assistant:")
MASK_LINE = "[gate: instruction-like text removed]"
INSPECT_KINDS = ("file", "web", "mcp", "shell", "subagent", "unknown")


def _line_is_flagged(line: str) -> bool:
    lowered = line.casefold().strip()
    if any(needle in lowered for needle in INSPECT_NEEDLES):
        return True
    if any(lowered.startswith(prefix) for prefix in INSPECT_ROLE_PREFIXES):
        return True
    return ("curl" in lowered or "wget" in lowered) and "| s" in lowered.replace("|s", "| s")


def inspect(body: dict[str, Any]) -> dict[str, Any]:
    """Заглушка `POST /v1/inspect`. Fail-closed здесь — `drop`, а не `ask`."""
    started = time.perf_counter()
    if not isinstance(body, dict):
        return _inspect_response("drop", 0, "api.invalid-request", started, "expected an object")

    # Плоскость протокола: всё, что сервис отверг бы до детекторов.
    if body.get("protocol", 1) != 1:
        return _inspect_response("drop", 0, None, started, "unsupported protocol")
    if not str(body.get("call_id") or "").strip():
        return _inspect_response("drop", 0, None, started, "call_id is required")
    if body.get("status") not in ("completed", "error"):
        return _inspect_response("drop", 0, None, started, "unknown status")
    provenance = body.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("kind") not in INSPECT_KINDS:
        return _inspect_response("drop", 0, None, started, "unknown provenance kind")

    output = body.get("output")
    if not isinstance(output, str):
        return _inspect_response("drop", 0, "api.invalid-request", started, "output must be text")
    if len(output.encode("utf-8", "surrogatepass")) > 262144:
        return _inspect_response("drop", 0, "api.output-too-large", started, "output too large")
    lines = output.split("\n")
    counted = len(lines) - 1 if output.endswith("\n") else len(lines)
    flagged = [n for n, line in enumerate(lines) if line and _line_is_flagged(line)]
    if not flagged:
        return _inspect_response("pass", 1, None, started, "")
    if len(flagged) > counted * 0.5:
        return _inspect_response(
            "drop",
            1,
            "inspect.injection",
            started,
            f"prompt injection detected in {len(flagged)} of {counted} lines",
        )
    flagged_set = set(flagged)
    rewritten = "\n".join(MASK_LINE if n in flagged_set else line for n, line in enumerate(lines))
    return _inspect_response(
        "mask",
        1,
        "inspect.injection",
        started,
        f"rewrote {len(flagged)} line(s) carrying instruction-like text",
        output=rewritten,
    )


def _inspect_response(
    verdict: str,
    stage: int,
    rule_id: str | None,
    started: float,
    reason: str,
    output: str | None = None,
) -> dict[str, Any]:
    total = (time.perf_counter() - started) * 1000
    return {
        "verdict": verdict,
        "output": output,
        "reason": reason,
        "suggest": "",
        "stage": stage,
        "rule_id": rule_id,
        "model": None,
        "latency_ms": {
            "stage1": round(total) if stage >= 1 else None,
            "stage2": None,
            "total": round(total),
        },
        "cached": False,
        "decision_id": uuid.uuid4().hex[:26].upper(),
        "protocol": 1,
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
        if self.path not in ("/v1/decide", "/v1/inspect"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length", 0))
        inspecting = self.path == "/v1/inspect"
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            # Fail-closed, exactly as the contract requires (section 4.4). The two routes
            # fail closed to different verdicts: decide has a human to ask, inspect does not.
            refusal = (
                _inspect_response("drop", 0, None, time.perf_counter(), "invalid request body")
                if inspecting
                else _response("ask", 0, None, time.perf_counter(), "invalid request body")
            )
            self._send(200, refusal)
            return
        self._send(200, inspect(body) if inspecting else decide(body))

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
