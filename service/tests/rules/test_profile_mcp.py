import pytest

from agentgate.api.schemas import DecisionKind
from agentgate.rules.profile_mcp import ProfileMcpRule
from tests.factories import mcp_action, mcp_policy, shell_action, stage1_policy

RULE = ProfileMcpRule()
POLICY = mcp_policy(
    allow=["github.get_*", "github.list_*"],
    ask=["github.create_*"],
    deny=["*.delete_*", "shell.*"],
)


@pytest.mark.parametrize(
    ("server", "tool", "expected", "rule_id"),
    [
        ("github", "get_issue", DecisionKind.allow, "profile.mcp-allow"),
        ("github", "list_repos", DecisionKind.allow, "profile.mcp-allow"),
        ("github", "create_pr", DecisionKind.ask, "profile.mcp-ask"),
        ("github", "delete_repo", DecisionKind.deny, "profile.mcp-deny"),
        ("shell", "run", DecisionKind.deny, "profile.mcp-deny"),
        ("notes", "append", None, None),
    ],
    ids=["allow_get", "allow_list", "ask_create", "deny_delete", "deny_whole_server", "no_match"],
)
def test_the_operators_mcp_lists(server, tool, expected, rule_id):
    verdict = RULE.evaluate(mcp_action(server, tool), POLICY)
    if expected is None:
        assert verdict is None
    else:
        assert verdict is not None and verdict.decision is expected and verdict.rule_id == rule_id


def test_deny_wins_over_ask_and_allow_when_several_lists_match():
    policy = mcp_policy(allow=["github.*"], ask=["github.*"], deny=["github.*"])
    assert RULE.evaluate(mcp_action("github", "get_issue"), policy).rule_id == "profile.mcp-deny"


def test_ask_wins_over_allow_when_both_match():
    policy = mcp_policy(allow=["github.*"], ask=["github.get_*"])
    assert RULE.evaluate(mcp_action("github", "get_issue"), policy).rule_id == "profile.mcp-ask"


@pytest.mark.parametrize(
    ("server", "tool"),
    [
        ("GitHub", "Get_Issue"),
        ("githυb", "get_issue"),   # Greek upsilon in place of `u`
        ("github", "get-issue"),
        ("github ", "get_issue"),
        ("github", "delete_repo".upper()),
    ],
    ids=["case", "homoglyph", "dash_instead_of_underscore", "trailing_space", "uppercase_delete"],
)
def test_an_obfuscated_name_is_not_matched_and_falls_through_to_stage_two(server, tool):
    # A near-miss must not silently become allow, and must not silently become
    # deny either: the rule says nothing and stage 2 sees the call.
    assert RULE.evaluate(mcp_action(server, tool), POLICY) is None


def test_an_empty_mcp_section_says_nothing():
    assert RULE.evaluate(mcp_action("github", "delete_repo"), stage1_policy()) is None


def test_a_shell_action_is_none_of_this_rules_business():
    assert RULE.evaluate(shell_action("rm -rf ./dist"), POLICY) is None
