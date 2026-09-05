import pytest
from pydantic import ValidationError

from agentgate.profiles.schema import DenyWindow, History, Inspect, NetworkMode, PerTurnChars, Profile
from tests.factories import minimal_profile_data, profile


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


def test_history_budget_has_the_spec_defaults():
    h = History()
    assert h.budget_chars == 12000
    assert (h.cap_for("human"), h.cap_for("assistant"), h.cap_for("toolcall"), h.cap_for("toolresult")) == (
        2048, 1500, 1000, 1500,
    )


def test_history_budget_is_read_from_the_profile_and_enters_the_hash():
    plain = profile()
    tuned = profile(history={"budget_chars": 100, "per_turn_chars": {"toolresult": 10}})
    assert tuned.history.budget_chars == 100 and tuned.history.cap_for("toolresult") == 10
    assert tuned.history.cap_for("human") == 2048
    assert tuned.profile_hash() != plain.profile_hash()


def test_history_budget_rejects_zero():
    with pytest.raises(ValidationError):
        profile(history={"budget_chars": 0})


def test_per_turn_caps_cover_exactly_the_turn_roles():
    from agentgate.api.schemas import TurnRole

    assert {role.value for role in TurnRole} == set(PerTurnChars.model_fields)


def test_cap_for_rejects_an_unknown_role():
    with pytest.raises(KeyError):
        History().cap_for("wizard")


def test_cap_for_answers_for_every_turn_role():
    from agentgate.api.schemas import TurnRole

    assert all(isinstance(History().cap_for(role.value), int) for role in TurnRole)


def test_inspect_classifier_defaults_to_off():
    p = Profile.model_validate(minimal_profile_data())
    assert p.inspect.classifier == "off"


def test_inspect_classifier_accepts_on_flag():
    assert Inspect(classifier="on-flag").classifier == "on-flag"
