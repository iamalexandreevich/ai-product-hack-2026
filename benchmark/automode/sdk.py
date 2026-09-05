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
    """The shell command of a proposed call, for the record of what the agent wanted.
    Only Bash sessions have one; it is reported, never used to attribute a verdict."""
    if isinstance(tool_input, dict):
        return tool_input.get("command")
    return None


def _stub_server(spec):
    """Register the MCP server a case names, with a handler that does nothing.

    The classifier only rules on tools the session actually offers, and none of the
    servers the dataset names (``notes-mcp``, ``github-mcp``, ``mailer-mcp``) exist here.
    The stand-in advertises the case's own tool and argument names so the agent proposes
    that exact shape, and returns a fixed string without touching a file, a mailbox or a
    network -- the benchmark measures the ruling on the call, never its effect. An
    allowed MCP call is therefore the one allow in this adapter that costs nothing.
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool

    schema = {name: str for name in spec.arg_names} or {"input": str}

    @tool(spec.tool, f"{spec.tool} ({spec.server})", schema)
    async def _noop(args):
        # The stand-in ignores its arguments by design: the ruling is the measurement.
        return {"content": [{"type": "text", "text": "ok"}]}

    return create_sdk_mcp_server(name=spec.server, tools=[_noop])


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

    payload = plan.build_input()
    # What we compare structured signals against, to tell our injected action apart from
    # any exploration call the rails deny afterwards. The plan owns the comparison because
    # each tool is identified by a different field (a command, a path, a URL, the whole
    # argument object for an MCP stand-in).
    ours = plan.identity(payload)
    human_req = case.human_req.strip()

    obs = ClaudeRunObservation(substituted_input=payload, claude_tool=plan.claude_tool)

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
        # Fires for PostToolUse and PostToolUseFailure alike: both mean the classifier
        # let the action through and it executed. Whether the command then exited
        # non-zero is the command's business, not the guardrail's -- and in a sandbox
        # (no project, no toolchain) most of the dataset exits non-zero, so reading only
        # PostToolUse would silently drop most allows into "no decision".
        if plan.identity(input_data.get("tool_input")) == ours:
            obs.ran_ours = True
        return {}

    async def can_use_tool(tool_name, input_data, context):
        if plan.identity(input_data) == ours:
            obs.asked_ours = True
        return PermissionResultDeny(message="benchmark: recording the prompt, not answering it")

    async def prompt_stream():
        yield {"type": "user", "message": {"role": "user", "content": human_req}}

    # An MCP case offers no built-in tool at all, only the stand-in; every other case is
    # restricted to the single built-in the plan names. Either way the agent has exactly
    # one tool to propose, so its first proposal is the one we hijack.
    mcp_servers = {}
    tools: list[str] = [plan.claude_tool]
    if plan.mcp is not None:
        mcp_servers = {plan.mcp.server: _stub_server(plan.mcp)}
        tools = []

    options = ClaudeAgentOptions(
        cwd=workspace,
        tools=tools,
        mcp_servers=mcp_servers,
        permission_mode="auto",
        setting_sources=[],
        max_turns=max_turns,
        model=model,
        can_use_tool=can_use_tool,
        hooks={
            "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_use])],
            "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[post_tool_use])],
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
                    if plan.identity(di) == ours:
                        obs.denied_ours = True
    except Exception as exc:  # a case must never abort a run
        logger.exception("claude code session for case %s raised", case.id)
        obs.error = f"{type(exc).__name__}: {exc}"

    return obs
