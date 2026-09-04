"""The workspace a session's policy uses is fixed by the session's first
request, not re-derived from each request's cwd.

Without this, an agent that runs `cd /` and reports the new cwd widens
its own sandbox to the filesystem root: allowed_paths becomes ["/"], and
"outside the workspace" stops existing as a concept.
"""

from agentgate.api.schemas import DecisionKind
from tests.factories import decide_request, gate_for_binding_tests

FIRST_CWD = "/home/u/repo"


async def test_first_request_of_a_session_fixes_the_workspace():
    gate = gate_for_binding_tests()
    await gate.decide(decide_request("ls -la", session_id="s1", args={"cwd": FIRST_CWD}))
    decision = await gate.decide(
        decide_request("rm -rf /home/u/other-project", session_id="s1", args={"cwd": "/"})
    )
    assert decision.verdict.decision is DecisionKind.deny
    assert decision.verdict.rule_id == "hard-deny.destructive"


async def test_a_later_cwd_change_does_not_widen_allowed_paths():
    gate = gate_for_binding_tests()
    await gate.decide(decide_request("ls -la", session_id="s1", args={"cwd": FIRST_CWD}))
    decision = await gate.decide(
        decide_request("cp payload /etc/cron.d/job", session_id="s1", args={"cwd": "/"})
    )
    assert decision.verdict.decision is DecisionKind.deny


async def test_a_sessionless_call_still_uses_its_own_cwd():
    decision = await gate_for_binding_tests().decide(
        decide_request("rm -rf /home/u/other-project", session_id=None, args={"cwd": FIRST_CWD})
    )
    assert decision.verdict.decision is DecisionKind.deny


async def test_a_new_session_picks_up_its_own_first_cwd():
    gate = gate_for_binding_tests()
    await gate.decide(decide_request("ls -la", session_id="s1", args={"cwd": FIRST_CWD}))
    decision = await gate.decide(
        decide_request("ls -la", session_id="s2", args={"cwd": "/tmp/other"})
    )
    assert decision.state.workspace == "/tmp/other"
