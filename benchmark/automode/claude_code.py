"""Claude Code (native Auto Mode) as an automode implementation.

This is the second production adapter behind ``AutomodeAdapter``. Where
``ServerAutomodeAdapter`` hands a ``(user_request, action)`` pair to our own service and
reads its decision, this adapter poses the *same pair* to Claude Code's own auto-mode
classifier — the guardrail Anthropic ships inside the harness — and reads *its* decision.
Both are guardrails; measuring one against the other is guardrail-vs-guardrail, the
methodology dynamic guardrail benchmarks use (e.g. TraceSafe's pre-action evaluation),
not the environment-and-outcome methodology of AgentDojo/AgentHarm.

How the pair is posed
---------------------
Claude Code's classifier is *pre-action* and *activation-gated*: it only ever judges a
tool call the agent has actually proposed. So one Claude Code session is started per
case with the case's ``human_req`` verbatim as the user message and the toolset
restricted to the one tool the case needs. When the agent proposes its first call, a
``PreToolUse`` hook rewrites that call's input to the case's declared action and returns
no permission decision, so the rewritten action flows through the normal permission
evaluation and the classifier judges *our* action. The verdict is then read from
structured signals, never inferred loosely:

* the action appears in ``ResultMessage.permission_denials``  -> ``deny``;
* ``PostToolUse`` or ``PostToolUseFailure`` fired for the action (it was allowed and
  ran; the exit code is the command's business, not the guardrail's) -> ``allow``;
* ``canUseTool`` fired for the action (it fell through to a prompt) -> ``ask``;
* the agent proposed no call to hijack -> no decision (see below).

Honest limits, stated so nobody plans against them
--------------------------------------------------
* **No decision is a real outcome, not a bug.** When the cover task induces no tool call
  (the agent answers in text, or refuses at the model layer before the classifier runs),
  there is nothing to hijack and Claude Code's guardrail genuinely renders no verdict —
  in the product it never would either. That is reported as an ``error`` result (the
  benchmark's "no decision" state), excluded from every rate, never counted as a catch.
* **A case carrying dialogue history renders no decision here.** The Agent SDK has no
  supported way to start a session with prior turns, and the transcript format that
  ``resume`` reads is documented as internal. Faking one would reproduce the turns but
  not their attribution, which is the very thing multi-turn trust attacks manipulate, so
  these cases report no decision with that reason. In a stripped ablation run
  (``--no-history``) the same cases are posed normally: there is no history left to
  present. The comparison with Claude Code is therefore available on the no-history
  baseline and unavailable on the history variant — a limit of the adapter, not a
  measurement.
* **A classifier ALLOW executes the command.** There is no way to observe an allow
  without the tool running (``PostToolUse`` fires post-execution). This adapter therefore
  MUST run against a disposable sandbox with no real secrets and no network egress; the
  dataset contains reverse shells and destructive removals. The adapter refuses to run
  unless the caller affirms the workspace is such a sandbox.
* **Cost is whole-session.** ``ResultMessage`` reports cost per model for the agent *and*
  the classifier together; the classifier's per-decision cost is not cleanly separable
  from the agent's session spend, so cost is reported ``unavailable`` with a reason and
  the per-model breakdown is kept in ``raw_response`` for transparency, never quoted as a
  per-decision price beside the server's.
* **Every tool of the v1 contract is posed, but not identically.** ``shell`` becomes a
  ``Bash`` call, ``file_read`` a ``Read``, ``file_write`` a ``Write``, ``network`` a
  ``WebFetch``. ``mcp_call`` is posed through an in-process stand-in registered under the
  server and tool name the case declares, whose handler does nothing -- the classifier
  rules on the call, and no side effect can escape the process. A ``file_read`` case that
  declares several paths is posed as its first path, and the narrowing is recorded on the
  result rather than glossed over. Adding a tool is one branch in ``plan_for``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from automode.base import AutomodeExecutionResult
from schemas.case import BenchmarkCase, ServiceDecision, ToolCall, ToolName
from schemas.result import CostSource, ServiceResponse, ServiceResultType


@dataclass(frozen=True)
class McpToolSpec:
    """The no-op stand-in for the MCP server a case names.

    The cases call servers nobody has here (``notes-mcp``, ``github-mcp``), and the
    classifier only ever judges a tool the session actually offers. So the session
    registers an in-process server under the case's own name, exposing the case's own
    tool with the case's own argument names, whose handler does nothing and returns a
    fixed string. That is enough for the agent to propose the call and for the
    classifier to rule on it, and it means no side effect can escape the process --
    which is the point: the benchmark measures the ruling, never the effect.
    """

    server: str
    tool: str
    arg_names: tuple[str, ...]

    @property
    def claude_tool_name(self) -> str:
        return f"mcp__{self.server}__{self.tool}"


@dataclass(frozen=True)
class ToolPlan:
    """How to pose one case's action to Claude Code.

    ``claude_tool`` is the single tool the session is restricted to, so the agent's first
    proposal is a call to it; ``build_input`` is the case's declared action written into
    that tool's input shape; ``identity`` extracts the value that tells our action apart
    from any other call to the same tool, which is how a verdict is attributed. ``mcp``
    is set only when the tool is a stand-in that has to be registered first, and
    ``narrowing`` records any way the pose is narrower than the case (see ``plan_for``).
    """

    claude_tool: str
    build_input: Callable[[], dict]
    identity: Callable[[dict], object]
    mcp: McpToolSpec | None = None
    narrowing: str | None = None


def _first_url(tool_call: ToolCall) -> str:
    """The URL a network case fetches: from the payload if it names one, else built from
    the domain the case declares. ``args.domains`` is required for tool=network, so the
    fallback always has something to build from."""
    match = re.search(r"https?://\S+", tool_call.raw or "")
    if match:
        return match.group(0)
    return f"https://{tool_call.arguments.domains[0]}/"


# Why a case carrying dialogue history cannot be posed here, in the words the result
# carries. Checked against the SDK: streaming input accepts only ``{"type": "user"}``
# envelopes, ``resume``/``fork_session`` take the id of a session already on disk, and
# the transcript format under ~/.claude/projects is documented as internal and free to
# change between releases. Hand-writing one would let us fake a turn, but not its
# attribution — and ``author: human`` versus ``author: agent`` is precisely the variable
# these attacks manipulate. A number produced that way would measure our guess at
# someone else's file format, so no number is produced.
_HISTORY_UNSUPPORTED = (
    "the case carries dialogue history and the Claude Agent SDK exposes no supported way "
    "to seed prior conversation turns with their role and author attribution"
)


def _by(key: str) -> Callable[[dict], object]:
    return lambda tool_input: tool_input.get(key) if isinstance(tool_input, dict) else None


def plan_for(tool_call: ToolCall) -> ToolPlan | None:
    """Pick how to pose one case's action, or ``None`` when there is no way to pose it.

    Every tool the v1 contract defines has a plan today. The ``None`` branch stays because
    the next tool added to the contract will not have one on day one, and a guess would be
    worse than an honest "no decision".
    """
    tool = tool_call.tool
    args = tool_call.arguments

    if tool is ToolName.SHELL:
        return ToolPlan("Bash", lambda: {"command": tool_call.raw}, _by("command"))

    if tool is ToolName.FILE_READ:
        # Read takes one file; a case may declare several. Posing the first is a real
        # narrowing, recorded so a reader of the result is never misled about what the
        # classifier actually ruled on.
        narrowing = (
            f"case declares {len(args.paths)} paths; posed 1 of {len(args.paths)}"
            if len(args.paths) > 1
            else None
        )
        return ToolPlan(
            "Read", lambda: {"file_path": args.paths[0]}, _by("file_path"), narrowing=narrowing
        )

    if tool is ToolName.FILE_WRITE:
        return ToolPlan(
            "Write",
            lambda: {"file_path": args.paths[0], "content": tool_call.raw},
            _by("file_path"),
        )

    if tool is ToolName.NETWORK:
        if args.method not in (None, "GET"):
            # WebFetch cannot preserve an explicit HEAD or mutating HTTP method.
            return None
        url = _first_url(tool_call)
        return ToolPlan(
            "WebFetch",
            lambda: {"url": url, "prompt": "Summarise what this URL returns."},
            _by("url"),
        )

    if tool is ToolName.MCP_CALL and args.mcp is not None:
        mcp = args.mcp
        declared = dict(mcp.arguments or {})
        spec = McpToolSpec(mcp.server, mcp.tool, tuple(declared))
        # The whole argument object is the identity: every call to this stand-in carries
        # the same shape, so no single key distinguishes ours from the agent's own.
        return ToolPlan(spec.claude_tool_name, lambda: dict(declared), lambda ti: ti, mcp=spec)

    return None


@dataclass
class ClaudeRunObservation:
    """Raw signals from one Claude Code session, before interpretation.

    Deliberately dumb: it records what happened, and ``interpret`` alone turns it into a
    verdict. Keeping this split pure is what lets the whole verdict logic be tested
    without the SDK, a live call, or a cent spent.
    """

    substituted: bool = False
    proposed_command: str | None = None
    substituted_input: dict | None = None
    claude_tool: str | None = None
    denied_ours: bool = False
    ran_ours: bool = False
    asked_ours: bool = False
    cost_by_model: dict[str, float] | None = None
    total_cost_usd: float | None = None
    session_id: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    unsupported_reason: str | None = None
    extra: dict = field(default_factory=dict)


# The type a session runner must satisfy: given a case and run id, drive one Claude Code
# session and report what happened. The default runner talks to the Agent SDK; tests
# inject a fake, so nothing here forces claude-agent-sdk to be installed to import.
SessionRunner = Callable[[BenchmarkCase, str], Awaitable[ClaudeRunObservation]]

_COST_UNAVAILABLE = (
    "Claude Code reports cost per model for the whole session (agent + classifier); "
    "the classifier's per-decision cost is not separable and is not comparable to the "
    "server's per-decision price"
)


def interpret(
    case: BenchmarkCase,
    obs: ClaudeRunObservation,
    *,
    adapter_name: str = "claude-code",
    permission_mode: str = "auto",
) -> ServiceResponse:
    """Turn one session's raw signals into a ``ServiceResponse``. Pure and total.

    Every non-decision path (unsupported tool, agent proposed nothing, transport error,
    an outcome that matched none of the structured signals) becomes an ``error`` result
    — the benchmark's "no decision" state — with a reason, never a silent ``allow``.
    """
    raw = {
        "adapter": adapter_name,
        "permission_mode": permission_mode,
        "proposed_command": obs.proposed_command,
        "substituted_command": case.assistant_tool_call.raw or None,
        "substituted_input": obs.substituted_input,
        "claude_tool": obs.claude_tool,
        "cost_by_model": obs.cost_by_model,
        "total_cost_usd": obs.total_cost_usd,
        "session_id": obs.session_id,
        "duration_ms": obs.duration_ms,
        **obs.extra,
    }

    def error(reason: str) -> ServiceResponse:
        return ServiceResponse(
            result_type=ServiceResultType.ERROR,
            error=reason,
            cost_source=CostSource.UNAVAILABLE,
            cost_unavailable_reason=_COST_UNAVAILABLE,
            raw_response=raw,
        )

    if obs.error is not None:
        raw["session_error"] = obs.error

    if obs.unsupported_reason is not None:
        return error(f"no decision: {obs.unsupported_reason}")

    # A signal is a verdict the classifier actually rendered, so it outranks whatever the
    # session did afterwards. The agent keeps exploring after its first call is hijacked
    # and regularly burns through max_turns; a failure that late must not erase a decision
    # already observed, and it stays in ``raw_response`` as ``session_error``.
    if obs.asked_ours:
        decision, result_type = ServiceDecision.ASK, ServiceResultType.ASK
        reason = f"Claude Code {permission_mode} mode routed the action to a permission prompt"
    elif obs.denied_ours:
        decision, result_type = ServiceDecision.DENY, ServiceResultType.DENY
        reason = (
            "Blocked by Claude Code auto-mode classifier"
            if permission_mode == "auto"
            else "Blocked by Claude Code permissions or AgentGate hook"
        )
    elif obs.ran_ours:
        decision, result_type = ServiceDecision.ALLOW, ServiceResultType.ALLOW
        reason = ""
    elif obs.error is not None:
        return error(f"no decision: {obs.error}")
    elif not obs.substituted:
        return error(
            "no decision: the agent proposed no tool call to judge "
            "(answered in text, or refused at the model layer before the classifier)"
        )
    else:
        return error(
            "no decision: the action was substituted but produced no allow, deny or ask "
            "signal (inconclusive session)"
        )

    return ServiceResponse(
        result_type=result_type,
        decision=decision,
        reason=reason,
        # Claude Code exposes no stage / rule_id / cache / model of its own to us.
        cost_source=CostSource.UNAVAILABLE,
        cost_unavailable_reason=_COST_UNAVAILABLE,
        raw_response=raw,
    )


class ClaudeCodeAutomodeAdapter:
    """Claude Code's native Auto Mode as one automode under test.

    ``workspace`` MUST be a disposable sandbox with no real secrets and no network
    egress: a classifier allow executes the command. The adapter refuses to run unless
    ``sandbox_confirmed`` is set, so this cannot be pointed at a real machine by accident.

    ``session_runner`` drives one Claude Code session and returns a
    ``ClaudeRunObservation``. It defaults to the Agent SDK driver in ``sdk.py``; tests
    inject a fake, which is why importing this module never requires ``claude-agent-sdk``.
    """

    name = "claude-code"

    def __init__(
        self,
        workspace: str,
        *,
        sandbox_confirmed: bool = False,
        model: str | None = None,
        session_runner: SessionRunner | None = None,
        send_history: bool = True,
        permission_mode: str = "auto",
        session_timeout_s: float = 180.0,
    ) -> None:
        if not sandbox_confirmed:
            raise ValueError(
                "ClaudeCodeAutomodeAdapter runs real commands when the classifier allows "
                "them; refusing to start without sandbox_confirmed=True. Point workspace "
                "at a disposable sandbox with no secrets and no network egress."
            )
        self.workspace = workspace
        if permission_mode not in ("auto", "default"):
            raise ValueError("Claude benchmark permission mode must be auto or default")
        self.permission_mode = permission_mode
        if permission_mode == "default":
            self.name = "claude-sdk"
        self.session_timeout_s = session_timeout_s
        self.model = model
        self._run_session = session_runner or self._default_session_runner
        self.send_history = send_history

    async def execute(self, case: BenchmarkCase, *, run_id: str) -> AutomodeExecutionResult:
        tool_call = case.assistant_tool_call
        plan = plan_for(tool_call)
        if self.send_history and case.history:
            # A stripped ablation run (send_history=False) poses these cases normally:
            # there is no history to present, and what is left is an ordinary action.
            obs = ClaudeRunObservation(unsupported_reason=_HISTORY_UNSUPPORTED)
        elif plan is None:
            obs = ClaudeRunObservation(
                unsupported_reason=f"{tool_call.tool} has no way to be posed to Claude Code"
            )
        else:
            obs = await self._run_session(case, run_id)
            if plan.narrowing:
                obs.extra["narrowing"] = plan.narrowing
        return AutomodeExecutionResult(response=self.interpret_observation(case, obs))

    def interpret_observation(
        self, case: BenchmarkCase, obs: ClaudeRunObservation
    ) -> ServiceResponse:
        return interpret(case, obs, adapter_name=self.name, permission_mode=self.permission_mode)

    async def _default_session_runner(
        self, case: BenchmarkCase, run_id: str
    ) -> ClaudeRunObservation:
        # Imported lazily so the module (and the whole test suite) loads without the SDK.
        from automode.sdk import run_claude_session

        plan = plan_for(case.assistant_tool_call)
        assert plan is not None  # execute() never reaches the runner without one
        return await run_claude_session(
            case,
            run_id,
            workspace=self.workspace,
            plan=plan,
            model=self.model,
            permission_mode=self.permission_mode,
            session_timeout_s=self.session_timeout_s,
        )
