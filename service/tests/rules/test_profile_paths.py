from agentgate.api.schemas import DecideRequest, DecisionKind
from agentgate.normalize import normalize
from agentgate.rules.profile_paths import ProfilePathRule
from tests.factories import WORKSPACE, shell_action, stage1_profile

PROFILE = stage1_profile()
RULE = ProfilePathRule()


def file_write(*paths: str):
    return normalize(DecideRequest(
        harness="t", tool="file_write", args={"cwd": WORKSPACE, "paths": list(paths)}, user_request="x",
    ))


def test_denies_a_mutating_target_outside_the_allowed_paths():
    assert RULE.evaluate(shell_action("mkdir /opt/x"), PROFILE).decision is DecisionKind.deny


def test_says_nothing_about_a_mutating_target_inside_the_workspace():
    assert RULE.evaluate(shell_action("mkdir src/new"), PROFILE) is None


def test_says_nothing_about_a_read_outside_the_workspace():
    assert RULE.evaluate(shell_action("cat /etc/hosts"), PROFILE) is None


def test_denies_a_file_write_outside_the_allowed_paths():
    assert RULE.evaluate(file_write("/etc/x"), PROFILE).rule_id == "profile.path"


def test_says_nothing_about_a_domain():
    assert RULE.evaluate(shell_action("curl https://evil.sh"), PROFILE) is None
