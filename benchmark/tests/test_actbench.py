import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from baselines.actbench import LOCK, native_command
from baselines.actbench_gateway import GuardedTools


def test_native_plan_preserves_task_scoring():
    command = native_command(
        Path("source"),
        Path("out"),
        self_test=False,
        model="test-model",
        suite="representative",
        score_mode="automated",
        judge_model=None,
    )
    assert command[1].endswith("actbench_test.py")
    assert command[command.index("--backend") + 1] == "claudecode"
    assert "--skip-baseline-gen" not in command
    assert LOCK["evaluation_boundary"] == "harness_task"
    assert len(LOCK["revision"]) == 40


@pytest.fixture
def rig(tmp_path):
    workspace = tmp_path / "task_B3_T01" / "workspace"
    workspace.mkdir(parents=True)
    tasks = tmp_path / "tasks"
    (tasks / "task_B3_T01").mkdir(parents=True)
    (tasks / "task_B3_T01/task.yaml").write_text("prompt: Summarize the notes.\n")
    context = SimpleNamespace(context_id="opaque-private-context", workspace=workspace)
    trace = []

    def execute(**arguments):
        (workspace / "side-effect.txt").write_text("changed")
        return {"text": "untrusted original"}

    gateway = SimpleNamespace(
        REGISTRY=SimpleNamespace(get=lambda _: context),
        TOOL_HANDLERS={"actbench_write_file": execute},
        _mcp_tool_result=lambda value: {
            "content": [{"type": "text", "text": json.dumps(value)}],
            "isError": False,
        },
        _mcp_tool_error=lambda value: {
            "content": [{"type": "text", "text": value}],
            "isError": True,
        },
        _maybe_record_tool_trace=lambda **entry: trace.append(entry),
    )
    params = {
        "name": "actbench_write_file",
        "arguments": {
            "context_id": context.context_id,
            "path": "side-effect.txt",
            "content": "changed",
        },
    }
    return gateway, tasks, workspace, params, trace


@pytest.mark.parametrize("decision", ["deny", "ask", "invalid"])
def test_gate_stops_side_effects_and_counts_interventions(rig, decision):
    gateway, tasks, workspace, params, trace = rig
    events = []
    guard = GuardedTools(gateway, tasks, lambda *_: {"decision": decision}, events.append)
    result = guard.dispatch(params)
    assert result["isError"]
    assert not (workspace / "side-effect.txt").exists()
    assert events[0]["response"]["decision"] == decision
    assert trace[-1]["result"] == result


@pytest.mark.parametrize("verdict", ["pass", "mask", "drop", "invalid"])
def test_agent_and_next_turn_see_only_delivered_output(rig, verdict):
    gateway, tasks, workspace, params, trace = rig
    requests = []

    def post(kind, body):
        requests.append((kind, body))
        return (
            {"decision": "allow"}
            if kind == "decide"
            else {"verdict": verdict, "output": "sanitized", "reason": "withheld"}
        )

    guard = GuardedTools(gateway, tasks, post, lambda _: None)
    result = guard.dispatch(params)
    assert (workspace / "side-effect.txt").exists()
    assert requests[0][1]["call_id"] == requests[1][1]["call_id"]
    assert "opaque-private-context" not in json.dumps(requests)
    delivered = result["content"][0]["text"]
    if verdict == "mask":
        assert delivered == "sanitized"
    elif verdict != "pass":
        assert result["isError"]
        assert "untrusted original" not in delivered
    guard.dispatch(params)
    assert requests[2][1]["history"][-1]["content"] == delivered
    assert trace[0]["result"] == result


def test_transport_failure_is_recorded_and_never_executes(rig):
    gateway, tasks, workspace, params, _ = rig
    events = []

    def failed(*_):
        raise TimeoutError("private transport detail")

    result = GuardedTools(gateway, tasks, failed, events.append).dispatch(params)
    assert result["isError"]
    assert events == [{"phase": "error", "error": "TimeoutError"}]
    assert not (workspace / "side-effect.txt").exists()


def test_multi_session_task_is_explicitly_unsupported(rig):
    gateway, tasks, workspace, params, _ = rig
    (tasks / "task_B3_T01/task.yaml").write_text("prompt: first\nsessions: [first, second]\n")
    result = GuardedTools(
        gateway, tasks, lambda *_: pytest.fail("must not call service"), lambda _: None
    ).dispatch(params)
    assert result["isError"]
    assert not (workspace / "side-effect.txt").exists()
