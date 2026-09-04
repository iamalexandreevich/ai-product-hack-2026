from agentgate.api.schemas import DecideRequest, DecisionKind
from agentgate.normalize import normalize
from agentgate.rules.profile_paths import ProfilePathRule
from tests.factories import WORKSPACE, shell_action, stage1_policy

POLICY = stage1_policy()
RULE = ProfilePathRule()


def file_write(*paths: str):
    return normalize(DecideRequest(
        harness="t", tool="file_write", args={"cwd": WORKSPACE, "paths": list(paths)}, user_request="x",
    ))


def test_denies_a_mutating_target_outside_the_allowed_paths():
    assert RULE.evaluate(shell_action("mkdir /opt/x"), POLICY).decision is DecisionKind.deny


def test_says_nothing_about_a_mutating_target_inside_the_workspace():
    assert RULE.evaluate(shell_action("mkdir src/new"), POLICY) is None


def test_says_nothing_about_a_read_outside_the_workspace():
    assert RULE.evaluate(shell_action("cat /etc/hosts"), POLICY) is None


def test_denies_a_file_write_outside_the_allowed_paths():
    assert RULE.evaluate(file_write("/etc/x"), POLICY).rule_id == "profile.path"


def test_says_nothing_about_a_domain():
    assert RULE.evaluate(shell_action("curl https://evil.sh"), POLICY) is None


def test_denies_an_in_place_edit_outside_the_allowed_paths():
    action = shell_action("sed -i 's/a/b/' /etc/hosts")
    assert RULE.evaluate(action, POLICY).decision is DecisionKind.deny


def test_denies_a_long_form_in_place_edit_outside_the_allowed_paths():
    action = shell_action("sed --in-place 's/a/b/' /etc/hosts")
    assert RULE.evaluate(action, POLICY).decision is DecisionKind.deny


def test_says_nothing_about_a_sed_that_does_not_edit_in_place():
    assert RULE.evaluate(shell_action("sed 's/a/b/' /etc/hosts"), POLICY) is None
