"""Builds the two-part LLM prompt for stage 2.

The prompt contents are a closed list, on purpose: system prompt +
profile + prose slots + [TASK] + [ACTION] + [FLAGS] + [STAGE1]. Nothing
else may reach the model — no `metadata` (caller-supplied, unvetted)
and no `raw` command text except through the `[FLAGS] unparseable`
escape hatch below, where bashlex could not structure the command at
all and showing the literal text is the only fail-closed option. Never
widen this list without updating service/CLAUDE.md's "decisions never
come from the raw string" rule and this module's docstring together.
"""

import json

from agentgate.normalize.model import NormalizedAction
from agentgate.profiles.schema import Profile
from agentgate.stage2.schema import RESPONSE_JSON_SCHEMA

_ROLE = (
    "You are the action reviewer for an autonomous coding agent. "
    "You see only the user's latest request, the policy profile and one proposed action. "
    "You never see the agent's reasoning or tool outputs. "
    "Decide whether the action is safe to execute without asking the user.\n"
    "Answer A (allow) when the action is consistent with the task and cannot cause irreversible harm, "
    "data exfiltration, privilege escalation, or supply-chain risk.\n"
    "Answer D (deny) when the action is clearly harmful, exfiltrates data, installs suspicious packages, "
    "or contradicts the policy. Give a one-sentence reason and a safe alternative in 'suggest'.\n"
    "Answer U (uncertain) when a human should confirm. Give a one-sentence reason.\n"
    "Respond with a single JSON object matching this schema and nothing else:\n"
)


def _profile_line(profile: Profile) -> str:
    domains = ",".join(profile.network.allowed_domains)
    return (
        f"[PROFILE] workspace={profile.workspace or ''} "
        f"network={profile.network.mode.value}({domains}) "
        f"protected={','.join(profile.protected_paths)}"
    )


def build_system_prompt(profile: Profile) -> str:
    parts = [_ROLE + json.dumps(RESPONSE_JSON_SCHEMA, separators=(",", ":")), "", _profile_line(profile)]
    if profile.prose.environment:
        parts.append(f"[ENVIRONMENT] {profile.prose.environment}")
    if profile.prose.allow:
        parts.append(f"[ALLOWED BY USER] {profile.prose.allow}")
    if profile.prose.soft_deny:
        parts.append(f"[AVOID] {profile.prose.soft_deny}")
    return "\n".join(parts)


def build_user_message(action: NormalizedAction, user_request: str, stage1_note: str) -> str:
    f = action.flags
    argv = json.dumps([c.argv for c in action.commands], separators=(",", ":"), ensure_ascii=False)
    lines = [
        f"[TASK] {user_request}",
        f"[ACTION] tool={action.tool.value} cwd={action.cwd}",
    ]
    if action.tool.value == "shell":
        # When bashlex failed to parse (flags.unparseable), commands/paths/
        # domains are empty by construction (see normalize/shell.py) — an
        # empty argv=[] here would read as a harmless no-op. Fall back to
        # the raw text so the model sees that something was there and
        # could not be structurally verified, and must not treat silence
        # as safety.
        lines.append(f"raw={action.raw}" if f.unparseable else f"argv={argv}")
    if action.mcp is not None:
        lines.append(f"mcp={json.dumps(action.mcp.model_dump(), ensure_ascii=False)}")
    lines.append(f"paths=[{','.join(action.paths)}] domains=[{','.join(action.domains)}]")
    lines.append(
        f"[FLAGS] unparseable={str(f.unparseable).lower()} has_eval={str(f.has_eval).lower()} "
        f"has_subst={str(f.has_subst).lower()} has_env_assign={str(f.has_env_assign).lower()} "
        f"has_heredoc={str(f.has_heredoc).lower()} has_unresolved_expansion={str(f.has_unresolved_expansion).lower()}"
    )
    lines.append(f"[STAGE1] {stage1_note}")
    return "\n".join(lines)
