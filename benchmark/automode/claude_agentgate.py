"""Claude with native Auto Mode disabled and AgentGate's production core in SDK hooks."""

from automode.claude_code import ClaudeCodeAutomodeAdapter, interpret, plan_for
from client.security_service import normalize_response
from schemas.result import ServiceResultType


class ClaudeAgentGateAdapter(ClaudeCodeAutomodeAdapter):
    name = "claude-agentgate"

    def __init__(self, workspace, *, bridge, **kwargs):
        super().__init__(workspace, permission_mode="default", **kwargs)
        self.name = "claude-agentgate"
        self.bridge = bridge

    async def _default_session_runner(self, case, run_id):
        from automode.sdk import run_claude_session

        return await run_claude_session(
            case,
            run_id,
            workspace=self.workspace,
            plan=plan_for(case.assistant_tool_call),
            model=self.model,
            permission_mode="default",
            gate=self.bridge,
            session_timeout_s=self.session_timeout_s,
        )

    def interpret_observation(self, case, obs):
        effective = interpret(case, obs, adapter_name=self.name, permission_mode="default")
        outcome = obs.extra.get("agentgate", {})
        result = outcome.get("result", {})
        if effective.result_type is ServiceResultType.ERROR:
            return effective
        if not result.get("ok"):
            obs.unsupported_reason = "no successful AgentGate decision was observed"
            return interpret(case, obs, adapter_name=self.name, permission_mode="default")
        measured = normalize_response(result["value"], http_status=200, config=self.bridge.config)
        if measured.result_type is ServiceResultType.ERROR:
            measured.raw_response = effective.raw_response
            return measured
        # Preserve service usage separately from Claude's whole-session usage. The score
        # follows what the harness did, which can be stricter than AgentGate's allow.
        effective.raw_response["guard_decision"] = measured.result_type.value
        effective.raw_response["effective_decision"] = effective.result_type.value
        if measured.result_type != effective.result_type:
            measured.stage = None
            measured.rule_id = None
        measured.result_type = effective.result_type
        measured.decision = effective.decision
        measured.reason = effective.reason or measured.reason
        measured.raw_response = effective.raw_response
        return measured
