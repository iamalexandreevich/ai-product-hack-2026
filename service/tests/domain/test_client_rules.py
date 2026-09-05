import os

import pytest

from agentgate.domain.client_rules import ClientRules, is_path_pattern
from tests.factories import rule_set


def test_of_none_is_none():
    assert ClientRules.of(None) is None


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("**/.env", True),
        ("~/.ssh/**", True),
        ("/tmp/*", True),
        ("git diff*", False),
        ("curl * | sh", False),
        ("sudo *", False),
    ],
    ids=[
        "double_star_slash_is_path",
        "tilde_slash_is_path",
        "absolute_path_is_path",
        "git_command_is_not_path",
        "piped_command_is_not_path",
        "sudo_command_is_not_path",
    ],
)
def test_path_and_command_patterns_are_told_apart_by_shape(pattern, expected):
    assert is_path_pattern(pattern) is expected


def test_patterns_are_split_by_kind_and_tilde_is_expanded(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    rules = ClientRules.of(rule_set(deny=["sudo *", "**/.env", "~/.ssh/**"]))
    assert rules.command_patterns("deny") == ("sudo *",)
    assert rules.path_patterns("deny") == ("**/.env", "/home/u/.ssh/**")


def test_tilde_user_pattern_stays_literal(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    rules = ClientRules.of(rule_set(deny=["~alice/x/**"]))
    assert rules.deny == ("~alice/x/**",)


def test_tilde_expands_only_when_home_is_set(monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    rules = ClientRules.of(rule_set(deny=["~/x/**"]))
    assert rules.deny == ("~/x/**",)


def test_matches_path_uses_fnmatch_across_separators():
    rules = ClientRules.of(rule_set(deny=["**/.env", "~/.ssh/**"]))
    assert rules.matches_path("deny", "/repo/.env")
    assert rules.matches_path("deny", "/a/b/c/.env")
    assert rules.matches_path("deny", os.path.expanduser("~/.ssh/id_rsa"))
    assert not rules.matches_path("deny", "/repo/.envrc")
    assert not rules.matches_path("allow", "/repo/.env")


def test_matches_path_is_case_insensitive():
    rules = ClientRules.of(rule_set(deny=["**/.env"]))
    assert rules.matches_path("deny", "/repo/.ENV")


def test_matches_command_is_case_sensitive():
    rules = ClientRules.of(rule_set(allow=["git diff*"]))
    assert rules.matches_command("allow", "git diff HEAD")
    assert not rules.matches_command("allow", "GIT diff HEAD")


def test_digest_ignores_order():
    a = ClientRules.of(rule_set(allow=["b", "a"]))
    b = ClientRules.of(rule_set(allow=["a", "b"]))
    assert a.digest() == b.digest()


def test_digest_ignores_level():
    a = ClientRules.of(rule_set(allow=["a"], level="low"))
    b = ClientRules.of(rule_set(allow=["a"], level="high"))
    assert a.digest() == b.digest()


def test_digest_is_sensitive_to_patterns():
    a = ClientRules.of(rule_set(allow=["a", "b"]))
    c = ClientRules.of(rule_set(allow=["a", "c"]))
    assert a.digest() != c.digest()


def test_digest_dedupes_patterns():
    a = ClientRules.of(rule_set(allow=["a", "a"]))
    b = ClientRules.of(rule_set(allow=["a"]))
    assert a.digest() == b.digest()


def test_digest_does_not_depend_on_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/first")
    first = ClientRules.of(rule_set(deny=["~/.ssh/**"]))
    monkeypatch.setenv("HOME", "/home/second")
    second = ClientRules.of(rule_set(deny=["~/.ssh/**"]))
    assert first.digest() == second.digest()


def test_digest_length():
    assert len(ClientRules.of(rule_set()).digest()) == 64
