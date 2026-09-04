from agentgate.api.schemas import DecisionKind
from agentgate.profiles.schema import DenyWindow, Escalation
from agentgate.session.escalation import should_escalate
from tests.factories import session_state


def test_record_counters():
    s = session_state()
    s.record(DecisionKind.deny); s.record(DecisionKind.deny)
    assert s.deny_consecutive == 2 and s.deny_total == 2 and s.decisions_total == 2
    s.record(DecisionKind.ask)
    assert s.deny_consecutive == 2
    s.record(DecisionKind.allow)
    assert s.deny_consecutive == 0 and s.deny_total == 2 and s.decisions_total == 4
    assert list(s.recent) == ["deny", "deny", "ask", "allow"]


def test_escalate_on_consecutive():
    s = session_state()
    cfg = Escalation(deny_consecutive=3, deny_window=DenyWindow(count=10, of_last=50))
    for _ in range(2):
        s.record(DecisionKind.deny)
    assert not should_escalate(s, cfg)
    s.record(DecisionKind.deny)
    assert should_escalate(s, cfg)


def test_escalate_on_window():
    s = session_state()
    cfg = Escalation(deny_consecutive=99, deny_window=DenyWindow(count=3, of_last=5))
    for d in ["deny", "allow", "deny"]:
        s.record(DecisionKind(d))
    # two denials among the last five must NOT escalate: one below threshold.
    assert not should_escalate(s, cfg)
    for d in ["allow", "deny"]:
        s.record(DecisionKind(d))
    assert should_escalate(s, cfg)
    for _ in range(5):
        s.record(DecisionKind.allow)
    assert not should_escalate(s, cfg)
