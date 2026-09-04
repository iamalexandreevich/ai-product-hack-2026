import dataclasses

import pytest

from agentgate.domain.policy import Policy
from tests.factories import profile


def test_bind_expands_the_workspace_placeholder():
    policy = Policy.bind(profile(allowed_paths=["${WORKSPACE}"]), "/home/u/repo")
    assert policy.allowed_paths == ("/home/u/repo",)


def test_bind_expands_a_tilde_in_protected_paths():
    policy = Policy.bind(profile(protected_paths=["~/.ssh/**"]), "/home/u/repo")
    assert policy.protected_paths[0].startswith("/") and "~" not in policy.protected_paths[0]


def test_resolved_paths_are_immutable():
    policy = Policy.bind(profile(), "/home/u/repo")
    assert isinstance(policy.allowed_paths, tuple)


def test_policy_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Policy.bind(profile(), "/home/u/repo").workspace = "/elsewhere"


def test_hash_does_not_depend_on_the_workspace():
    first = Policy.bind(profile(), "/home/u/repo")
    second = Policy.bind(profile(), "/tmp/other")
    assert first.profile_hash == second.profile_hash


def test_hash_changes_when_the_profile_changes():
    first = Policy.bind(profile(), "/w")
    second = Policy.bind(profile(protected_paths=[".env*", "*.pem"]), "/w")
    assert first.profile_hash != second.profile_hash


def test_network_is_reachable_without_reaching_into_the_profile():
    assert Policy.bind(profile(), "/w").network.allowed_domains == ["pypi.org"]
