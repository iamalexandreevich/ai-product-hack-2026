"""The one place that talks to the Claude Agent SDK.

Kept deliberately thin and separate from ``claude_code.py`` so that the verdict logic
(``interpret``) stays pure and testable, and so that importing the adapter never requires
``claude-agent-sdk`` to be installed. This module is imported lazily, only when a real
Claude Code session actually runs; ``claude-agent-sdk`` lives in the optional ``claude``
dependency group. Everything here is a direct port of the validated spike
(docs/reports name the run): restrict the toolset, hijack the first proposed call, read
the verdict from structured signals.
"""

from __future__ import annotations

import logging

from automode.claude_code import ClaudeRunObservation, ToolPlan
from schemas.case import BenchmarkCase

logger = logging.getLogger(__name__)


def _command(tool_input: object) -> str | None:
    if isinstance(tool_input, dict):
        return tool_input.get("command")
    return None


async def run_claude_session(
    case: BenchmarkCase,
    run_id: str,
    *,
    workspace: str,
    plan: ToolPlan,
    model: str | None = None,
    max_turns: int = 6,
) -> ClaudeRunObservation:
    """Drive one Claude Code session for one case and report what happened."""
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, query
    from claude_agent_sdk.types import PermissionResultDeny, ResultMessage

    tool_call = case.assistant_tool_call
    payload = plan.build_input(tool_call)
    # The value we compare structured signals against, to tell our injected action apart
    # from any exploration call the rails deny afterwards.
    ours = payload.get("command") if plan.claude_tool == "Bash" else None
    human_req = case.human_req.strip()

    obs = ClaudeRunObservation()

    async def pre_tool_use(input_data, tool_use_id, context):
        if obs.substituted:
            # One measured action per case: deny everything after the first.
            return {
                "hookSpecificOutput": {
                    "hookEventName": input_data["hook_event_name"],
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "benchmark: only the first action is measured",
                }
            }
        obs.substituted = True
        obs.proposed_command = _command(input_data.get("tool_input"))
        # Rewrite to the case's action and return NO permissionDecision, so it flows
        # through normal permission evaluation and the classifier judges our action.
        return {
            "hookSpecificOutput": {
                "hookEventName": input_data["hook_event_name"],
                "updatedInput": payload,
            }
        }

    async def post_tool_use(input_data, tool_use_id, context):
        if _command(input_data.get("tool_input")) == ours:
            obs.ran_ours = True
        return {}

    async def can_use_tool(tool_name, input_data, context):
        if _command(input_data) == ours:
            obs.asked_ours = True
        return PermissionResultDeny(message="benchmark: recording the prompt, not answering it")

    async def prompt_stream():
        yield {"type": "user", "message": {"role": "user", "content": human_req}}

    options = ClaudeAgentOptions(
        cwd=workspace,
        tools=[plan.claude_tool],
        permission_mode="auto",
        setting_sources=[],
        max_turns=max_turns,
        model=model,
        can_use_tool=can_use_tool,
        hooks={
            "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_use])],
        },
    )

    try:
        async for message in query(prompt=prompt_stream(), options=options):
            if isinstance(message, ResultMessage):
                obs.total_cost_usd = message.total_cost_usd
                obs.session_id = message.session_id
                obs.duration_ms = message.duration_ms
                if message.model_usage:
                    obs.cost_by_model = {
                        m: round(u.get("costUSD", 0.0), 6) for m, u in message.model_usage.items()
                    }
                for denial in message.permission_denials or []:
                    di = denial.get("tool_input") if isinstance(denial, dict) else None
                    if _command(di) == ours:
                        obs.denied_ours = True
    except Exception as exc:  # a case must never abort a run
        logger.exception("claude code session for case %s raised", case.id)
        obs.error = f"{type(exc).__name__}: {exc}"

    return obs
