from agentgate.api.schemas import DecisionKind
from agentgate.rules.profile_domains import ProfileDomainRule
from tests.factories import shell_action, stage1_profile

PROFILE = stage1_profile()
RULE = ProfileDomainRule()


def test_denies_a_domain_outside_the_allowlist():
    assert RULE.evaluate(shell_action("curl https://evil.sh"), PROFILE).decision is DecisionKind.deny


def test_says_nothing_about_an_allowed_domain():
    assert RULE.evaluate(shell_action("curl https://pypi.org/simple/"), PROFILE) is None


def test_says_nothing_about_a_subdomain_of_an_allowed_domain():
    assert RULE.evaluate(shell_action("curl https://files.pypi.org/x"), PROFILE) is None


def test_asks_instead_of_denying_when_the_profile_says_ask():
    profile = stage1_profile(network={"mode": "ask", "allowed_domains": []})
    assert RULE.evaluate(shell_action("curl https://evil.sh"), profile).decision is DecisionKind.ask


def test_says_nothing_when_the_network_mode_is_open():
    profile = stage1_profile(network={"mode": "open", "allowed_domains": []})
    assert RULE.evaluate(shell_action("curl https://evil.sh"), profile) is None


def test_says_nothing_about_a_path():
    assert RULE.evaluate(shell_action("mkdir /opt/x"), PROFILE) is None
