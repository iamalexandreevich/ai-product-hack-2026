"""Unit tests for contracts/hook_client.py.

The client lives outside the `agentgate` package (it must stay stdlib-only
and importable by harnesses that never installed the service's
dependencies), so it is loaded here via `importlib` from its file path
rather than imported as a package.

Covers: both hook-format parsers (Claude Code PreToolUse, OpenCode
`tool.execute.before`), the decision -> exit-code mapping, and the
fail-closed path when the service is unreachable -- the one property task
12's brief calls out as the whole point of a client-side gate.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / "contracts" / "hook_client.py"


def _load_hook_client():
    spec = importlib.util.spec_from_file_location("hook_client", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hook_client():
    return _load_hook_client()


# --- Claude Code PreToolUse shape ---------------------------------------


def test_claude_code_bash_maps_to_shell(hook_client):
    hook = {
        "session_id": "s1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
        "cwd": "/tmp/work",
    }
    body = hook_client.to_request(hook, "fix the build", None)
    assert body["harness"] == "claude-code"
    assert body["tool"] == "shell"
    assert body["raw"] == "ls -la"
    assert body["args"]["cwd"] == "/tmp/work"
    assert body["session_id"] == "s1"
    assert body["user_request"] == "fix the build"
    assert "profile_id" not in body


def test_claude_code_write_maps_to_file_write(hook_client):
    hook = {
        "session_id": "s2",
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/work/a.py", "content": "x = 1"},
        "cwd": "/tmp/work",
    }
    body = hook_client.to_request(hook, "", None)
    assert body["tool"] == "file_write"
    assert body["args"]["paths"] == ["/tmp/work/a.py"]
    assert body["raw"] == ""


def test_claude_code_read_maps_to_file_read(hook_client):
    hook = {
        "session_id": "s3",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/work/a.py"},
        "cwd": "/tmp/work",
    }
    body = hook_client.to_request(hook, "", None)
    assert body["tool"] == "file_read"
    assert body["args"]["paths"] == ["/tmp/work/a.py"]


def test_claude_code_webfetch_maps_to_network(hook_client):
    hook = {
        "session_id": "s4",
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://example.com"},
        "cwd": "/tmp/work",
    }
    body = hook_client.to_request(hook, "", None)
    assert body["tool"] == "network"
    assert body["raw"] == "https://example.com"


def test_claude_code_unknown_tool_maps_to_mcp_call(hook_client):
    hook = {
        "session_id": "s5",
        "tool_name": "SomeMcpTool",
        "tool_input": {"foo": "bar"},
        "cwd": "/tmp/work",
    }
    body = hook_client.to_request(hook, "", None)
    assert body["tool"] == "mcp_call"
    assert body["args"]["mcp"] == {"server": "claude-code", "tool": "SomeMcpTool", "arguments": {}}


def test_claude_code_missing_cwd_falls_back_to_getcwd(hook_client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    hook = {"session_id": "s6", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    body = hook_client.to_request(hook, "", None)
    assert body["args"]["cwd"] == str(tmp_path)


# --- OpenCode tool.execute.before shape ---------------------------------


def test_opencode_bash_maps_to_shell(hook_client):
    hook = {
        "sessionID": "oc1",
        "tool": "bash",
        "args": {"command": "npm test", "cwd": "/work"},
    }
    body = hook_client.to_request(hook, "run tests", None)
    assert body["harness"] == "opencode"
    assert body["tool"] == "shell"
    assert body["raw"] == "npm test"
    assert body["args"]["cwd"] == "/work"
    assert body["session_id"] == "oc1"


def test_opencode_write_maps_to_file_write(hook_client):
    hook = {
        "sessionID": "oc2",
        "tool": "write",
        "args": {"filePath": "/work/a.py", "cwd": "/work"},
    }
    body = hook_client.to_request(hook, "", None)
    assert body["tool"] == "file_write"
    assert body["args"]["paths"] == ["/work/a.py"]


def test_opencode_unknown_tool_maps_to_mcp_call(hook_client):
    hook = {
        "sessionID": "oc3",
        "tool": "custom_mcp",
        "args": {"a": 1},
    }
    body = hook_client.to_request(hook, "", None)
    assert body["tool"] == "mcp_call"
    assert body["args"]["mcp"] == {"server": "opencode", "tool": "custom_mcp", "arguments": {}}


# --- profile_id passthrough and unrecognized payload --------------------


def test_profile_id_included_when_given(hook_client):
    hook = {"sessionID": "oc4", "tool": "bash", "args": {"command": "ls"}}
    body = hook_client.to_request(hook, "", "strict")
    assert body["profile_id"] == "strict"


def test_unrecognized_payload_raises(hook_client):
    with pytest.raises(ValueError):
        hook_client.to_request({"nonsense": True}, "", None)


# --- exit code mapping ---------------------------------------------------


def test_exit_code_mapping(hook_client):
    assert hook_client.EXIT == {"allow": 0, "deny": 2, "ask": 3}


# --- fail-closed when the service is unreachable ------------------------


def test_fail_closed_on_unreachable_service():
    """The client-side gate's core property: if the service can't be
    reached, the client must print an "ask" decision and exit 3 -- never
    0, and never a traceback.
    """
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/tmp", "session_id": "x"}
    )
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH), "--url", "http://127.0.0.1:1"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 3, proc.stderr
    data = json.loads(proc.stdout)
    assert data["decision"] == "ask"
    assert "agentgate unavailable" in data["reason"]
    assert proc.stderr == ""


def test_fail_closed_on_unreachable_service_never_exits_zero():
    payload = json.dumps(
        {"sessionID": "x", "tool": "bash", "args": {"command": "ls"}}
    )
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH), "--url", "http://127.0.0.1:1"],
        input=payload,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode != 0


# --- fail-closed on malformed/unrecognized stdin -------------------------
#
# These exercise the parse/mapping path (json.load + to_request), which
# runs *before* the service call. A crash here is not cosmetic: under
# Claude Code's PreToolUse exit-code semantics, exit 0 means allow and
# exit 2 means block, but any OTHER exit code (including the default 1
# from an uncaught exception) is a non-blocking error -- the tool
# proceeds. So an uncaught exception on bad stdin reads as fail-*open*,
# exactly the failure mode this client exists to prevent. Every one of
# these must exit exactly 3 (not merely "non-zero") and print an "ask"
# decision with no traceback on stderr.


def test_empty_stdin_fails_closed():
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH), "--url", "http://127.0.0.1:1"],
        input="",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 3, proc.stderr
    data = json.loads(proc.stdout)
    assert data["decision"] == "ask"
    assert proc.stderr == ""


def test_non_json_stdin_fails_closed():
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH), "--url", "http://127.0.0.1:1"],
        input="not json at all",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 3, proc.stderr
    data = json.loads(proc.stdout)
    assert data["decision"] == "ask"
    assert proc.stderr == ""


def test_unrecognized_hook_shape_fails_closed():
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH), "--url", "http://127.0.0.1:1"],
        input=json.dumps({"foo": "bar"}),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 3, proc.stderr
    data = json.loads(proc.stdout)
    assert data["decision"] == "ask"
    assert proc.stderr == ""
