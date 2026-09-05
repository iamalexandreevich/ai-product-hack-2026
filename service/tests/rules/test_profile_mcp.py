import pytest

from agentgate.api.schemas import DecisionKind
from agentgate.rules.profile_mcp import ProfileMcpRule
from tests.factories import mcp_action, mcp_policy, shell_action, stage1_policy

REFUSE = ProfileMcpRule("refuse")
ALLOW = ProfileMcpRule("allow")
POLICY = mcp_policy(
    allow=["github.get_*", "github.list_*"],
    ask=["github.create_*"],
    deny=["*.delete_*", "shell.*"],
)


def _evaluate(server: str, tool: str, policy=POLICY):
    """What the chain settles on: refuse (deny/ask) first, then allow --
    mirroring the two instances' positions in STAGE1.
    """
    action = mcp_action(server, tool)
    return REFUSE.evaluate(action, policy) or ALLOW.evaluate(action, policy)


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
    verdict = _evaluate(server, tool)
    if expected is None:
        assert verdict is None
    else:
        assert verdict is not None and verdict.decision is expected and verdict.rule_id == rule_id


def test_deny_wins_over_ask_and_allow_when_several_lists_match():
    policy = mcp_policy(allow=["github.*"], ask=["github.*"], deny=["github.*"])
    assert _evaluate("github", "get_issue", policy).rule_id == "profile.mcp-deny"


def test_ask_wins_over_allow_when_both_match():
    policy = mcp_policy(allow=["github.*"], ask=["github.get_*"])
    assert _evaluate("github", "get_issue", policy).rule_id == "profile.mcp-ask"


def test_the_operators_mcp_ask_is_a_floor():
    # Spec principle (v3.1): an ask never lowers the ceiling. The operator's
    # own mcp.ask must not settle the chain before stage 2 -- it has to be
    # carried as a floor, exactly like the user's client.ask.
    verdict = REFUSE.evaluate(mcp_action("github", "create_pr"), POLICY)
    assert verdict.rule_id == "profile.mcp-ask" and verdict.floor is True


def test_the_operators_mcp_deny_is_not_a_floor():
    verdict = REFUSE.evaluate(mcp_action("github", "delete_repo"), POLICY)
    assert verdict.rule_id == "profile.mcp-deny" and verdict.floor is False


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
    assert _evaluate(server, tool) is None


def test_an_empty_mcp_section_says_nothing():
    assert _evaluate("github", "delete_repo", stage1_policy()) is None


def test_a_shell_action_is_none_of_this_rules_business():
    action = shell_action("rm -rf ./dist")
    assert REFUSE.evaluate(action, POLICY) is None
    assert ALLOW.evaluate(action, POLICY) is None


def test_an_operators_allow_settles_as_ask_when_the_user_asked_to_confirm():
    # The blocking fix: `refuse` says nothing here (no deny/ask match), so
    # `allow` fires -- but in STAGE1 that allow sits below the user's
    # `client.ask` floor and is turned into `ask` by the chain/gate, not
    # by this rule. This test documents the rule's own half of the story.
    assert REFUSE.evaluate(mcp_action("github", "get_issue"), POLICY) is None
    assert ALLOW.evaluate(mcp_action("github", "get_issue"), POLICY).rule_id == "profile.mcp-allow"
