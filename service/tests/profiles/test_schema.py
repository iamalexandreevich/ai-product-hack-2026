import pytest

from agentgate.profiles.schema import DenyWindow, NetworkMode, Profile
from tests.factories import minimal_profile_data


def test_minimal_profile_defaults():
    p = Profile.model_validate(minimal_profile_data())
    assert p.escalation.deny_consecutive == 3
    assert p.escalation.deny_window.of_last == 50
    assert p.network.mode is NetworkMode.allowlist
    assert p.models.model_config_for(None)[0] == "m"
    assert p.models.model_config_for("m")[1].timeout_ms == 3000
    with pytest.raises(KeyError):
        p.models.model_config_for("nope")


def test_default_model_must_exist():
    bad = minimal_profile_data(models={"default": "zzz", "configs": minimal_profile_data()["models"]["configs"]})
    with pytest.raises(ValueError):
        Profile.model_validate(bad)


def test_deny_window_of_last_zero_rejected():
    # of_last=0 makes list[-0:] the WHOLE list in Python, silently defeating
    # an operator's attempt to disable the window check.
    with pytest.raises(ValueError):
        DenyWindow(of_last=0)


def test_deny_window_count_zero_rejected():
    # count=0 makes window.count("deny") >= 0 trivially true: escalates on everything.
    with pytest.raises(ValueError):
        DenyWindow(count=0)


def test_deny_window_count_greater_than_of_last_rejected():
    # count > of_last: the window can never hold `count` denials, so escalation
    # never fires. Fails open, silently, which is worse than the of_last=0 bug.
    with pytest.raises(ValueError):
        DenyWindow(count=5, of_last=3)


def test_profile_hash_is_a_sha256_hex_digest():
    assert len(Profile.model_validate(minimal_profile_data()).profile_hash()) == 64


def test_profile_hash_is_stable_across_calls():
    p = Profile.model_validate(minimal_profile_data())
    assert p.profile_hash() == p.profile_hash()
