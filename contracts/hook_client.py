#!/usr/bin/env python3
"""Reference AgentGate client: hook JSON on stdin -> decision on stdout.

Exit codes: 0 allow, 2 deny, 3 ask (also used when the service is unreachable).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

EXIT = {"allow": 0, "deny": 2, "ask": 3}


def to_request(hook: dict, user_request: str, profile: str | None) -> dict:
    # Claude Code PreToolUse
    if "tool_name" in hook:
        name = hook["tool_name"]
        ti = hook.get("tool_input", {})
        session = hook.get("session_id")
        cwd = hook.get("cwd") or os.getcwd()
        if name == "Bash":
            tool, raw, paths = "shell", ti.get("command", ""), []
        elif name in ("Write", "Edit", "MultiEdit"):
            tool, raw, paths = "file_write", "", [ti.get("file_path", "")]
        elif name == "Read":
            tool, raw, paths = "file_read", "", [ti.get("file_path", "")]
        elif name in ("WebFetch", "WebSearch"):
            tool, raw, paths = "network", ti.get("url", ""), []
        else:
            tool, raw, paths = "mcp_call", json.dumps(ti), []
        harness = "claude-code"
    # OpenCode tool.execute.before
    elif "sessionID" in hook:
        name = hook.get("tool", "")
        args = hook.get("args", {})
        session = hook.get("sessionID")
        cwd = args.get("cwd") or os.getcwd()
        if name == "bash":
            tool, raw, paths = "shell", args.get("command", ""), []
        elif name in ("write", "edit", "patch"):
            tool, raw, paths = "file_write", "", [args.get("filePath", "")]
        elif name == "read":
            tool, raw, paths = "file_read", "", [args.get("filePath", "")]
        elif name == "webfetch":
            tool, raw, paths = "network", args.get("url", ""), []
        else:
            tool, raw, paths = "mcp_call", json.dumps(args), []
        harness = "opencode"
    else:
        raise ValueError("unrecognized hook payload")
    body = {
        "protocol": 1,
        "session_id": session, "harness": harness, "tool": tool, "raw": raw,
        "args": {"cwd": cwd, "paths": [p for p in paths if p], "domains": []},
        "user_request": user_request,
        "metadata": {"hook_client": "0.1.0"},
    }
    if tool == "mcp_call":
        body["args"]["mcp"] = {"server": harness, "tool": name, "arguments": {}}
    if profile:
        body["profile_id"] = profile
    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-request", default=os.environ.get("AGENTGATE_USER_REQUEST", ""))
    ap.add_argument("--url", default=os.environ.get("AGENTGATE_URL", "http://127.0.0.1:8400"))
    ap.add_argument("--profile", default=os.environ.get("AGENTGATE_PROFILE"))
    opts = ap.parse_args()
    # Reading and mapping the hook's stdin is part of the fail-closed
    # boundary, not just the network call: empty stdin, non-JSON stdin, or
    # valid JSON in neither hook shape (to_request's ValueError) must all
    # come out as ask/3, never an uncaught traceback. That distinction
    # matters beyond aesthetics -- under Claude Code's PreToolUse exit-code
    # semantics, exit 0 is allow and exit 2 is block, but any *other* exit
    # code (the default 1 from an unhandled exception included) is a
    # non-blocking error and the tool proceeds. A crash here would read as
    # fail-open, which is the one thing this client must never do.
    try:
        hook = json.load(sys.stdin)
        body = to_request(hook, opts.user_request, opts.profile)
    except Exception as exc:  # noqa: BLE001 - any parse/mapping failure must fail closed, not crash
        data = {"decision": "ask", "reason": f"invalid hook input: {exc}", "suggest": ""}
        print(json.dumps(data, ensure_ascii=False))
        return EXIT.get(data.get("decision"), 3)
    req = urllib.request.Request(opts.url.rstrip("/") + "/v1/decide", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    token = os.environ.get("AGENTGATE_TOKEN")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        data = {"decision": "ask", "reason": f"agentgate unavailable: {exc}", "suggest": ""}
    print(json.dumps(data, ensure_ascii=False))
    return EXIT.get(data.get("decision"), 3)


if __name__ == "__main__":
    sys.exit(main())
