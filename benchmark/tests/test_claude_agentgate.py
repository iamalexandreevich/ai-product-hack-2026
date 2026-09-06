"""SDK hook orchestration with a scripted SDK; no model or recorded tool executes."""

import asyncio
import copy
import importlib.metadata
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from automode.claude_agentgate import ClaudeAgentGateAdapter
from automode.claude_code import ClaudeCodeAutomodeAdapter, plan_for
from automode.gate_bridge import GateBridge, bridge_command
from automode.sdk import run_claude_session
from cli import main
from config import ServiceConfig
from schemas.case import BenchmarkCase
from schemas.rules import load_rules
from tests.conftest import HISTORY_CASE, VALID_CASE


def sample():
    return BenchmarkCase.model_validate(copy.deepcopy(VALID_CASE))


class Bridge:
    config = ServiceConfig()

    def __init__(self, decision="allow", verdict="mask", *, available=True):
        self.decision, self.verdict, self.available = decision, verdict, available
        self.calls = []

    async def request(self, **payload):
        self.calls.append(payload)
        if payload["operation"] == "inspect":
            return {
                "result": {"ok": True},
                "policy": {
                    "action": "pass" if self.verdict == "pass" else "replace",
                    "output": "clean",
                },
            }
        return {
            "request": {},
            "result": {
                "ok": self.available,
                "value": {
                    "decision": self.decision,
                    "stage": 2,
                    "model": "primary",
                    "cost": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "amount": 0.01,
                        "currency": "USD",
                    },
                },
            },
            "policy": {"status": self.decision},
            "deny_message": "gate decision",
        }


@pytest.fixture
def sdk(monkeypatch):
    state = SimpleNamespace(
        options=None,
        post=None,
        second=None,
        callback=False,
        propose=True,
        later_denial=False,
        fail=False,
        delay=False,
        tool_response="original secret",
    )

    class Result:
        def __init__(self, denials):
            self.total_cost_usd = 0.2
            self.session_id = "sdk-session"
            self.duration_ms = 10
            self.model_usage = {"agent-model": {"costUSD": 0.2, "inputTokens": 100}}
            self.usage = {"input_tokens": 100, "output_tokens": 5}
            self.permission_denials = denials

    async def query(*, prompt, options):
        state.options = options
        async for _ in prompt:
            pass
        if state.delay:
            await asyncio.sleep(10)
        denials = []
        if state.propose:
            event = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "session_id": "sdk-session",
                "tool_input": {"command": "proposal"},
            }
            pre = options.hooks["PreToolUse"][0].hooks[0]
            out = await pre(event, "first", {})
            state.pre = out
            event["tool_input"] = out["hookSpecificOutput"].get("updatedInput", event["tool_input"])
            permission = out["hookSpecificOutput"].get("permissionDecision")
            if permission == "deny":
                denials.append({**event, "tool_use_id": "first"})
            elif state.callback:
                await options.can_use_tool("Bash", event["tool_input"], {})
            else:
                event["hook_event_name"] = "PostToolUseFailure" if state.fail else "PostToolUse"
                event["tool_response"] = state.tool_response
                state.post = await options.hooks[event["hook_event_name"]][0].hooks[0](
                    event, "first", {}
                )
            state.second = await pre({**event, "hook_event_name": "PreToolUse"}, "second", {})
            if state.later_denial:
                denials.append({**event, "tool_use_id": "second"})
        yield Result(denials)

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(
            ClaudeAgentOptions=lambda **kw: SimpleNamespace(**kw),
            HookMatcher=lambda **kw: SimpleNamespace(**kw),
            query=query,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk.types",
        SimpleNamespace(
            PermissionResultDeny=lambda **kw: SimpleNamespace(**kw),
            ResultMessage=Result,
        ),
    )
    monkeypatch.setattr(importlib.metadata, "version", lambda _: "test-version")
    return state


def adapter(bridge, **kwargs):
    return ClaudeAgentGateAdapter("/sandbox", bridge=bridge, sandbox_confirmed=True, **kwargs)


@pytest.mark.parametrize("decision", ["allow", "deny", "ask"])
def test_gate_decisions_are_enforced_and_recorded(sdk, decision):
    bridge = Bridge(decision)
    result = asyncio.run(adapter(bridge).execute(sample(), run_id="run")).response
    assert result.result_type.value == decision
    assert result.raw_response["guard_decision"] == decision
    assert sdk.options.permission_mode == "default"
    assert sdk.options.setting_sources == []
    assert sdk.pre["hookSpecificOutput"]["permissionDecision"] == (
        "allow" if decision == "allow" else "deny"
    )
    assert sdk.second["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert bridge.calls[0]["tool_input"]["command"] == sample().assistant_tool_call.raw
    assert bridge.calls[0]["cwd"] == "/sandbox"
    assert bridge.calls[0]["session_id"] == "sdk-session"
    assert bridge.calls[0]["call_id"] == "first"
    assert result.usage.input_tokens == 10
    assert result.cost == 0.01
    assert result.raw_response["total_cost_usd"] == 0.2
    if decision == "allow":
        assert sdk.post["hookSpecificOutput"]["updatedToolOutput"] == "clean"
        assert bridge.calls[1]["call_id"] == bridge.calls[0]["call_id"]
    else:
        assert len(bridge.calls) == 1


def test_harness_prompt_after_gate_allow_is_ask_not_allow(sdk):
    sdk.callback = True
    result = asyncio.run(adapter(Bridge()).execute(sample(), run_id="run")).response
    assert result.result_type.value == "ask"
    assert result.raw_response["guard_decision"] == "allow"
    assert result.stage is None


def test_later_denial_of_identical_input_does_not_replace_measured_allow(sdk):
    sdk.later_denial = True
    result = asyncio.run(adapter(Bridge()).execute(sample(), run_id="run")).response
    assert result.result_type.value == "allow"


def test_service_outage_is_not_scored_as_successful_defence(sdk):
    result = asyncio.run(adapter(Bridge(available=False)).execute(sample(), run_id="run")).response
    assert result.result_type.value == "error"
    assert sdk.pre["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_bridge_exception_blocks_and_records_no_decision(sdk):
    class Broken(Bridge):
        async def request(self, **payload):
            raise OSError("private detail")

    result = asyncio.run(adapter(Broken()).execute(sample(), run_id="run")).response
    assert result.result_type.value == "error"
    assert "private detail" not in str(result.raw_response)
    assert sdk.pre["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_no_proposal_never_calls_gate(sdk):
    sdk.propose = False
    bridge = Bridge()
    result = asyncio.run(adapter(bridge).execute(sample(), run_id="run")).response
    assert result.result_type.value == "error"
    assert not bridge.calls


def test_history_is_not_faked_for_claude(sdk):
    bridge = Bridge()
    case = BenchmarkCase.model_validate(copy.deepcopy(HISTORY_CASE))
    result = asyncio.run(adapter(bridge).execute(case, run_id="run")).response
    assert result.result_type.value == "error"
    assert not bridge.calls
    result = asyncio.run(adapter(bridge, send_history=False).execute(case, run_id="run"))
    assert result.history_turns_sent == 0
    assert bridge.calls


@pytest.mark.parametrize("mode,name", [("auto", "claude-code"), ("default", "claude-sdk")])
def test_baseline_modes_keep_native_permission_flow(sdk, mode, name):
    sdk.callback = True
    baseline = ClaudeCodeAutomodeAdapter("/sandbox", sandbox_confirmed=True, permission_mode=mode)
    result = asyncio.run(baseline.execute(sample(), run_id="run")).response
    assert "permissionDecision" not in sdk.pre["hookSpecificOutput"]
    assert sdk.options.permission_mode == mode
    assert result.result_type.value == "ask"
    assert result.raw_response["adapter"] == name


def test_session_timeout_becomes_no_decision(sdk):
    sdk.delay = True
    obs = asyncio.run(
        run_claude_session(
            sample(),
            "run",
            workspace="/sandbox",
            plan=plan_for(sample().assistant_tool_call),
            session_timeout_s=0.01,
        )
    )
    assert "TimeoutError" in obs.error


def test_failed_tool_execution_is_still_allow_and_documents_inspection_limit(sdk):
    sdk.fail = True
    result = asyncio.run(adapter(Bridge()).execute(sample(), run_id="run")).response
    assert result.result_type.value == "allow"
    assert "PostToolUseFailure" in result.raw_response["inspection_skipped"]


@pytest.mark.parametrize("name", ["claude-sdk", "claude-agentgate"])
def test_new_cli_adapters_have_dry_run_and_sandbox_guard(name, capsys):
    args = [
        "benchmark",
        "--adapter",
        name,
        "--case-id",
        "BENIGN_004",
        "--dry-run",
        "--url",
        "http://127.0.0.1:8400",
    ]
    assert main(args) == 2
    assert main([*args, "--sandbox", "/sandbox", "--i-have-a-sandbox"]) == 0


def test_gate_cli_rejects_remote_without_opt_in():
    assert (
        main(
            [
                "benchmark",
                "--adapter",
                "claude-agentgate",
                "--dry-run",
                "--sandbox",
                "/sandbox",
                "--i-have-a-sandbox",
                "--url",
                "https://guard.example",
            ]
        )
        == 2
    )


def test_real_core_bridge_offline():
    try:
        GateBridge(ServiceConfig()).preflight()
    except ValueError as exc:
        pytest.skip(str(exc))
    command = bridge_command(offline=True, script="check_claude_gate.ts")
    output = subprocess.check_output(command, text=True, encoding="utf-8", timeout=60)
    assert "Claude AgentGate core checks passed" in output


def test_docker_bridge_mounts_only_code_and_has_no_credentials(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "docker" if name == "docker" else None)
    command = bridge_command()
    assert "--read-only" in command
    assert all(".env" not in arg and "token" not in arg for arg in command)
    assert sum("type=bind" in arg for arg in command) == 2


def test_mask_keeps_the_tool_output_shape_so_the_sdk_does_not_reject_it(sdk):
    """A built-in tool's output schema is validated: a bare string where the tool
    answers ``{"stdout": ...}`` is rejected and the ORIGINAL text reaches the model,
    silently, while the run would still record a mask. The replacement must therefore
    differ from the original in exactly the text field and nowhere else."""
    sdk.tool_response = {"stdout": "original secret", "stderr": "", "interrupted": False}
    result = asyncio.run(adapter(Bridge()).execute(sample(), run_id="run")).response
    assert sdk.post["hookSpecificOutput"]["updatedToolOutput"] == {
        "stdout": "clean",
        "stderr": "",
        "interrupted": False,
    }
    assert result.raw_response["inspection_field"] == "stdout"
    assert result.raw_response["inspection_applied"] == "replaced"


def test_nested_text_is_masked_in_place(sdk):
    sdk.tool_response = {"type": "text", "file": {"filePath": "/x", "content": "original secret"}}
    asyncio.run(adapter(Bridge()).execute(sample(), run_id="run"))
    assert sdk.post["hookSpecificOutput"]["updatedToolOutput"] == {
        "type": "text",
        "file": {"filePath": "/x", "content": "clean"},
    }


def test_a_response_with_no_maskable_text_is_recorded_not_masked(sdk):
    """Better to report that the mask could not be applied than to emit a replacement
    the SDK will drop while the record claims the output was filtered."""
    sdk.tool_response = {"content": [{"type": "text", "text": "original secret"}]}
    bridge = Bridge()
    result = asyncio.run(adapter(bridge).execute(sample(), run_id="run")).response
    assert sdk.post == {}
    assert "no text field" in result.raw_response["inspection_skipped"]
    assert len(bridge.calls) == 1  # nothing inspectable, so nothing was sent


def test_pass_verdict_leaves_the_output_alone(sdk):
    sdk.tool_response = {"stdout": "harmless", "stderr": ""}
    result = asyncio.run(adapter(Bridge(verdict="pass")).execute(sample(), run_id="run")).response
    assert sdk.post == {}
    assert result.raw_response["inspection_applied"] == "pass"


def test_a_failing_bridge_withholds_the_output_in_shape(sdk):
    class Failing(Bridge):
        async def request(self, **payload):
            if payload["operation"] == "inspect":
                raise RuntimeError("bridge down")
            return await super().request(**payload)

    sdk.tool_response = {"stdout": "original secret", "stderr": ""}
    result = asyncio.run(adapter(Failing()).execute(sample(), run_id="run")).response
    replaced = sdk.post["hookSpecificOutput"]["updatedToolOutput"]
    assert "withheld" in replaced["stdout"] and replaced["stderr"] == ""
    assert result.raw_response["inspection_error"] == "RuntimeError"
    assert result.raw_response["inspection_applied"] == "withheld"


def test_a_ruled_claude_run_records_its_digest(monkeypatch, capsys):
    """Without it a later ``compare`` reads the run as unruled and puts its FP, which
    counts only what the case did not accept, beside an unruled run's FP."""
    import cli

    seen = {}

    def capture(*, run_config, **kwargs):
        seen["config"] = run_config
        raise SystemExit(0)

    monkeypatch.setattr(cli, "_run_with_adapter", capture)
    monkeypatch.setattr(cli.GateBridge, "preflight", lambda self: None)
    with pytest.raises(SystemExit):
        main(
            [
                "benchmark",
                "--adapter",
                "claude-agentgate",
                "--case-id",
                "BENIGN_004",
                "--sandbox",
                "/sandbox",
                "--i-have-a-sandbox",
                "--url",
                "http://127.0.0.1:8400",
                "--no-health-check",
                "--no-db",
                "--rules",
                "rules.example.yaml",
            ]
        )
    assert seen["config"].rules_digest == load_rules("rules.example.yaml").digest()
    assert seen["config"].rules is not None
