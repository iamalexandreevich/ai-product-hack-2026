import pytest

from agentgate.rules.mcp_readonly import READONLY_PREFIXES, McpReadonlyRule
from tests.factories import mcp_action, mcp_policy, shell_action, stage1_policy

RULE = McpReadonlyRule()
ON = mcp_policy(readonly_prefixes_allow=True)


@pytest.mark.parametrize("tool", sorted(f"{p}thing" for p in READONLY_PREFIXES))
def test_every_declared_prefix_is_allowed_when_the_flag_is_on(tool):
    verdict = RULE.evaluate(mcp_action("github", tool), ON)
    assert verdict is not None and verdict.rule_id == "allowlist.mcp-readonly"


@pytest.mark.parametrize(
    "tool",
    ["getIssue", "delete_get_thing", "Get_issue", "fetch_issue", "get", "readme"],
    ids=["camel_case", "prefix_not_at_the_start", "wrong_case", "undeclared_prefix", "bare_prefix_without_underscore", "prefix_as_a_substring"],
)
def test_a_name_that_is_not_a_declared_prefix_is_not_allowed(tool):
    assert RULE.evaluate(mcp_action("github", tool), ON) is None


@pytest.mark.parametrize("tool", ["get_issue", "list_repos", "read_file"])
def test_the_rule_is_silent_while_the_flag_is_off(tool):
    assert RULE.evaluate(mcp_action("github", tool), stage1_policy()) is None


def test_a_shell_action_is_none_of_this_rules_business():
    assert RULE.evaluate(shell_action("ls -la"), ON) is None
