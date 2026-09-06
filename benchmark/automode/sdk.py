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

import asyncio
import importlib.metadata
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


# The field a tool result carries its text in. Bash answers with ``stdout``, file tools
# nest a ``content`` string, and some answer with a bare string. A response shaped in
# none of these ways (a list of MCP content parts, say) is reported as uninspectable.
_TEXT_FIELDS = ("stdout", "content", "text", "output")


def _text_path(response, depth: int = 0) -> tuple[str, ...] | None:
    """Where the inspectable text sits, or ``None`` when nothing there is text.

    A path rather than a value, because the mask has to go back into the *same* place
    for the replacement to keep the tool's output schema. Depth is bounded so an
    unfamiliar response shape is reported as unsupported instead of guessed at.
    """
    if depth == 0 and isinstance(response, str):
        return ()
    if not isinstance(response, dict) or depth >= 3:
        return None
    for field in _TEXT_FIELDS:
        if isinstance(response.get(field), str):
            return (field,)
    # Only a field the tool named as text qualifies. Descending into any string would
    # mask a status or a path -- ``{"type": "text", ...}`` is not the tool's output.
    for key, value in response.items():
        nested = _text_path(value, depth + 1)
        if nested is not None:
            return (key, *nested)
    return None


def _at(response, path: tuple[str, ...]):
    for key in path:
        response = response[key]
    return response


def _replacement(response, path: tuple[str, ...], text: str) -> dict:
    """A ``PostToolUse`` output replacement that differs from the original in one field."""
    if not path:
        replaced = text
    else:
        replaced = {**response, path[0]: _at_replaced(response[path[0]], path[1:], text)}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": replaced}}


def _at_replaced(value, path: tuple[str, ...], text: str):
    if not path:
        return text
    return {**value, path[0]: _at_replaced(value[path[0]], path[1:], text)}


async def run_claude_session(
    case: BenchmarkCase,
    run_id: str,
    *,
    workspace: str,
    plan: ToolPlan,
    model: str | None = None,
    max_turns: int = 6,
    permission_mode: str = "auto",
    gate=None,
    session_timeout_s: float = 180.0,
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
    obs.extra["sdk_version"] = importlib.metadata.version("claude-agent-sdk")
    measured_call_id = None
    measured_session_id = None

    def matches(input_data, tool_use_id=None):
        # The id correlates PreToolUse with PostToolUse for the same call. It is only
        # ever a tie-breaker: the second proposal never reaches PostToolUse (pre denies
        # it), so an id missing on either side must not cost us the attribution.
        called = tool_use_id or input_data.get("tool_use_id")
        return (
            input_data.get("tool_name") == plan.claude_tool
            and plan.identity(input_data.get("tool_input")) == ours
            and (measured_call_id is None or called is None or called == measured_call_id)
        )

    async def pre_tool_use(input_data, tool_use_id, context):
        nonlocal measured_call_id, measured_session_id
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
        measured_call_id = tool_use_id or input_data.get("tool_use_id")
        measured_session_id = input_data.get("session_id")
        obs.extra["measured_call_id"] = measured_call_id
        obs.proposed_command = _command(input_data.get("tool_input"))
        if input_data.get("tool_name") != plan.claude_tool:
            obs.unsupported_reason = "the proposed tool does not match the case tool"
            return {
                "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}
            }
        if gate is not None:
            try:
                outcome = await gate.request(
                    operation="decide",
                    tool_name=plan.claude_tool,
                    tool_input=payload,
                    cwd=workspace,
                    session_id=measured_session_id,
                    call_id=measured_call_id,
                    user_request=human_req,
                    agent_model=model,
                    sdk_version=obs.extra["sdk_version"],
                    rules=case.rules.model_dump(mode="json") if case.rules else None,
                )
                obs.extra["agentgate"] = outcome
                if not outcome["result"]["ok"]:
                    obs.unsupported_reason = (
                        "AgentGate unavailable; local fallback is not a measured guard decision"
                    )
                    decision = "deny"
                else:
                    decision = outcome["policy"]["status"]
                    obs.denied_ours = decision == "deny"
                    obs.asked_ours = decision == "ask"
                # In unattended evaluation an ask is recorded then refused, never approved.
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "updatedInput": payload,
                        "permissionDecision": "allow" if decision == "allow" else "deny",
                        "permissionDecisionReason": outcome.get(
                            "deny_message", "AgentGate benchmark"
                        ),
                    }
                }
            except Exception as exc:  # noqa: BLE001 - hook failures must block execution
                obs.unsupported_reason = f"AgentGate bridge failed ({type(exc).__name__})"
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                    }
                }
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
        if matches(input_data, tool_use_id):
            obs.ran_ours = True
            if gate is not None and input_data.get("hook_event_name") == "PostToolUse":
                return await _inspect(input_data.get("tool_response"))
            if gate is not None:
                obs.extra["inspection_skipped"] = (
                    "PostToolUseFailure has no supported output replacement field"
                )
        return {}

    async def _inspect(response) -> dict:
        """Inspect one tool result and hand back a replacement in the tool's own shape.

        The replacement is built by swapping the one text field in place, never by
        substituting a masked JSON dump of the whole response: the SDK validates
        ``updatedToolOutput`` against the tool's output schema and, on a mismatch,
        *silently keeps the original*. A mask that cannot be put back in shape is
        therefore recorded as not applied rather than claimed.
        """
        path = _text_path(response)
        text = _at(response, path) if path is not None else None
        if not text:
            obs.extra["inspection_skipped"] = (
                "the tool response carries no text field a mask could be put back into"
                if path is None
                else "the tool produced no output to inspect"
            )
            return {}
        obs.extra["inspection_field"] = ".".join(path) or "(whole response)"
        try:
            outcome = await gate.request(
                operation="inspect",
                tool_name=plan.claude_tool,
                tool_input=payload,
                cwd=workspace,
                session_id=measured_session_id,
                call_id=measured_call_id,
                user_request=human_req,
                output=text,
                agent_model=model,
                sdk_version=obs.extra["sdk_version"],
            )
        except Exception as exc:  # noqa: BLE001 - withhold output on any bridge failure
            obs.extra["inspection_error"] = type(exc).__name__
            obs.extra["inspection_applied"] = "withheld"
            return _replacement(response, path, "[gate] output withheld: inspection failed")
        obs.extra["agentgate_inspection"] = outcome
        if outcome["policy"]["action"] != "replace":
            obs.extra["inspection_applied"] = "pass"
            return {}
        obs.extra["inspection_applied"] = "replaced"
        return _replacement(response, path, outcome["policy"]["output"])

    async def can_use_tool(tool_name, input_data, context):
        if tool_name == plan.claude_tool and plan.identity(input_data) == ours:
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
        permission_mode=permission_mode,
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
        async with asyncio.timeout(session_timeout_s):
            async for message in query(prompt=prompt_stream(), options=options):
                if isinstance(message, ResultMessage):
                    obs.total_cost_usd = message.total_cost_usd
                    obs.session_id = message.session_id
                    obs.duration_ms = message.duration_ms
                    obs.extra["session_model_usage"] = message.model_usage
                    obs.extra["session_usage"] = message.usage
                    if message.model_usage:
                        obs.cost_by_model = {
                            m: round(u.get("costUSD", 0.0), 6)
                            for m, u in message.model_usage.items()
                        }
                    for denial in message.permission_denials or []:
                        if isinstance(denial, dict) and matches(denial, denial.get("tool_use_id")):
                            obs.denied_ours = True
    except Exception as exc:  # a case must never abort a run
        logger.exception("claude code session for case %s raised", case.id)
        obs.error = f"{type(exc).__name__}: {exc}"

    return obs
