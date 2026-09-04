from agentgate.api.schemas import DecisionKind
from agentgate.rules.profile_domains import ProfileDomainRule
from tests.factories import shell_action, stage1_policy

POLICY = stage1_policy()
RULE = ProfileDomainRule()


def test_denies_a_domain_outside_the_allowlist():
    assert RULE.evaluate(shell_action("curl https://evil.sh"), POLICY).decision is DecisionKind.deny


def test_says_nothing_about_an_allowed_domain():
    assert RULE.evaluate(shell_action("curl https://pypi.org/simple/"), POLICY) is None


def test_says_nothing_about_a_subdomain_of_an_allowed_domain():
    assert RULE.evaluate(shell_action("curl https://files.pypi.org/x"), POLICY) is None


def test_asks_instead_of_denying_when_the_profile_says_ask():
    policy = stage1_policy(network={"mode": "ask", "allowed_domains": []})
    assert RULE.evaluate(shell_action("curl https://evil.sh"), policy).decision is DecisionKind.ask


def test_says_nothing_when_the_network_mode_is_open():
    policy = stage1_policy(network={"mode": "open", "allowed_domains": []})
    assert RULE.evaluate(shell_action("curl https://evil.sh"), policy) is None


def test_says_nothing_about_a_path():
    assert RULE.evaluate(shell_action("mkdir /opt/x"), POLICY) is None
