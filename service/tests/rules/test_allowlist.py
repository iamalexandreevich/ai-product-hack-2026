from agentgate.rules.allowlist import AllowlistRule
from tests.factories import shell_action, stage1_policy, unparseable_action

POLICY = stage1_policy()
RULE = AllowlistRule()


def test_allows_a_readonly_command():
    assert RULE.evaluate(shell_action("ls -la"), POLICY).rule_id == "allowlist.readonly"


def test_allows_a_command_matching_a_safe_prefix():
    assert RULE.evaluate(shell_action("pytest tests/ -x"), POLICY).rule_id == "allowlist.prefix"


def test_says_nothing_about_a_command_that_is_neither():
    assert RULE.evaluate(shell_action("npm install lodash"), POLICY) is None


def test_says_nothing_about_a_readonly_command_reading_a_protected_path():
    assert RULE.evaluate(shell_action("cat .env"), POLICY) is None


def test_says_nothing_about_a_readonly_command_reading_outside_the_workspace():
    assert RULE.evaluate(shell_action("cat /etc/hosts"), POLICY) is None


def test_says_nothing_when_the_command_carries_an_eval():
    assert RULE.evaluate(shell_action("eval echo hi"), POLICY) is None


def test_says_nothing_about_an_unparseable_command():
    assert RULE.evaluate(unparseable_action(), POLICY) is None
