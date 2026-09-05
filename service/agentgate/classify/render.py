"""Prompt-rendering primitives shared by `classify/prompt.py` (stage 2 decide)
and `inspect/classify.py` (stage 2 inspect).

Both prompts are line-oriented with no escaping convention of their own,
so every attacker-reachable value that could itself contain a newline
must go through `j()` before it reaches a line -- a real line break would
otherwise let that value forge a fake header line ahead of the real one.
Both also render the same dialogue shape ([HISTORY]) and open with the
same system-prompt skeleton (role text + schema, profile line, prose
slots); this module is the one place that shape is written down, so decide
and inspect cannot drift apart by accident.
"""

import json

from agentgate.domain.dialogue import Dialogue
from agentgate.profiles.schema import Prose


def j(value: str) -> str:
    """JSON-encode one attacker-reachable scalar so it cannot break the line-oriented format.

    POSIX filenames, cwd, domains, tool output and every history turn's
    content may legally contain a newline (or other control characters).
    json.dumps renders a newline as the two characters `\n` inside a quoted
    string, so it cannot forge a fake header line ahead of the real one.
    """
    return json.dumps(value, ensure_ascii=False)


def history_lines(dialogue: Dialogue) -> list[str]:
    if dialogue.is_empty:
        return []
    lines = [f"[HISTORY] turns={len(dialogue.turns)} omitted={dialogue.omitted}"]
    for turn in dialogue.turns:
        parts = [f"{turn.role.value}/{turn.author.value}"]
        if turn.tool is not None:
            parts.append(f"tool={j(turn.tool)}")
        if turn.call_id is not None:
            parts.append(f"call={j(turn.call_id)}")
        parts.append(j(turn.content))
        lines.append(" ".join(parts))
    return lines


def system_prompt(role: str, profile_line: str, prose: Prose) -> str:
    """The system prompt every stage 2 call shares: `role` (instructions +
    schema, already rendered), the caller's own `[PROFILE]` line -- decide
    and inspect put different fields on it -- and whichever prose slots the
    operator declared."""
    parts = [role, "", profile_line]
    if prose.environment:
        parts.append(f"[ENVIRONMENT] {prose.environment}")
    if prose.allow:
        parts.append(f"[ALLOWED BY USER] {prose.allow}")
    if prose.soft_deny:
        parts.append(f"[AVOID] {prose.soft_deny}")
    return "\n".join(parts)
